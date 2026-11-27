"""LLM-1.1, ADR-0021: the Ollama adapter, with a fake Ollama, card and lease owner.

The real service is checked in ``tests/gpu/test_LLM_1_1_ollama_gpu.py``; the adapter
with the real model holder and lease in ``tests/integration/test_M12_llm_holder.py``.
"""

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from stratal.core.errors import GpuBusy, LlmBusy, VramInsufficient
from stratal.core.settings import Settings
from stratal.inference.llm import LlmAdapter
from stratal.pretrained.catalog import STAMP, get_model, model_dir
from stratal.testing.fake_llm import FakeCard, FakeOllama
from stratal.testing.fakes import ListSink

ENTRY = get_model("qwen3-1.7b-q4km")
DIGEST = f"sha256:{ENTRY.files[0].sha256}"


class FakeOwner:
    def __init__(self) -> None:
        self.claims = 0
        self.busy_with: str | None = None

    def claim_gpu_for_llm(self) -> None:
        if self.busy_with:
            raise GpuBusy(f"The GPU is in use by {self.busy_with}.")
        self.claims += 1

    def note_gpu_use(self) -> None:
        pass


@pytest.fixture
def setup(tmp_path: Path) -> tuple[LlmAdapter, FakeOllama, FakeCard, FakeOwner, ListSink]:
    folder = model_dir(tmp_path / "models", ENTRY)
    folder.mkdir(parents=True)
    (folder / ENTRY.files[0].path).write_bytes(b"gguf")
    (folder / STAMP).write_text("{}", encoding="utf-8")
    api, card, owner, sink = FakeOllama(), FakeCard(), FakeOwner(), ListSink()
    api.card = card
    settings = Settings(data_dir=tmp_path, llm_unload_wait_s=0.3, inference_timeout_s=2.0)
    adapter = LlmAdapter(api=api, owner=owner, settings=settings, card=card, sink=sink)
    return adapter, api, card, owner, sink


def _answer(adapter: LlmAdapter) -> list[dict[str, Any]]:
    generation = adapter.start([{"role": "user", "content": "Hi"}])
    return list(generation.events())


def test_LLM_1_1_model_is_built_from_the_pinned_blob(setup: Any) -> None:
    adapter, api, *_ = setup
    adapter.ensure_model()
    assert api.calls[0] == ("push", (DIGEST, ENTRY.files[0].path))
    name, filename, digest, num_ctx = api.calls[1][1]
    assert (filename, digest, num_ctx) == (ENTRY.files[0].path, DIGEST, 4096)
    assert name == f"stratal-qwen3-1.7b:{ENTRY.revision[:12]}"
    api.calls.clear()
    adapter._ready = False  # a restarted service finds the model already there
    adapter.ensure_model()
    assert api.calls == []


def test_LLM_1_1_an_answer_streams_meta_tokens_and_timings(setup: Any) -> None:
    adapter, api, _, owner, sink = setup
    events = _answer(adapter)
    assert [e["event"] for e in events] == ["meta", "token", "token", "done"]
    assert events[0]["cold_start"] is True and events[0]["device"] == "gpu"
    assert events[0]["model"] == "qwen3-1.7b-q4km"
    assert "".join(e["text"] for e in events if e["event"] == "token") == "Hello there."
    done = events[-1]
    assert done["output_tokens"] == 3 and done["tokens_per_s"] == 100.0
    assert done["ttft_ms"] is not None and done["prompt_tokens"] == 12
    body = next(c[1] for c in api.calls if c[0] == "chat")
    assert body["think"] is False and body["keep_alive"] == -1
    assert body["options"]["num_predict"] == 512 and body["options"]["num_ctx"] == 4096
    assert owner.claims == 1
    loaded = [e for e in sink.events if e.get("type") == "model_loaded"]
    assert loaded and "no PyTorch figure" in loaded[0]["message"]
    assert loaded[0]["metrics"]["card_used_mb_after"] == 1957.0
    assert _answer(adapter)[0]["cold_start"] is False  # loaded once


def test_LLM_1_1_gpu_busy_before_anything_streams(setup: Any) -> None:
    adapter, api, _, owner, _ = setup
    owner.busy_with = "job_0131"
    with pytest.raises(GpuBusy, match="job_0131"):
        adapter.start([{"role": "user", "content": "Hi"}])
    assert not any(c[0] in ("load", "chat") for c in api.calls)
    owner.busy_with = None
    assert _answer(adapter)[-1]["event"] == "done"  # the slot was given back


def test_LLM_1_1_refused_when_vram_is_short(setup: Any) -> None:
    adapter, api, card, *_ = setup
    card.used = 4800.0
    with pytest.raises(VramInsufficient, match="free"):
        adapter.start([{"role": "user", "content": "Hi"}])
    assert not any(c[0] == "load" for c in api.calls)


def test_LLM_1_1_a_partial_offload_is_unloaded_and_refused(setup: Any) -> None:
    adapter, api, *_ = setup
    api.offload_all = False
    with pytest.raises(VramInsufficient, match="CPU"):
        adapter.start([{"role": "user", "content": "Hi"}])
    assert ("unload", adapter.name) in api.calls
    assert adapter.loaded is False


def test_LLM_1_1_stopping_closes_the_stream(setup: Any) -> None:
    adapter, api, *_ = setup
    generation = adapter.start([{"role": "user", "content": "Hi"}])
    events = generation.events()
    assert next(events)["event"] == "meta"
    assert next(events)["event"] == "token"
    events.close()  # the client went away
    assert api.streams[-1].closed
    assert adapter.busy is False
    assert _answer(adapter)[-1]["event"] == "done"


def test_LLM_1_1_the_queue_is_bounded(setup: Any) -> None:
    adapter, api, *_ = setup
    api.gate = threading.Event()
    first = adapter.start([{"role": "user", "content": "Hi"}])
    events = first.events()
    next(events)  # meta: the first answer holds the slot
    waiting: list[threading.Thread] = []
    for _ in range(4):
        thread = threading.Thread(target=lambda: _answer(adapter), daemon=True)
        thread.start()
        waiting.append(thread)
    deadline = time.monotonic() + 2
    while adapter.status()["waiting"] < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(LlmBusy):
        adapter.start([{"role": "user", "content": "Hi"}])
    api.gate.set()
    list(events)
    for thread in waiting:
        thread.join(5)


def test_LLM_1_1_unload_waits_for_the_card_and_reports(setup: Any) -> None:
    adapter, api, card, _, sink = setup
    _answer(adapter)
    assert adapter.unload("idle") is True
    unloaded = [e for e in sink.events if e.get("type") == "model_unloaded"]
    assert "back at its baseline" in unloaded[-1]["message"]
    _answer(adapter)
    card.stuck = True
    assert adapter.unload("idle") is False
    assert [c[0] for c in api.calls].count("unload") == 3  # one, then two tries
    error = [e for e in sink.events if e.get("level") == "ERROR"][-1]
    assert "vram_not_released" in str(error["reason"])
    assert adapter.loaded is True
