"""The LLM, served by Ollama, with ``inference`` as its adapter (LLM-1.1, ADR-0021).

Ollama keeps its model in VRAM outside PyTorch and knows nothing of the GPU lease
(ADR-0006 rule 2). This module is the adapter that makes it follow the rules:

* **Its model comes from the allow-list's verified file** (rule 2). The GGUF file
  ``models pull`` verified is uploaded to Ollama under its pinned SHA-256, which
  Ollama checks, and the model is created from that blob. Ollama never pulls.
* **The lease before a load** (rule 4). A request takes the lease through the model
  holder, which unloads a ``/predict`` model first; free VRAM is checked on the card
  before loading, and after it ``/api/ps`` must show the whole model in VRAM.
* **The GPU is given back** (rule 5) by the holder's background check, which calls
  :meth:`LlmAdapter.unload`: ``keep_alive: 0``, then the card's used memory is polled
  until it is back near the baseline measured before the load.
* **One answer at a time** (rule 8): one generates, up to ``llm_max_waiting`` wait,
  and any more are refused with ``llm_busy``.

Nothing here imports PyTorch: Ollama runs the model, and the card is read with NVML.
"""

import contextlib
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from stratal.core.errors import LlmBusy, LlmUnavailable, VramInsufficient
from stratal.core.logging import get_logger
from stratal.core.settings import Settings
from stratal.mon.sink import EventSink, LogSink
from stratal.pretrained.catalog import get_model, local_path

log = get_logger("stratal.inference.llm")
Json = dict[str, Any]


# ---------------------------------------------------------------------------- Ollama's API


class ChatStream(Protocol):
    def __iter__(self) -> Iterator[Json]: ...
    def close(self) -> None: ...


class OllamaApi(Protocol):
    """The part of Ollama's HTTP API the adapter uses."""

    def models(self) -> list[str]: ...
    def has_blob(self, digest: str) -> bool: ...
    def push_blob(self, digest: str, path: Path) -> None: ...
    def create(self, name: str, filename: str, digest: str, num_ctx: int) -> None: ...
    def load(self, name: str) -> Json: ...
    def ps(self) -> list[Json]: ...
    def unload(self, name: str) -> None: ...
    def chat(self, body: Json) -> ChatStream: ...


class _LineStream:
    """Ollama's streamed answer, one JSON object per line. Closing it closes the
    connection, which makes Ollama stop generating."""

    def __init__(self, response: Any) -> None:
        self._response = response

    def __iter__(self) -> Iterator[Json]:
        for line in self._response:
            text = line.strip()
            if text:
                yield json.loads(text)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._response.close()


class OllamaHttp:
    """:class:`OllamaApi` over HTTP with the standard library (no new dependency)."""

    def __init__(self, url: str, timeout_s: float) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout_s

    def _open(
        self, method: str, path: str, body: Any = None, *, timeout: float | None = None
    ) -> Any:
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        request = urllib.request.Request(
            self._url + path, data=data, method=method, headers=headers
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout or self._timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "HEAD":
                raise
            detail = exc.read().decode(errors="replace")[:300]
            raise LlmUnavailable(
                f"Ollama refused {method} {path}: {exc.code} {detail}",
                suggested_action="Check the ollama service's log (docker compose logs ollama).",
            ) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise LlmUnavailable(
                f"The LLM service at {self._url} did not answer ({exc}).",
                suggested_action="Start it: docker compose --profile gpu up -d ollama",
            ) from exc

    def _json(
        self, method: str, path: str, body: Any = None, *, timeout: float | None = None
    ) -> Json:
        with self._open(method, path, body, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}

    def models(self) -> list[str]:
        return [str(m["name"]) for m in self._json("GET", "/api/tags").get("models", [])]

    def has_blob(self, digest: str) -> bool:
        try:
            with self._open("HEAD", f"/api/blobs/{digest}"):
                return True
        except urllib.error.HTTPError:
            return False

    def push_blob(self, digest: str, path: Path) -> None:
        with path.open("rb") as handle:
            request = urllib.request.Request(
                f"{self._url}/api/blobs/{digest}",
                data=handle,
                method="POST",
                headers={"Content-Length": str(path.stat().st_size)},
            )
            try:
                with urllib.request.urlopen(request, timeout=600):
                    pass
            except urllib.error.HTTPError as exc:
                raise LlmUnavailable(
                    f"Ollama refused the model file {path.name}: {exc.code} "
                    f"{exc.read().decode(errors='replace')[:300]}",
                    suggested_action="Pull the model again (models pull); Ollama checks the hash.",
                ) from exc

    def create(self, name: str, filename: str, digest: str, num_ctx: int) -> None:
        body = {
            "model": name,
            "files": {filename: digest},
            "parameters": {"num_ctx": num_ctx},
            "stream": False,
        }
        self._json("POST", "/api/create", body, timeout=600)

    def load(self, name: str) -> Json:
        return self._json("POST", "/api/generate", {"model": name, "keep_alive": -1}, timeout=300)

    def ps(self) -> list[Json]:
        return list(self._json("GET", "/api/ps").get("models", []))

    def unload(self, name: str) -> None:
        self._json("POST", "/api/generate", {"model": name, "keep_alive": 0})

    def chat(self, body: Json) -> ChatStream:
        return _LineStream(self._open("POST", "/api/chat", body))


# ---------------------------------------------------------------------------- the card


class Card(Protocol):
    """The GPU's memory as the driver sees it (NVML), in MB."""

    def used_mb(self) -> float | None: ...
    def total_mb(self) -> float | None: ...


class NvmlCard:
    def __init__(self) -> None:
        from stratal.mon.probes import GpuProbe

        self._probe = GpuProbe()

    def used_mb(self) -> float | None:
        reading = self._probe.read()
        return None if reading is None else reading.vram_used_mb

    def total_mb(self) -> float | None:
        reading = self._probe.read()
        return None if reading is None else reading.vram_total_mb


class GpuOwner(Protocol):
    """What the adapter needs from the model holder (ADR-0021 rule 3)."""

    def claim_gpu_for_llm(self) -> None:
        """Take the lease and unload a ``/predict`` model from the GPU, or raise
        ``gpu_busy`` naming the job that holds it."""

    def note_gpu_use(self) -> None: ...


# ---------------------------------------------------------------------------- the adapter


@dataclass
class Generation:
    """One answer, prepared: the lease held, the model loaded. :meth:`events` streams it."""

    adapter: "LlmAdapter"
    body: Json
    meta: Json
    started: float
    _closed: bool = False
    _stream: ChatStream | None = field(default=None, repr=False)

    def events(self) -> Iterator[Json]:
        """``meta``, then ``token`` events, then ``done`` with the timings (rule 8). Closing
        the iterator early (the client went away) stops Ollama's generation."""
        first: float | None = None
        final: Json = {}
        try:
            yield {"event": "meta", **self.meta}
            self._stream = self.adapter.api.chat(self.body)
            for part in self._stream:
                message: Json = part.get("message") or {}
                content = str(message.get("content") or "")
                if content:
                    if first is None:
                        first = time.monotonic()
                    yield {"event": "token", "text": content}
                if part.get("done"):
                    final = part
            yield {"event": "done", **_timings(self.started, first, final)}
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stream is not None:
            self._stream.close()
        self.adapter.finish()


def _timings(started: float, first: float | None, final: Json) -> Json:
    eval_s = float(final.get("eval_duration") or 0) / 1e9
    tokens = int(final.get("eval_count") or 0)
    return {
        "ttft_ms": None if first is None else round(1000 * (first - started), 1),
        "total_ms": round(1000 * (time.monotonic() - started), 1),
        "prompt_tokens": final.get("prompt_eval_count"),
        "output_tokens": tokens,
        "tokens_per_s": round(tokens / eval_s, 1) if eval_s else None,
    }


class LlmAdapter:
    """Serves the allow-listed LLM through Ollama under the GPU lease (ADR-0021)."""

    def __init__(
        self,
        *,
        api: OllamaApi,
        owner: GpuOwner,
        settings: Settings,
        card: Card,
        sink: EventSink | None = None,
    ) -> None:
        self.api = api
        self._owner = owner
        self._settings = settings
        self._card = card
        self._sink: EventSink = sink or LogSink()
        entry = get_model(settings.llm_model)
        self._entry = entry
        self._file = entry.files[0]
        self.name = f"stratal-{entry.name.removesuffix('-q4km')}:{entry.revision[:12]}"
        self.display = entry.display
        self._state = threading.Lock()
        self._model_lock = threading.Lock()
        self._slot = threading.Semaphore(1)
        self._waiting = 0
        self._busy = 0
        self._ready = False
        self.loaded = False
        self.last_use = 0.0
        self._baseline: float | None = None

    # ------------------------------------------------------------------ status
    @property
    def busy(self) -> bool:
        return self._busy > 0

    def status(self) -> Json:
        return {
            "model": self.name,
            "loaded": self.loaded,
            "busy": self.busy,
            "waiting": self._waiting,
            "ready": self._ready,
        }

    # ------------------------------------------------------------------ the model in Ollama
    def ensure_model(self) -> None:
        """Create Ollama's model from the verified GGUF file once (rule 2)."""
        with self._model_lock:
            if self._ready:
                return
            self._unload_leftovers()
            if self.name in self.api.models():
                self._ready = True
                return
            folder = local_path(self._settings.data_dir / "models", self._entry.name)
            path = folder / self._file.path
            digest = f"sha256:{self._file.sha256}"
            if not self.api.has_blob(digest):
                self._sink.emit(
                    stage="inference",
                    message=f"Copying {self._file.path} ({path.stat().st_size / 2**30:.1f} GB) "
                    "to the LLM service; Ollama checks its SHA-256",
                )
                self.api.push_blob(digest, path)
            self.api.create(self.name, self._file.path, digest, self._settings.llm_num_ctx)
            self._sink.emit(
                stage="inference",
                message=f"Created the LLM {self.name} from the verified file {self._file.path}",
            )
            self._ready = True

    def _unload_leftovers(self) -> None:
        """Unload what Ollama still holds from before this process started (rule 5).

        When ``inference`` restarts and ``ollama`` does not, the LLM stays on the card
        without the GPU lease, and the first load would take that card as its baseline
        (found in the M12 live check, 2026-11-27: "card 1.8 → 1.8 GB"), so the idle unload
        would never bring the card back to where it was."""
        held = [str(m.get("name") or "") for m in self.api.ps()]
        held = [name for name in held if name]
        if not held:
            return
        before = self._card.used_mb()
        for name in held:
            with contextlib.suppress(LlmUnavailable):
                self.api.unload(name)
        deadline = time.monotonic() + self._settings.llm_unload_wait_s
        while self.api.ps() and time.monotonic() < deadline:
            time.sleep(0.2)
        after = self._card.used_mb()
        self._sink.emit(
            stage="inference",
            message=f"Unloaded {', '.join(held)}, left on the GPU from before inference "
            f"started: {_card_text(before, after)}",
            metrics=_card_metrics(before, after),
            event_type="model_unloaded",
        )

    # ------------------------------------------------------------------ one answer
    def start(
        self, messages: Sequence[Json], *, route: str = "llm", extra: Json | None = None
    ) -> Generation:
        """Queue, take the GPU, load the model if needed; then the answer streams from
        :meth:`Generation.events`. Raises before anything is streamed, so the API can
        answer with a problem document."""
        with self._state:
            if self._waiting >= self._settings.llm_max_waiting:
                raise LlmBusy(
                    f"{self._waiting + 1} answers are ahead of this one.",
                    suggested_action="Try again in a few seconds.",
                )
            self._waiting += 1
        try:
            if not self._slot.acquire(timeout=self._settings.inference_timeout_s):
                raise LlmBusy("Waited too long for the answers ahead of this one.")
        finally:
            with self._state:
                self._waiting -= 1
        try:
            started = time.monotonic()
            self.ensure_model()
            self._owner.claim_gpu_for_llm()
            with self._state:
                cold_ms = None if self.loaded else self._load()
                self._busy += 1
        except BaseException:
            self._slot.release()
            raise
        body: Json = {
            "model": self.name,
            "messages": list(messages),
            "stream": True,
            "think": False,
            "keep_alive": -1,
            "options": {
                "temperature": self._settings.llm_temperature,
                "num_predict": self._settings.llm_max_tokens,
                "num_ctx": self._settings.llm_num_ctx,
            },
        }
        meta: Json = {
            "route": route,
            "model": self._entry.name,
            "revision": self._entry.revision,
            "display": self.display,
            "device": "gpu",
            "cold_start": cold_ms is not None,
            "load_ms": cold_ms,
            **(extra or {}),
        }
        return Generation(adapter=self, body=body, meta=meta, started=started)

    def finish(self) -> None:
        with self._state:
            self._busy = max(0, self._busy - 1)
            self.last_use = time.monotonic()
        self._owner.note_gpu_use()
        self._slot.release()

    # ------------------------------------------------------------------ load and unload
    def _load(self) -> float:
        before = self._card.used_mb()
        total = self._card.total_mb()
        if before is not None and total is not None:
            free = total - before
            if free < self._settings.llm_vram_estimate_mb:
                raise VramInsufficient(
                    f"The LLM needs about {self._settings.llm_vram_estimate_mb / 1024:.1f} GB "
                    f"of VRAM and {free / 1024:.1f} GB is free.",
                    suggested_action="Something else is using the card: check SCR-13.",
                )
        self._sink.emit(
            stage="inference",
            message=f"Loading LLM {self.display} to GPU "
            f"({self._settings.llm_vram_estimate_mb / 1024:.1f} GB with its cache)",
        )
        started = time.monotonic()
        self.api.load(self.name)
        seconds = time.monotonic() - started
        placed = next((m for m in self.api.ps() if m.get("name") == self.name), None)
        if placed is None or int(placed.get("size_vram") or 0) < int(placed.get("size") or 1):
            self.api.unload(self.name)
            raise VramInsufficient(
                "Ollama put part of the LLM on the CPU: there was not enough VRAM for all of it.",
                suggested_action="Free the card (a GPU job or another process) and try again.",
            )
        after = self._card.used_mb()
        self._baseline = before
        self.loaded = True
        self._sink.emit(
            stage="inference",
            message=f"Loaded LLM {self.display} to GPU (cold start {seconds:.1f} s): "
            f"{_card_text(before, after)}; no PyTorch figure, Ollama manages its own memory",
            metrics=_card_metrics(before, after, load_s=round(seconds, 2)),
            event_type="model_loaded",
        )
        return round(1000 * seconds, 1)

    def unload(self, reason: str) -> bool:
        """Unload, then wait for the card to come back to the baseline (rule 5). One more
        try if it does not; ``False`` means the VRAM was not given back."""
        with self._state:
            if not self.loaded:
                return True
            if self._busy:
                return False
            before = self._card.used_mb()
            for attempt in (1, 2):
                with contextlib.suppress(LlmUnavailable):
                    self.api.unload(self.name)
                if self._wait_for_baseline():
                    break
                log.warning("llm_unload_not_back", attempt=attempt)
            else:
                after = self._card.used_mb()
                self._sink.emit(
                    stage="inference",
                    level="ERROR",
                    message=f"Unloaded LLM {self.display}, but the card did not give the memory "
                    f"back: {_card_text(before, after)}",
                    reason="vram_not_released: Ollama still holds VRAM after two unloads.",
                    suggested_action="The lease is kept so no job loads onto a full card. "
                    "Restart the ollama service: docker compose restart ollama",
                    metrics=_card_metrics(before, after),
                    event_type="model_unloaded",
                )
                return False
            after = self._card.used_mb()
            self.loaded = False
            self._sink.emit(
                stage="inference",
                message=f"Unloaded LLM {self.display} ({reason}): {_card_text(before, after)}; "
                "the card is back at its baseline",
                metrics=_card_metrics(before, after),
                event_type="model_unloaded",
            )
            return True

    def _wait_for_baseline(self) -> bool:
        if self._baseline is None:
            return True
        limit = self._baseline + self._settings.llm_baseline_margin_mb
        deadline = time.monotonic() + self._settings.llm_unload_wait_s
        while True:
            used = self._card.used_mb()
            if used is None or used <= limit:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)


def _card_text(before: float | None, after: float | None) -> str:
    if before is None or after is None:
        return "card memory unavailable"
    return f"card {before / 1024:.1f} → {after / 1024:.1f} GB"


def _card_metrics(before: float | None, after: float | None, **more: float) -> dict[str, float]:
    metrics: dict[str, float] = dict(more)
    if before is not None and after is not None:
        metrics["card_used_mb_before"] = round(before, 1)
        metrics["card_used_mb_after"] = round(after, 1)
    return metrics
