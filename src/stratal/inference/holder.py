# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU and ML images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""The one model the inference service holds, and where it runs (ADR-0016 rules 2 to 6).

- **One model at a time.** A request for another version unloads the current one
  first; a GPU unload goes through the VRAM manager, which proves memory is back
  at its baseline. If it is not, the process exits and Compose restarts it.
- **GPU or CPU per request (decision 2).** A model on the GPU is used there. Else
  the lease is tried once, without waiting; if a job holds it, a model under
  ``inference_cpu_max_parameters`` runs on the CPU and a larger one is refused
  with ``gpu_busy`` naming the holder. Every result says which device served it.
- **The GPU is given back** after ``inference_idle_release_s`` without a GPU
  request, and at once when a job is waiting for the lease (``stratal:gpu:wanted``).
- **It reports what it holds** in ``stratal:inference:loaded``, set before the
  weights are read, and reads them under the lineage's lock, so retention never
  deletes a version while it is being loaded or served (REG-2.8).
- **Only with a working ``TrainingState.close()`` (RUN-1.4)** is the GPU used: it is
  tried once on a tiny CPU model, and without it no GPU unload could be proven. Then
  small models are served on the CPU, larger ones are refused, and the reason says
  why (the owner's choice, 2026-11-18). ``close()`` exists since 2026-11-19.

Weights are read from the registry's safetensors with the model rebuilt from its
spec; nothing is unpickled (NFR-8).

Since M10 (ADR-0019 rules 20 and 21) it also serves tables and texts: a classical
model's skops pipeline (on the CPU, never taking the lease; it counts as the one
model held), and table and text networks, whose input is encoded with the
version's own ``preprocess.json`` or its dataset version's vocabulary.
"""

import base64
import binascii
import contextlib
import gc
import io
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import torch
from PIL import Image, UnidentifiedImageError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from torch import nn

from stratal.build.shapes import Shape
from stratal.core.bus import Bus
from stratal.core.errors import (
    Conflict,
    CudaUnavailable,
    GpuBusy,
    NotFound,
    ValidationFailed,
    WeightsDeleted,
    WrongInput,
)
from stratal.core.logging import get_logger
from stratal.core.settings import Settings
from stratal.data import registry
from stratal.data.records import META_TABLE_SCHEMA
from stratal.data.tokenize import Vocabulary, vocab_of
from stratal.inference import inputs
from stratal.label.models import GpuBackend, ModelInfo
from stratal.label.vram import VramManager
from stratal.mon.events import utc_now
from stratal.mon.sink import EventSink, LogSink
from stratal.registry import store
from stratal.runtime.gpu_lease import HeldLease, current_holder
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import read_metadata
from stratal.train.config import ModelSpec
from stratal.train.data import to_tensor
from stratal.train.memory import TrainingState
from stratal.train.reference import build_model
from stratal.train.table_prep import PREPROCESS_FILE, TablePrep
from stratal.train.weights import read_tensors

log = get_logger(__name__)

Device = Literal["cpu", "gpu"]
VRAM_FRACTION = 0.85
"""The per-process cap (ADR-0006 rule 8)."""
MB = 2**20
TOP_K = 5


class GpuSide(Protocol):
    """The card as the holder uses it. :class:`TorchGpuSide` on the real card; tests
    pass a fake that counts memory without CUDA."""

    tensor_device: str

    def available(self) -> bool: ...
    def backend(self) -> GpuBackend: ...
    def prepare(self) -> None: ...
    def to_device(self, model: nn.Module) -> nn.Module: ...


class TorchGpuSide:
    tensor_device = "cuda"

    def __init__(self) -> None:
        self._prepared = False

    def available(self) -> bool:
        return torch.cuda.is_available()

    def backend(self) -> GpuBackend:
        from stratal.label.hf import TorchGpu

        return TorchGpu()

    def prepare(self) -> None:
        if not self._prepared:  # before the first allocation (ADR-0006 rule 8)
            torch.cuda.set_per_process_memory_fraction(VRAM_FRACTION)
            self._prepared = True

    def to_device(self, model: nn.Module) -> nn.Module:
        return model.to("cuda")


def close_written(state_cls: type[TrainingState] = TrainingState) -> bool:
    """Whether ``TrainingState.close`` (RUN-1.4) works: tried on a tiny CPU
    model with no optimizer, as inference uses it (its contract's rule 6)."""
    probe = nn.Linear(1, 1)
    try:
        state_cls(probe, None, info=ModelInfo("probe", "probe", "probe", "fp32")).close()
    except NotImplementedError:
        return False
    except Exception as exc:  # written, but not for a model without an optimizer
        log.warning("training_state_close_failed", reason=str(exc))
        return False
    return True


def _exit_for_restart() -> None:
    """A GPU unload left memory behind (BUG5): exit, so Compose restarts us (rule 3)."""
    log.error("inference_unload_leaked", action="exiting so the container restarts")
    os._exit(3)


@dataclass
class _Loaded:
    version_id: str
    lineage_id: str
    name: str
    device: Device
    classes: list[str]
    shape: tuple[int, int, int]
    parameters: int
    model: nn.Module | None = None
    """The CPU model; a GPU model is held only by its TrainingState in the VRAM manager."""
    state: TrainingState | None = None
    since: str = field(default_factory=lambda: utc_now().isoformat())
    task: str = "classification"
    prep: TablePrep | None = None
    """A table network's fitted encoding (ADR-0019 rule 13)."""
    vocab: Vocabulary | None = None
    length: int = 0
    """A text network's vocabulary and L (ADR-0019 rule 11)."""
    classical: inputs.ClassicalModel | None = None
    """A classical model's pipeline, always on the CPU (ADR-0019 rule 20)."""


def decode_image(*, image_b64: str | None, blob: str | None, data_dir: Path) -> Image.Image:
    """The request's image: uploaded bytes, or a blob of the data folder by its SHA-256."""
    if (image_b64 is None) == (blob is None):
        raise ValidationFailed(
            "Send exactly one of image_b64 and blob.",
            errors=[{"loc": ["body"], "msg": "one of image_b64, blob", "code": "invalid"}],
        )
    if blob is not None:
        blobs = BlobStore(data_dir)
        if not blobs.exists(blob):
            raise NotFound(f"No blob {blob}.")
        source: Any = blobs.path_for(blob)
    else:
        try:
            source = io.BytesIO(base64.b64decode(str(image_b64), validate=True))
        except (binascii.Error, ValueError):
            raise ValidationFailed(
                "image_b64 is not valid base64.",
                errors=[{"loc": ["body", "image_b64"], "msg": "not base64", "code": "invalid"}],
            ) from None
    try:
        with Image.open(source) as image:
            image.load()
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise ValidationFailed(
            "The image could not be decoded.",
            suggested_action="Send a JPEG, PNG or WebP image.",
        ) from None


def prepare_input(image: Image.Image, shape: tuple[int, int, int]) -> torch.Tensor:
    """Resized to the model's input as the preprocess stage resizes (LANCZOS), and
    normalised as the training data is (``to_tensor``)."""
    _, height, width = shape
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return to_tensor(image).unsqueeze(0)


class ModelHolder:
    """Holds at most one model and serves predictions from it. Thread-safe: requests and
    the background check take one lock, so a release waits for a request in flight."""

    def __init__(
        self,
        *,
        engine: Engine,
        settings: Settings,
        bus: Bus,
        gpu: GpuSide | None = None,
        state_cls: type[TrainingState] = TrainingState,
        on_leak: Callable[[], None] = _exit_for_restart,
        sink: EventSink | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._bus = bus
        self._gpu: GpuSide = gpu or TorchGpuSide()
        self._state_cls = state_cls
        self._on_leak = on_leak
        self._sink: EventSink = sink or LogSink()
        self._lock = threading.RLock()
        self._lease = HeldLease(engine, holder="inference")
        self._loaded: _Loaded | None = None
        self._manager: VramManager | None = None
        self._last_gpu_use = 0.0
        self._close_written: bool | None = None

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        with self._lock:
            loaded = self._loaded
            return {
                "loaded": None
                if loaded is None
                else {
                    "version_id": loaded.version_id,
                    "device": loaded.device,
                    "since": loaded.since,
                },
                "lease_held": self._lease.held,
                "gpu_unavailable": self._gpu_unavailable(),
            }

    def _gpu_unavailable(self) -> str | None:
        """Why the GPU cannot be used for inference at all, or ``None``."""
        if not self._gpu.available():
            return "this container has no CUDA device"
        if self._close_written is None:
            self._close_written = close_written(self._state_cls)
        if not self._close_written:
            return (
                "inference does not use the GPU until RUN-1.4 (TrainingState.close) is written, "
                "since a GPU unload could not be proven"
            )
        return None

    # ------------------------------------------------------------------ predict
    def predict(self, version_id: str, image: Image.Image) -> dict[str, Any]:
        with self._lock:
            started = time.monotonic()
            version, _ = self._check(version_id)
            self._refuse_input(version_id, self._meta(version), "image")
            loaded, fallback, load_ms = self._place(version_id)
            x = prepare_input(image, loaded.shape)
            model = loaded.model if loaded.device == "cpu" else self._gpu_model()
            assert model is not None
            device = "cpu" if loaded.device == "cpu" else self._gpu.tensor_device
            with torch.no_grad():
                logits = model(x.to(device))
                probabilities = torch.softmax(logits.float(), dim=1)[0].cpu()
            del model
            if loaded.device == "gpu":
                self._last_gpu_use = time.monotonic()
            k = min(TOP_K, len(loaded.classes))
            top = torch.topk(probabilities, k)
            return {
                "version_id": loaded.version_id,
                "lineage_id": loaded.lineage_id,
                "device": loaded.device,
                "fallback_reason": fallback,
                "top": [
                    {"class": loaded.classes[int(i)], "probability": round(float(p), 6)}
                    for p, i in zip(top.values, top.indices, strict=True)
                ],
                "load_ms": load_ms,
                "total_ms": round(1000 * (time.monotonic() - started), 1),
            }

    def predict_values(
        self, version_id: str, *, record: dict[str, Any] | None = None, text: str | None = None
    ) -> dict[str, Any]:
        """A prediction from a record (tables) or a text (ADR-0019 rules 20 and 21)."""
        with self._lock:
            started = time.monotonic()
            version, lineage = self._check(version_id)
            meta = self._meta(version)
            self._refuse_input(version_id, meta, "record" if record is not None else "text")
            values = (
                inputs.check_record(record, self._table_schema(meta)) if record is not None else {}
            )
            if meta.get("kind") == "sklearn":
                loaded, load_ms = self._place_classical(version, lineage)
                assert loaded.classical is not None
                result = loaded.classical.predict(values)
                fallback: str | None = None
            else:
                loaded, fallback, load_ms = self._place(version_id)
                if record is not None:
                    assert loaded.prep is not None
                    x = inputs.table_tensor(values, loaded.prep)
                else:
                    assert loaded.vocab is not None and text is not None
                    x = inputs.text_tensor(text, loaded.vocab, loaded.length)
                model = loaded.model if loaded.device == "cpu" else self._gpu_model()
                assert model is not None
                device = "cpu" if loaded.device == "cpu" else self._gpu.tensor_device
                with torch.no_grad():
                    output = model(x.to(device)).cpu()
                del model
                if loaded.device == "gpu":
                    self._last_gpu_use = time.monotonic()
                result = inputs.network_result(output, loaded.task, loaded.classes, loaded.prep)
                if text is not None and loaded.vocab is not None:
                    result["input"] = inputs.text_facts(text, loaded.vocab, loaded.length)
            return {
                **result,
                "version_id": loaded.version_id,
                "lineage_id": loaded.lineage_id,
                "device": loaded.device,
                "fallback_reason": fallback,
                "load_ms": load_ms,
                "total_ms": round(1000 * (time.monotonic() - started), 1),
            }

    def _place_classical(self, version: Any, lineage: Any) -> tuple[_Loaded, float | None]:
        """A classical model on the CPU: it never takes the lease (ADR-0019 rule 20)."""
        loaded = self._loaded
        if loaded is not None and loaded.version_id == version.id:
            return loaded, None
        from stratal.ml import persist  # scikit-learn and skops load only for this

        started = time.monotonic()
        self._unload()
        since = utc_now().isoformat()
        self._announce({"version_id": version.id, "device": "cpu", "since": since})
        try:
            with Session(self._engine) as session, session.begin():
                store.lock_lineage(session, lineage.id)
                current = store.get_version(session, version.id)
                if current.weights_status != "present" or current.weights_path is None:
                    raise WeightsDeleted(
                        f"The model of {version.id} was deleted by retention as it was loaded.",
                        suggested_action="Re-train it, then predict with the new version.",
                    )
                folder = self._settings.data_dir / current.weights_path
                meta = json.loads((folder / store.META_FILE).read_text(encoding="utf-8"))
                pipeline = persist.load(folder / persist.MODEL_FILE)
        except BaseException:
            self._announce(None)
            raise
        self._loaded = _Loaded(
            version_id=version.id,
            lineage_id=lineage.id,
            name=f"{lineage.name} {version.id}",
            device="cpu",
            classes=list(meta.get("classes") or []),
            shape=(0, 0, 0),
            parameters=0,
            since=since,
            task=str(meta["task"]),
            classical=inputs.ClassicalModel(pipeline, meta),
        )
        self._sink.emit(
            stage="inference",
            message=f"Loaded {self._loaded.name} (skops) on the CPU",
            metrics={"version_id": version.id},
        )
        return self._loaded, round(1000 * (time.monotonic() - started), 1)

    def _gpu_model(self) -> nn.Module:
        assert self._manager is not None and self._loaded is not None
        assert self._loaded.state is not None
        return self._loaded.state.model

    def _meta(self, version: Any) -> dict[str, Any]:
        folder = self._settings.data_dir / str(version.weights_path)
        return dict(json.loads((folder / store.META_FILE).read_text(encoding="utf-8")))

    @staticmethod
    def _takes(meta: dict[str, Any]) -> str:
        """What a version predicts from: ``image``, ``record`` or ``text``."""
        if meta.get("kind") == "sklearn":
            return "record"
        kind = (meta.get("input") or {}).get("kind", "image")
        return {"vector": "record", "tokens": "text"}.get(kind, "image")

    def _refuse_input(self, version_id: str, meta: dict[str, Any], given: str) -> None:
        wanted = self._takes(meta)
        if wanted != given:
            field_name = {"image": "image_b64 or blob", "record": "record", "text": "text"}[wanted]
            raise WrongInput(
                f"{version_id} predicts from a {wanted}, not a {given}.",
                suggested_action=f"Send {field_name} for this version (ADR-0019 rule 21).",
                errors=[{"loc": ["body"], "msg": f"send {field_name}", "code": "wrong_input"}],
            )

    def check_input(self, version_id: str, given: str) -> None:
        """Refuse an input the version does not take, before anything is read or loaded.
        A version that cannot be served at all (deleted, partial) is left to ``predict``,
        which refuses it after checking the input as before M10."""
        try:
            version, _ = self._check(version_id)
        except (WeightsDeleted, Conflict):
            return
        self._refuse_input(version_id, self._meta(version), given)

    def _check(self, version_id: str) -> tuple[Any, Any]:
        """The version and its lineage, refusing what cannot be served."""
        with Session(self._engine) as session:
            version = store.get_version(session, version_id)
            lineage = store.get_lineage(session, version.lineage_id)
            session.expunge_all()
        if version.state == "partial":
            raise Conflict(
                f"{version_id} is partial: its weights are its paused run's checkpoint.",
                suggested_action="Resume the run, then predict with the trained version.",
            )
        if version.weights_status != "present" or version.weights_path is None:
            raise WeightsDeleted(
                f"The weights of {version_id} were deleted by retention; its metadata remains.",
                suggested_action="Re-train it (POST /model-versions/{id}/retrain), then "
                "predict with the new version.",
            )
        return version, lineage

    def _place(self, version_id: str) -> tuple[_Loaded, str | None, float | None]:
        """Load ``version_id`` where decision 2 says, unless it is already there."""
        loaded = self._loaded
        if loaded is not None and loaded.version_id == version_id and loaded.device == "gpu":
            return loaded, None, None
        version, lineage = self._check(version_id)
        unavailable = self._gpu_unavailable()
        if unavailable is None and self._lease.try_acquire():
            started = time.monotonic()
            self._unload()
            try:
                self._loaded = self._load(version, lineage, "gpu")
            except BaseException:
                self._lease.release()  # nothing is on the card: give it back at once
                raise
            return self._loaded, None, round(1000 * (time.monotonic() - started), 1)
        reason = unavailable or f"{current_holder(self._engine) or 'another process'} holds the GPU"
        limit = self._settings.inference_cpu_max_parameters
        if lineage.parameters >= limit:
            error = CudaUnavailable if unavailable and "CUDA" in unavailable else GpuBusy
            raise error(
                f"{lineage.name} has {lineage.parameters / 1e6:.1f} M parameters, at or above "
                f"the {limit / 1e6:g} M that run on the CPU, and {reason}.",
                suggested_action="Wait for the GPU job to finish, or pause it, and try again.",
            )
        if loaded is not None and loaded.version_id == version_id and loaded.device == "cpu":
            return loaded, reason, None
        started = time.monotonic()
        self._unload()
        self._loaded = self._load(version, lineage, "cpu")
        return self._loaded, reason, round(1000 * (time.monotonic() - started), 1)

    # ------------------------------------------------------------------ load / unload
    def _read_weights(self, version: Any, lineage: Any) -> tuple[nn.Module, dict[str, Any]]:
        """Rebuild the model from its spec and load its safetensors, under the lineage's
        lock, after announcing what we hold (so retention keeps it)."""
        with Session(self._engine) as session, session.begin():
            store.lock_lineage(session, lineage.id)
            current = store.get_version(session, version.id)
            if current.weights_status != "present" or current.weights_path is None:
                raise WeightsDeleted(
                    f"The weights of {version.id} were deleted by retention as it was loaded.",
                    suggested_action="Re-train it, then predict with the new version.",
                )
            folder = self._settings.data_dir / current.weights_path
            meta = json.loads((folder / store.META_FILE).read_text(encoding="utf-8"))
            tensors = read_tensors(
                folder / store.WEIGHTS_FILE, suggested_action="Re-train the version (REG-2.7)."
            )
        shape: Any = (
            Shape.from_json(meta["input"]) if meta.get("input") else tuple(meta["input_shape"])
        )
        outputs = 1 if meta.get("task") == "regression" else len(meta["classes"])
        model = build_model(ModelSpec.model_validate(meta["model"]), shape, outputs)
        model.load_state_dict(tensors, strict=True)
        del tensors
        if (folder / PREPROCESS_FILE).exists():
            meta["_prep"] = TablePrep.from_json((folder / PREPROCESS_FILE).read_text("utf-8"))
        if (meta.get("input") or {}).get("kind") == "tokens":
            meta["_vocab"] = self._vocabulary(str(meta["dataset_version"]))
        return model.eval(), meta

    def _table_schema(self, meta: dict[str, Any]) -> dict[str, Any]:
        """The table schema a record is checked against: from ``model.json``, or for a
        network trained before it was recorded there, from its dataset version."""
        if meta.get("table_schema"):
            return dict(meta["table_schema"])
        with Session(self._engine) as session:
            _, manifest = registry.manifest_for(
                session, self._settings.data_dir, str(meta["dataset_version"])
            )
        text = read_metadata(manifest).get(META_TABLE_SCHEMA)
        if text is None:
            raise WrongInput(f"{meta.get('version_id')} has no table to check a record against.")
        return dict(json.loads(text))

    def _vocabulary(self, dataset_version: str) -> tuple[Vocabulary, int]:
        """A text network's vocabulary and L, from its dataset version (ADR-0019 rule 11)."""
        with Session(self._engine) as session:
            _, manifest = registry.manifest_for(session, self._settings.data_dir, dataset_version)
        found = vocab_of(manifest)
        if found is None:
            raise NotFound(f"{dataset_version} has no vocabulary to tokenize with.")
        blob = BlobStore(self._settings.data_dir).path_for(str(found["sha256"]))
        return Vocabulary.from_json(blob.read_text(encoding="utf-8")), int(found["max_length"])

    def _announce(self, info: dict[str, Any] | None) -> None:
        with contextlib.suppress(Exception):
            self._bus.set_inference_loaded(info)

    def _load(self, version: Any, lineage: Any, device: Device) -> _Loaded:
        since = utc_now().isoformat()
        self._announce({"version_id": version.id, "device": device, "since": since})
        try:
            model, meta = self._read_weights(version, lineage)
        except BaseException:
            self._announce(None)
            raise
        loaded = _Loaded(
            version_id=version.id,
            lineage_id=lineage.id,
            name=f"{lineage.name} {version.id}",
            device=device,
            classes=list(meta["classes"]),
            shape=tuple(meta["input_shape"]),
            parameters=int(lineage.parameters),
            since=since,
            task=str(meta.get("task") or "classification"),
            prep=meta.get("_prep"),
        )
        if "_vocab" in meta:
            loaded.vocab, loaded.length = meta["_vocab"]
        if device == "cpu":
            loaded.model = model
            self._sink.emit(
                stage="inference",
                message=f"Loaded {loaded.name} on the CPU",
                metrics={"version_id": version.id},
            )
            return loaded
        self._gpu.prepare()
        if self._manager is None:
            self._manager = VramManager(self._gpu.backend(), sink=self._sink, stage="inference")
        holder = [model]  # the manager's state must hold the only reference (RUN-1.4)
        del model
        info = ModelInfo(lineage.name, version.id, loaded.name, "fp32")

        def build() -> TrainingState:
            return self._state_cls(self._gpu.to_device(holder.pop()), None, info=info)

        estimate = loaded.parameters * 4 / MB + 256  # fp32 weights and the CUDA context
        try:
            loaded.state = self._manager.load(build, name=loaded.name, estimate_mb=estimate)
        except BaseException:
            self._announce(None)
            raise
        self._last_gpu_use = time.monotonic()
        return loaded

    def _unload(self) -> None:
        loaded, self._loaded = self._loaded, None
        if loaded is None:
            return
        if loaded.device == "cpu":
            loaded.model = None
            del loaded
            gc.collect()
            self._sink.emit(stage="inference", message="Unloaded the CPU model")
        else:
            loaded.state = None  # the manager holds the state; it closes and checks it
            del loaded
            assert self._manager is not None
            report = self._manager.unload()
            if not report.returned_to_baseline:
                self._announce(None)
                self._on_leak()
        self._announce(None)

    # ------------------------------------------------------------------ give the GPU back
    def tick(self) -> str | None:
        """Release the GPU if it has been idle long enough or a job wants it (rule 5).
        Returns why it released, or ``None``."""
        with self._lock:
            if not self._lease.held:
                return None
            wanted: str | None = None
            with contextlib.suppress(Exception):
                wanted = self._bus.get_gpu_wanted()
            idle = time.monotonic() - self._last_gpu_use >= self._settings.inference_idle_release_s
            if wanted is None and not idle:
                return None
            reason = f"{wanted} is waiting for the GPU" if wanted else "idle"
            if self._loaded is not None and self._loaded.device == "gpu":
                self._unload()
            self._lease.release()
            self._sink.emit(stage="inference", message=f"Released the GPU lease: {reason}")
            return reason

    def run_checks(self, stop: threading.Event) -> None:
        """The background loop: :meth:`tick` every ``inference_check_interval_s``."""
        while not stop.wait(self._settings.inference_check_interval_s):
            try:
                self.tick()
            except Exception as exc:
                log.warning("inference_check_failed", reason=str(exc))

    def shutdown(self) -> None:
        with self._lock:
            with contextlib.suppress(Exception):
                self._unload()
            self._lease.release()
