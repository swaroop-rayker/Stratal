# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU and ML images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""The train stage: one training run, in one child process (RUN-1.1, ADR-0014).

It reads a labelled split version, builds a reference model, trains it on the
chosen device under the GPU lease and the VRAM cap, and reports live metrics at
most once a second (RUN-1.8). It writes a checkpoint after every epoch and
whenever ``checkpoint_every_s`` has passed (RUN-1.3). If the job's folder
already holds a checkpoint for the same config, as after a crash and a retry, it
resumes from there, replaying the rest of the epoch in the same order.

At the end it writes the trained weights to ``jobs/<job_id>/weights/`` and
removes the checkpoints (REG-2.6). The registry (M8 part B) takes the weights
from there.
"""

import json
import math
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import save_file
from sqlalchemy.orm import Session
from torch import nn
from torch.utils.data import DataLoader

from stratal.core.errors import CudaUnavailable, GpuOom, StratalError
from stratal.data import registry
from stratal.data.dataset_types import validate_labelled_images
from stratal.label.models import ModelInfo
from stratal.mon.events import MetricValue
from stratal.runtime.context import JobContext
from stratal.runtime.gpu_lease import gpu_lease
from stratal.runtime.jobs import config_hash
from stratal.runtime.stages import StageResult, StageRun
from stratal.storage.blobs import BlobStore
from stratal.train import checkpoint as ckpt
from stratal.train.config import TrainConfig
from stratal.train.data import EpochOrder, Item, LabelledImages, split_items
from stratal.train.memory import TrainingState, suggest_batch_size, training_oom_error
from stratal.train.reference import build_model, parameter_count

STAGE = "train"
PROGRESS_EVERY_S = 1.0
VRAM_FRACTION = 0.85
"""The per-process cap (ADR-0006 rule 8)."""
MB = 2**20


@dataclass(frozen=True)
class TrainUnits:
    """The owner's units the stage calls (ADR-0012); tests pass fakes."""

    state: type[TrainingState] = TrainingState
    suggest: Callable[..., int | None] = suggest_batch_size
    oom_error: Callable[..., GpuOom] = training_oom_error


OWNER_UNITS = TrainUnits()


def _owner_unit_missing(exc: NotImplementedError) -> StratalError:
    return StratalError(
        f"Stage {STAGE} needs an owner unit that is not written yet: {exc}",
        suggested_action="Write the unit (ADR-0012; its tests are strict expected failures "
        "until then).",
    )


def _device(choice: str) -> str:
    if choice == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if choice == "gpu":
        raise CudaUnavailable(
            "The run asks for the GPU, but this worker has no CUDA device.",
            suggested_action="Start the GPU worker (docker compose --profile gpu up -d), "
            "or set device to cpu.",
        )
    return "cpu"


def _input_shape(rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    sizes = {
        (int(r["derived_height"] or r["height"]), int(r["derived_width"] or r["width"]))
        for r in rows
        if r.get("status") == "accepted" and r.get("split")
    }
    if len(sizes) != 1:
        raise StratalError(
            f"The images of this version come in {len(sizes)} sizes; training needs one.",
            suggested_action="Use a dataset whose images share one size; resizing for "
            "training arrives with the model builder (M9).",
        )
    height, width = sizes.pop()
    return (3, height, width)


def _schedule(optimizer: torch.optim.Optimizer, config: TrainConfig, total_steps: int) -> Any:
    if config.schedule == "cosine":
        return torch.optim.lr_scheduler.LambdaLR(
            optimizer,
            lambda step: 0.5 * (1 + math.cos(math.pi * min(step, total_steps) / total_steps)),
        )
    return torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _step: 1.0)


def _optimizer(model: nn.Module, config: TrainConfig) -> torch.optim.Optimizer:
    if config.optimizer == "sgd":
        return torch.optim.SGD(
            model.parameters(),
            lr=config.learning_rate,
            momentum=config.momentum,
            weight_decay=config.weight_decay,
        )
    return torch.optim.Adam(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )


def _loader(items: list[Item], *, batch: int, sampler: Any, workers: int, cuda: bool) -> DataLoader:
    # Its own generator, so creating the loader never draws from the global random
    # state, which a checkpoint restores exactly (ADR-0014 rule 5).
    return DataLoader(
        LabelledImages(items),
        batch_size=batch,
        sampler=sampler,
        num_workers=workers,
        pin_memory=cuda,
        generator=torch.Generator().manual_seed(0),
    )


class _Run:
    """The state of one training run, between the setup and the loop."""

    def __init__(self, run: StageRun, config: TrainConfig, units: TrainUnits) -> None:
        self.run = run
        self.ctx: JobContext = run.ctx
        self.config = config
        self.units = units
        self.hash = config_hash(config.model_dump(mode="json"))
        self.folder = ckpt.checkpoint_dir(run.data_dir, run.ctx.job_id)

    # ------------------------------------------------------------------ setup
    def prepare(self) -> None:
        version_id = self.run.input_version_id
        if version_id is None:
            raise StratalError("The train stage was given no dataset version.")
        with Session(self.run.engine) as session:
            rows = list(registry.load_version(session, self.run.data_dir, version_id))
        blobs = BlobStore(self.run.data_dir)
        report = validate_labelled_images(rows, blobs.exists)
        if not report.is_valid:
            raise StratalError(
                f"{len(report.problems):,} rows of {version_id} fail the labelled-images "
                f"schema: {report.by_reason()}.",
                suggested_action="Run the pipeline again from ingest; the files may have "
                "been removed from the data folder.",
            )
        items = split_items(rows, blobs)
        self.classes = items.classes
        self.train = items.by_split.get("train", [])
        self.val = items.by_split.get("val", [])
        if not self.train or not self.val:
            raise StratalError(
                f"{version_id} has {len(self.train)} train and {len(self.val)} val rows; "
                "training needs both.",
                suggested_action="Train on a version with train and val rows.",
            )
        self.shape = _input_shape(rows)
        self.device = _device(self.config.device)
        default_workers = 4 if self.device == "cuda" else 1
        self.workers = (
            self.config.loader_workers
            if self.config.loader_workers is not None
            else default_workers
        )
        self.steps_per_epoch = math.ceil(len(self.train) / self.config.batch_size)
        self.total_steps = self.steps_per_epoch * self.config.epochs

    # ------------------------------------------------------------------ the run
    def execute(self) -> StageResult:
        started = time.monotonic()
        config = self.config
        random.seed(config.seed)
        torch.manual_seed(config.seed)
        if config.deterministic:
            os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
            torch.use_deterministic_algorithms(True)
            torch.backends.cudnn.benchmark = False
        model = build_model(config.model, self.shape, len(self.classes))
        self.parameters = parameter_count(model)
        self.ctx.emit(
            stage=STAGE,
            substage="setup",
            message=f"Training {config.model.name} ({self.parameters / 1e6:.2f} M parameters) "
            f"on the {'GPU' if self.device == 'cuda' else 'CPU'} · {len(self.train):,} images · "
            f"{config.epochs} epochs · batch {config.batch_size}",
            metrics={
                "parameters": self.parameters,
                "device": "gpu" if self.device == "cuda" else "cpu",
                "train_rows": len(self.train),
                "val_rows": len(self.val),
                "classes": len(self.classes),
                "loader_workers": self.workers,
            },
        )
        if self.device != "cuda":
            optimizer = _optimizer(model, config)
            return self._train(model, optimizer, started)

        from stratal.label.hf import TorchGpu
        from stratal.label.vram import VramManager

        with gpu_lease(self.run.engine, holder=f"{self.ctx.job_id}/{STAGE}", wait_s=60):
            torch.cuda.set_per_process_memory_fraction(VRAM_FRACTION)
            manager = VramManager(TorchGpu(), sink=self.ctx, stage=STAGE)
            estimate = self.parameters * 16 / MB + 256  # weights, grads, Adam's two; context
            info = ModelInfo(
                config.model.name,
                f"algorithm-{config.algorithm_version}",
                config.model.name,
                "fp32",
            )

            # The manager must hold the only reference, so close() can free it (RUN-1.4).
            holder = [model]
            del model

            def load() -> TrainingState:
                on_card = holder.pop().to("cuda")
                return self.units.state(on_card, _optimizer(on_card, config), info=info)

            state = manager.load(load, name=config.model.name, estimate_mb=estimate)
            result = self._train(state.model, state.optimizer, started)
            try:
                manager.unload()
            except NotImplementedError:
                self.ctx.emit(
                    stage=STAGE,
                    level="WARN",
                    message="VRAM manager v2 is not written yet (RUN-1.4); the process "
                    "exit frees the GPU",
                    reason="TrainingState.close is an owner unit.",
                    suggested_action="None needed for this run.",
                )
            return result

    def _train(
        self, model: nn.Module, optimizer: torch.optim.Optimizer, started: float
    ) -> StageResult:
        config, ctx = self.config, self.ctx
        cuda = self.device == "cuda"
        scheduler = _schedule(optimizer, config, self.total_steps)
        state = ckpt.Checkpoint(ckpt.Position(0, 0, 0), self.hash)
        found = ckpt.latest(self.folder)
        if found is not None:
            loaded = ckpt.load(found)
            if loaded.checkpoint.config_hash == self.hash:
                ckpt.restore(loaded, model=model, optimizer=optimizer, scheduler=scheduler)
                state = loaded.checkpoint
                p = state.position
                ctx.emit(
                    stage=STAGE,
                    substage="resume",
                    message=f"Resuming from checkpoint: epoch {p.epoch + 1}/{config.epochs}, "
                    f"step {p.batches_done}/{self.steps_per_epoch}",
                    metrics={"resumed_at_step": p.global_step, "resumed_epoch": p.epoch + 1},
                )
            else:
                ctx.emit(
                    stage=STAGE,
                    level="WARN",
                    message=f"Ignoring checkpoint {found.stem}: it was made with another config",
                )
        loss_fn = nn.CrossEntropyLoss()
        model.train()
        last_checkpoint = time.monotonic()
        checkpoint_bytes = 0
        position = state.position
        global_step = position.global_step

        for epoch in range(position.epoch, config.epochs):
            first = position.batches_done if epoch == position.epoch else 0
            sampler = EpochOrder(
                len(self.train), seed=config.seed, epoch=epoch, start=first * config.batch_size
            )
            loader = _loader(
                self.train,
                batch=config.batch_size,
                sampler=sampler,
                workers=self.workers,
                cuda=cuda,
            )
            window_start, window_steps, window_samples = time.monotonic(), 0, 0
            loss_sum = torch.zeros((), device=self.device)
            correct = torch.zeros((), device=self.device, dtype=torch.long)
            seen = 0
            grad_norm = torch.zeros((), device=self.device)
            for batch_index, (x, y) in enumerate(loader, start=first):
                ctx.raise_if_cancelled()
                x = x.to(self.device, non_blocking=True)
                y = y.to(self.device, non_blocking=True)
                optimizer.zero_grad(set_to_none=True)
                try:
                    logits = model(x)
                    loss = loss_fn(logits, y)
                    loss.backward()
                except torch.cuda.OutOfMemoryError:
                    del x, y
                    raise self._oom(optimizer) from None
                grad_norm = nn.utils.clip_grad_norm_(model.parameters(), float("inf"))
                optimizer.step()
                scheduler.step()
                global_step += 1
                loss_sum += loss.detach() * len(y)
                correct += (logits.detach().argmax(1) == y).sum()
                seen += len(y)
                window_steps += 1
                window_samples += len(y)
                position = ckpt.Position(epoch, batch_index + 1, global_step)
                now = time.monotonic()
                last_of_epoch = batch_index + 1 == self.steps_per_epoch
                if now - window_start >= PROGRESS_EVERY_S or last_of_epoch:
                    elapsed = max(now - window_start, 1e-9)
                    ctx.emit(
                        stage=STAGE,
                        substage="train",
                        message=f"Epoch {epoch + 1}/{config.epochs} · step "
                        f"{batch_index + 1}/{self.steps_per_epoch}",
                        step=global_step,
                        total=self.total_steps,
                        metrics={
                            "epoch": epoch + 1,
                            "loss": round(float(loss_sum) / seen, 5),
                            "accuracy": round(int(correct) / seen, 5),
                            "learning_rate": round(scheduler.get_last_lr()[0], 8),
                            "grad_norm": round(float(grad_norm), 5),
                            "step_ms": round(1000 * elapsed / window_steps, 2),
                            "samples_per_s": round(window_samples / elapsed, 1),
                        },
                    )
                    window_start, window_steps, window_samples = now, 0, 0
                if not last_of_epoch and now - last_checkpoint >= config.checkpoint_every_s:
                    state = ckpt.Checkpoint(position, self.hash, state.history)
                    checkpoint_bytes = self._save(model, optimizer, scheduler, state)
                    last_checkpoint = time.monotonic()

            val_loss, val_accuracy = self._evaluate(model, loss_fn)
            record = {
                "epoch": epoch + 1,
                "train_loss": round(float(loss_sum) / max(seen, 1), 5),
                "train_accuracy": round(int(correct) / max(seen, 1), 5),
                "val_loss": round(val_loss, 5),
                "val_accuracy": round(val_accuracy, 5),
            }
            position = ckpt.Position(epoch + 1, 0, global_step)
            state = ckpt.Checkpoint(position, self.hash, [*state.history, record])
            ctx.emit(
                stage=STAGE,
                substage="validate",
                message=f"Epoch {epoch + 1}/{config.epochs}: val loss {val_loss:.4f} · "
                f"val accuracy {100 * val_accuracy:.1f}%",
                metrics=dict(record),
            )
            checkpoint_bytes = self._save(model, optimizer, scheduler, state)
            last_checkpoint = time.monotonic()

        weights = self._write_weights(model, state.history)
        freed = ckpt.remove_all(self.folder)
        ctx.emit(
            stage=STAGE,
            substage="finish",
            message=f"Run complete; checkpoints removed ({freed / MB:.1f} MB)",
        )
        last = state.history[-1] if state.history else {}
        metrics: dict[str, MetricValue] = {
            "epochs": config.epochs,
            "steps": global_step,
            "parameters": self.parameters,
            "device": "gpu" if cuda else "cpu",
            "val_accuracy": last.get("val_accuracy", 0.0),
            "val_loss": last.get("val_loss", 0.0),
            "train_loss": last.get("train_loss", 0.0),
            "checkpoint_bytes": checkpoint_bytes,
            "weights_bytes": weights,
            "seconds": round(time.monotonic() - started, 1),
        }
        if cuda:
            metrics["peak_vram_allocated_mb"] = round(torch.cuda.max_memory_allocated() / MB, 1)
            metrics["peak_vram_reserved_mb"] = round(torch.cuda.max_memory_reserved() / MB, 1)
        return StageResult(metrics=metrics)

    # ------------------------------------------------------------------ helpers
    def _save(
        self, model: nn.Module, optimizer: Any, scheduler: Any, state: ckpt.Checkpoint
    ) -> int:
        started = time.perf_counter()
        meta = ckpt.save(
            self.folder, model=model, optimizer=optimizer, scheduler=scheduler, checkpoint=state
        )
        size = meta.stat().st_size + meta.with_suffix(".safetensors").stat().st_size
        p = state.position
        where = (
            f"at the end of epoch {p.epoch}"
            if p.batches_done == 0
            else f"in epoch {p.epoch + 1}, step {p.batches_done}"
        )
        self.ctx.emit(
            stage=STAGE,
            substage="checkpoint",
            message=f"Saving checkpoint {where} ({size / MB:.1f} MB, "
            f"{1000 * (time.perf_counter() - started):.0f} ms)",
            metrics={"checkpoint_step": p.global_step, "checkpoint_bytes": size},
        )
        return size

    def _evaluate(self, model: nn.Module, loss_fn: nn.Module) -> tuple[float, float]:
        model.eval()
        total_loss, correct, seen = 0.0, 0, 0
        loader = _loader(
            self.val, batch=512, sampler=None, workers=self.workers, cuda=self.device == "cuda"
        )
        with torch.no_grad():
            for x, y in loader:
                self.ctx.raise_if_cancelled()
                x, y = x.to(self.device), y.to(self.device)
                logits = model(x)
                total_loss += float(loss_fn(logits, y)) * len(y)
                correct += int((logits.argmax(1) == y).sum())
                seen += len(y)
        model.train()
        return total_loss / seen, correct / seen

    def _write_weights(self, model: nn.Module, history: list[dict[str, Any]]) -> int:
        folder = self.run.data_dir / "jobs" / self.ctx.job_id / "weights"
        folder.mkdir(parents=True, exist_ok=True)
        tensors = {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()}
        save_file(tensors, str(folder / "model.safetensors"))
        meta = {
            "model": self.config.model.model_dump(mode="json"),
            "input_shape": list(self.shape),
            "classes": self.classes,
            "parameters": self.parameters,
            "dataset_version": self.run.input_version_id,
            "config": self.config.model_dump(mode="json"),
            "config_hash": self.hash,
            "history": history,
        }
        (folder / "model.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return sum(p.stat().st_size for p in folder.iterdir())

    def _oom(self, optimizer: Any) -> StratalError:
        optimizer.zero_grad(set_to_none=True)
        peak = torch.cuda.max_memory_allocated() / MB
        total = torch.cuda.get_device_properties(0).total_memory / MB
        cap = total * VRAM_FRACTION
        fixed = torch.cuda.memory_allocated() / MB
        torch.cuda.empty_cache()
        batch = self.config.batch_size
        per_sample = max(peak - fixed, 0.0) / batch
        try:
            suggested = self.units.suggest(
                failed_batch=batch, fixed_mb=fixed, per_sample_mb=per_sample, cap_mb=cap
            )
            return self.units.oom_error(
                failed_batch=batch, suggested=suggested, peak_mb=peak, cap_mb=cap
            )
        except NotImplementedError as exc:
            return _owner_unit_missing(exc)


def run_train(run: StageRun, units: TrainUnits = OWNER_UNITS) -> StageResult:
    """Run one training stage (RUN-1.1). ``units`` are the owner's; tests pass fakes."""
    config = TrainConfig.model_validate(run.config)
    training = _Run(run, config, units)
    training.prepare()
    return training.execute()


def weights_folder(data_dir: Path, job_id: str) -> Path:
    return data_dir / "jobs" / job_id / "weights"
