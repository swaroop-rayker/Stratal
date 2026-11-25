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

Every run belongs to a lineage (ADR-0015 rule 1). The first run of a lineage
saves its initial weights as the lineage's base before the first step; later
runs start from that base (REG-2.1). A pause records the run's version as
``partial``; at the end the trained weights go to ``registry/<lineage>/<version>/``,
the version becomes ``trained``, and only then are the checkpoints removed
(REG-1.1, REG-1.2, REG-2.6). A retry of a run that already registered its
weights returns that result without training again.
"""

import gc
import json
import math
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, cast

import torch
from sqlalchemy.orm import Session
from torch import nn
from torch.utils.data import DataLoader

from stratal.build.io import TRAIN_VRAM_FRACTION, image_shape
from stratal.build.shapes import Shape
from stratal.core.errors import CudaUnavailable, GpuOom, StratalError
from stratal.data import registry
from stratal.data.dataset_types import validate_labelled_images
from stratal.data.records import LABELLED_TEXT, TABULAR, schema_to_json
from stratal.db.models import Dataset
from stratal.eval import task_metrics
from stratal.label.models import ModelInfo
from stratal.mon.events import MetricValue, utc_now
from stratal.registry import store
from stratal.runtime.context import JobCancelled, JobContext, JobPaused, StageHandoff
from stratal.runtime.gpu_lease import gpu_lease
from stratal.runtime.jobs import config_hash
from stratal.runtime.stages import StageResult, StageRun
from stratal.storage.blobs import BlobStore
from stratal.train import checkpoint as ckpt
from stratal.train import device, table_prep, weights
from stratal.train.config import TrainConfig
from stratal.train.data import EpochOrder, LabelledImages, TensorRows, split_items
from stratal.train.memory import TrainingState, suggest_batch_size, training_oom_error
from stratal.train.reference import build_model, parameter_count
from stratal.train.table_input import graph_of, with_table_input

STAGE = "train"
PROGRESS_EVERY_S = 1.0
VRAM_FRACTION = TRAIN_VRAM_FRACTION
TRIAL_RESULT = "result.json"
"""A finished trial's metrics and history, beside its weights (ADR-0018 rule 7)."""
"""The per-process cap (ADR-0006 rule 8)."""
MB = 2**20


@dataclass(frozen=True)
class TrainUnits:
    """The owner's units the stage calls (ADR-0012); tests pass fakes."""

    state: type[TrainingState] = TrainingState
    suggest: Callable[..., int | None] = suggest_batch_size
    oom_error: Callable[..., GpuOom] = training_oom_error


OWNER_UNITS = TrainUnits()


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


def _schedule(optimizer: torch.optim.Optimizer, config: TrainConfig, total_steps: int) -> Any:
    if config.schedule == "cosine":
        return torch.optim.lr_scheduler.LambdaLR(
            optimizer,
            lambda step: 0.5 * (1 + math.cos(math.pi * min(step, total_steps) / total_steps)),
        )
    return torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _step: 1.0)


def _optimizer(model: nn.Module, config: TrainConfig) -> torch.optim.Optimizer:
    """Over the trainable parameters only: frozen ones get no gradient, no weight decay and
    no optimizer state (ADR-0014 Addendum A rules 7 and 8)."""
    params = [p for p in model.parameters() if p.requires_grad]
    if config.optimizer == "sgd":
        return torch.optim.SGD(
            params,
            lr=config.learning_rate,
            momentum=config.momentum,
            weight_decay=config.weight_decay,
        )
    return torch.optim.Adam(params, lr=config.learning_rate, weight_decay=config.weight_decay)


def _head_prefix(model: nn.Module) -> str:
    """The state-dict prefix of the locked head: ``head.`` for a compiled graph (ADR-0017
    rule 8), the last ``Linear`` of a reference model's ``Sequential``."""
    if hasattr(model, "head"):
        return "head."
    last = [name for name, m in model.named_modules() if isinstance(m, nn.Linear)][-1]
    return last + "."


def _loader(dataset: Any, *, batch: int, sampler: Any, workers: int, cuda: bool) -> DataLoader:
    # Its own generator, so creating the loader never draws from the global random
    # state, which a checkpoint restores exactly (ADR-0014 rule 5).
    return DataLoader(
        dataset,
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
        self.trial: Any = None
        """For an ``hpo_trial`` job: its link to the study (ADR-0018 rules 5 to 7)."""
        self.parent: Any = None
        """For a fine-tune: the trained version it starts from (RUN-1.10, ADR-0014 Addendum A)."""
        self.parent_val_loss: float | None = None
        """The parent's val loss when this run validates on the parent's own split with its
        own head: what epoch 0 must equal (RUN-1.10.AC1)."""
        self.frozen: list[nn.Module] = []
        self.parent_id: str | None = None
        """The version this run's version names as parent: ``init_from``, else the base."""
        if run.spec.get("kind") == "hpo_trial":
            from stratal.hpo.trial import TrialLink

            self.trial = TrialLink(run.engine, run.data_dir, run.ctx.job_id, run.spec)

    # ------------------------------------------------------------------ setup
    def prepare(self) -> None:
        version_id = self.run.input_version_id
        if version_id is None:
            raise StratalError("The train stage was given no dataset version.")
        self.prep: table_prep.TablePrep | None = None
        self.table_schema: Any = None
        self.task = "classification"
        self.noun = "images"
        with Session(self.run.engine) as session:
            version, manifest = registry.manifest_for(session, self.run.data_dir, version_id)
            if self.config.init_from is not None:
                self._check_parent(session, version)
            dataset = session.get(Dataset, version.dataset_id)
            assert dataset is not None
            self.dataset_id, self.dataset_slug = dataset.id, dataset.slug
            self.dtype = version.dtype
            if self.dtype == LABELLED_TEXT:
                rows = list(
                    registry.load_version(
                        session,
                        self.run.data_dir,
                        version_id,
                        columns=["row_key", "status", "split", "label", "tokens"],
                    )
                )
            elif self.dtype != TABULAR:
                rows = list(registry.load_version(session, self.run.data_dir, version_id))
        if self.dtype == TABULAR:
            self._prepare_table(manifest)
        elif self.dtype == LABELLED_TEXT:
            self._prepare_text(manifest, rows)
        else:
            self._prepare_images(rows)
        if len(self.train) == 0 or len(self.val) == 0:
            raise StratalError(
                f"{version_id} has {len(self.train)} train and {len(self.val)} val rows; "
                "training needs both.",
                suggested_action="Train on a version with train and val rows.",
            )
        self.outputs = len(self.classes) if self.task == "classification" else 1
        self.device = _device(self.config.device)
        in_memory = self.dtype in (TABULAR, LABELLED_TEXT)
        default_workers = 0 if in_memory else 4 if self.device == "cuda" else 1
        self.workers = (
            self.config.loader_workers
            if self.config.loader_workers is not None
            else default_workers
        )
        self.steps_per_epoch = math.ceil(len(self.train) / self.config.batch_size)
        self.total_steps = self.steps_per_epoch * self.config.epochs

    def _check_parent(self, session: Session, version: Any) -> None:
        """The fine-tune's checks again, as the API ran them (ADR-0014 Addendum A rule 4):
        the parent may have lost its weights, or the request skipped the API."""
        from stratal.train import finetune

        fine = finetune.check(
            session, self.run.engine, self.run.data_dir, config=self.config, version=version
        )
        finetune.refuse(fine)
        self.parent = fine.parent.version
        metrics = fine.parent.version.metrics or {}
        same_split = fine.parent.version.dataset_version_id == version.version_id
        if same_split and not self.config.reset_head and "val_loss" in metrics:
            self.parent_val_loss = float(metrics["val_loss"])

    def _prepare_images(self, rows: list[dict[str, Any]]) -> None:
        version_id = self.run.input_version_id
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
        self.train = LabelledImages(items.by_split.get("train", []))
        self.val = LabelledImages(items.by_split.get("val", []))
        self.test = LabelledImages(items.by_split.get("test", []))
        self.shape = cast(tuple[int, int, int], image_shape(rows).dims)  # one rule with the builder
        self.input = Shape("image", self.shape)

    def _prepare_table(self, manifest: Any) -> None:
        """A table: the fitted encoding from train (ADR-0019 rules 12 to 14, ML-1.3)."""
        from stratal.ml.data import load_tabular

        data = load_tabular(manifest)
        self.table_schema = data.schema
        fitted = table_prep.fit(data)
        prep: table_prep.TablePrep = fitted
        if self.parent is not None:
            # The weights were learnt for the parent's scaling and encoding: keep them, a
            # deliberate exception to ML-1.3 (ADR-0014 Addendum A rule 5). Only a reset
            # head takes this split's classes and target scale.
            from stratal.train.finetune import parent_table_prep

            theirs = parent_table_prep(self.run.data_dir, self.parent)
            prep = (
                replace(
                    theirs,
                    classes=fitted.classes,
                    target_mean=fitted.target_mean,
                    target_std=fitted.target_std,
                )
                if self.config.reset_head
                else theirs
            )
        self.prep = prep
        self.task = prep.task
        self.classes = list(prep.classes)
        self.noun = "rows"
        target_type = torch.long if self.task == "classification" else torch.float32

        def rows(split: str) -> TensorRows:
            part = data.splits[split]
            assert self.prep is not None
            x = torch.tensor(self.prep.encode(part.columns), dtype=torch.float32)
            x = x.reshape(len(part), self.prep.raw_width)
            y = torch.tensor(self.prep.target(part.target) if len(part) else [], dtype=target_type)
            return TensorRows(x, y)

        self.train, self.val, self.test = rows("train"), rows("val"), rows("test")
        self.input = Shape("vector", (prep.width,))
        self.shape = self.input.dims

    def _prepare_text(self, manifest: Any, rows: list[dict[str, Any]]) -> None:
        """Token IDs from the tokenize stage, L and the vocabulary from its metadata
        (ADR-0019 rules 9 and 12)."""
        from stratal.data.tokenize import decode_tokens, vocab_of

        vocab = vocab_of(manifest)
        if vocab is None:
            raise StratalError(
                f"{self.run.input_version_id} has no vocabulary; texts train on a tokenize "
                "version.",
                suggested_action="Run the labelled-text pipeline through its tokenize stage.",
            )
        length, size = int(vocab["max_length"]), int(vocab["size"])
        accepted = sorted(
            (r for r in rows if r["status"] == "accepted" and r["split"]),
            key=lambda r: str(r["row_key"]),
        )
        self.classes = sorted({str(r["label"]) for r in accepted})
        index = {name: i for i, name in enumerate(self.classes)}
        self.noun = "texts"

        def rows_of(split: str) -> TensorRows:
            part = [r for r in accepted if r["split"] == split]
            x = torch.tensor([decode_tokens(str(r["tokens"])) for r in part], dtype=torch.long)
            x = x.reshape(len(part), length)
            y = torch.tensor([index[str(r["label"])] for r in part], dtype=torch.long)
            return TensorRows(x, y)

        self.train, self.val, self.test = rows_of("train"), rows_of("val"), rows_of("test")
        self.input = Shape("tokens", (length,), vocab=size)
        self.shape = self.input.dims

    # ------------------------------------------------------------------ the run
    def execute(self) -> StageResult:
        started = time.monotonic()
        config = self.config
        self.choice: device.DeviceChoice | None = None
        if config.device == "auto":
            self._choose_device()
        random.seed(config.seed)
        torch.manual_seed(config.seed)
        if config.deterministic:
            os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
            torch.use_deterministic_algorithms(True)
            torch.backends.cudnn.benchmark = False
        model = with_table_input(build_model(config.model, self.input, self.outputs), self.prep)
        self.parameters = parameter_count(model)
        if self.parent is not None:
            self._start_from_parent(model)
        self.ctx.emit(
            stage=STAGE,
            substage="setup",
            message=f"Training {config.model.display} ({self.parameters / 1e6:.2f} M parameters) "
            f"on the {'GPU' if self.device == 'cuda' else 'CPU'} · {len(self.train):,} "
            f"{self.noun} · {self.task} · "
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
        self._attach_lineage(model)
        if self.device != "cuda":
            optimizer = _optimizer(model, config)
            return self._with_choice(self._train(model, optimizer, started))

        from stratal.label.hf import TorchGpu
        from stratal.label.vram import VramManager

        with gpu_lease(self.run.engine, holder=f"{self.ctx.job_id}/{STAGE}", wait_s=60):
            torch.cuda.set_per_process_memory_fraction(VRAM_FRACTION)
            manager = VramManager(TorchGpu(), sink=self.ctx, stage=STAGE)
            estimate = self.parameters * 16 / MB + 256  # weights, grads, Adam's two; context
            info = ModelInfo(
                config.model.display,
                f"algorithm-{config.algorithm_version}",
                config.model.display,
                "fp32",
            )

            # The manager must hold the only reference, so close() can free it (RUN-1.4).
            holder = [model]
            del model

            def load() -> TrainingState:
                on_card = holder.pop().to("cuda")
                return self.units.state(on_card, _optimizer(on_card, config), info=info)

            state = manager.load(load, name=config.model.display, estimate_mb=estimate)
            result = self._with_choice(self._train(state.model, state.optimizer, started))
            del state  # the manager now holds the only reference (RUN-1.4)
            manager.unload()  # proves the card is back at its baseline, or warns (BUG5)
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
        regression = self.task == "regression"
        loss_fn: nn.Module = nn.MSELoss() if regression else nn.CrossEntropyLoss()
        if self.parent is not None and not state.history and state.position.global_step == 0:
            state = ckpt.Checkpoint(state.position, self.hash, [self._epoch_zero(model, loss_fn)])
        self._train_mode(model)
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
                self._stop_if_asked(model, optimizer, scheduler, state, position)
                x = x.to(self.device, non_blocking=True)
                y = y.to(self.device, non_blocking=True)
                optimizer.zero_grad(set_to_none=True)
                out_of_memory = False
                try:
                    logits = model(x)
                    loss = loss_fn(logits.squeeze(1) if regression else logits, y)
                    loss.backward()
                    grad_norm = nn.utils.clip_grad_norm_(model.parameters(), float("inf"))
                    optimizer.step()  # Adam's state is allocated on the first step
                except torch.cuda.OutOfMemoryError:
                    out_of_memory = True
                if out_of_memory:
                    # Outside the except block, so the traceback no longer holds the failed
                    # step's tensors; dropping these frees the rest before _oom measures.
                    x = y = logits = loss = None
                    raise self._oom(optimizer) from None
                scheduler.step()
                global_step += 1
                loss_sum += loss.detach() * len(y)
                if not regression:
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
                        f"{batch_index + 1}/{self.steps_per_epoch} · {config.model.display} · "
                        f"bs={config.batch_size} · lr {scheduler.get_last_lr()[0]:.1e} · fp32",
                        step=global_step,
                        total=self.total_steps,
                        metrics={
                            "epoch": epoch + 1,
                            "loss": round(float(loss_sum) / seen, 5),
                            **self._train_score(float(loss_sum) / seen, int(correct) / seen),
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

            val_loss, val_score = self._evaluate(model, loss_fn)
            train_loss = float(loss_sum) / max(seen, 1)
            score_name = "rmse" if regression else "accuracy"
            train_score = self._rmse(train_loss) if regression else int(correct) / max(seen, 1)
            record = {
                "epoch": epoch + 1,
                "train_loss": round(train_loss, 5),
                f"train_{score_name}": round(train_score, 5),
                "val_loss": round(val_loss, 5),
                f"val_{score_name}": round(val_score, 5),
            }
            position = ckpt.Position(epoch + 1, 0, global_step)
            state = ckpt.Checkpoint(position, self.hash, [*state.history, record])
            ctx.emit(
                stage=STAGE,
                substage="validate",
                message=f"Epoch {epoch + 1}/{config.epochs}: val loss {val_loss:.4f} · "
                + (
                    f"val RMSE {val_score:.4g}"
                    if regression
                    else f"val accuracy {100 * val_score:.1f}%"
                ),
                metrics=dict(record),
            )
            checkpoint_bytes = self._save(model, optimizer, scheduler, state)
            last_checkpoint = time.monotonic()
            if self.trial is not None:
                stop = self.trial.after_epoch(epoch + 1, record, last=epoch + 1 == config.epochs)
                if stop is not None:
                    return self._pruned(stop, record, epoch + 1, started)

        last = state.history[-1] if state.history else {}
        metrics: dict[str, MetricValue] = {
            "epochs": config.epochs,
            "steps": global_step,
            "parameters": self.parameters,
            "device": "gpu" if cuda else "cpu",
            "val_loss": last.get("val_loss", 0.0),
            "train_loss": last.get("train_loss", 0.0),
            "checkpoint_bytes": checkpoint_bytes,
            "seconds": round(time.monotonic() - started, 1),
        }
        if not regression:
            metrics["val_accuracy"] = last.get("val_accuracy", 0.0)
        # A trial never sees the test split (ADR-0018 rule 8): its study tests the best once.
        scores, detail = self._final_scores(model, ("val",) if self.trial else ("val", "test"))
        metrics.update(scores)
        if cuda:
            metrics["peak_vram_allocated_mb"] = round(torch.cuda.max_memory_allocated() / MB, 1)
            metrics["peak_vram_reserved_mb"] = round(torch.cuda.max_memory_reserved() / MB, 1)
        version_id = self._register(model, state.history, metrics, detail)
        freed = self._remove_checkpoints()
        if self.trial is not None:
            value = self.trial.complete(last)
            ctx.emit(
                stage=STAGE,
                substage="finish",
                message=f"Trial {self.trial.number} complete: {self.trial.objective} "
                f"{value:.4g}; its weights wait for the study's next step; checkpoints "
                f"removed ({freed / MB:.1f} MB)",
                metrics={"trial": self.trial.number, "value": value},
            )
            return StageResult(metrics=metrics)
        ctx.emit(
            stage=STAGE,
            substage="finish",
            message=f"Run complete: registered {version_id}; checkpoints removed "
            f"({freed / MB:.1f} MB)",
            metrics={"model_version_id": version_id},
        )
        return StageResult(metrics=metrics)

    def _pruned(
        self, reason: str, record: dict[str, Any], epoch: int, started: float
    ) -> StageResult:
        """A trial stopped by the median pruner or the study's time budget (HPO-1.3)."""
        assert self.trial is not None
        freed = self._remove_checkpoints()
        self.trial.pruned(reason, record)
        self.ctx.emit(
            stage=STAGE,
            substage="pruned",
            message=f"Trial {self.trial.number} pruned after epoch {epoch} · {reason}; "
            f"checkpoints removed ({freed / MB:.1f} MB)",
            metrics={"trial": self.trial.number, "pruned_at_step": epoch, "prune_reason": reason},
        )
        return StageResult(
            metrics={
                "trial": self.trial.number,
                "pruned_at_step": epoch,
                "prune_reason": reason,
                "seconds": round(time.monotonic() - started, 1),
            }
        )

    # ------------------------------------------------------------------ RUN-1.9
    def _choose_device(self) -> None:
        """For ``device: auto``: time both devices on a small model, and move to the CPU
        worker if it wins (ADR-0014 rule 8). Large models stay on the GPU untimed."""
        config = self.config
        probe = with_table_input(build_model(config.model, self.input, self.outputs), self.prep)
        small = parameter_count(probe) < device.MAX_PARAMETERS
        del probe
        if not small:
            return
        key = device.cache_key(config.model, self.input, config.batch_size, self.outputs)
        if self.run.queue == "cpu" or self.device != "cuda":
            # Moved here by an earlier benchmark (or no GPU): train on the CPU, and report
            # the timings that decided it.
            self._use_cpu()
            self.choice = device.cached(self.run.data_dir, key)
            return
        batches = self._sample_batches(device.WARMUP + device.TIMED)
        with gpu_lease(self.run.engine, holder=f"{self.ctx.job_id}/{STAGE}", wait_s=60):
            torch.cuda.set_per_process_memory_fraction(VRAM_FRACTION)
            self.choice = device.choose(
                data_dir=self.run.data_dir,
                spec=config.model,
                shape=self.input,
                num_classes=self.outputs,
                batch=config.batch_size,
                batches=batches,
                wrap=lambda model: with_table_input(model, self.prep),
                on_start=lambda: self.ctx.emit(
                    stage=STAGE,
                    substage="device",
                    message=f"Benchmarking devices: {device.TIMED} steps on each",
                    metrics={"device_benchmark_steps": device.TIMED},
                ),
                on_measured=lambda which, ms: self.ctx.emit(
                    stage=STAGE,
                    substage="device",
                    message=f"Benchmarked the {'GPU' if which == 'cuda' else 'CPU'}: "
                    f"{ms:.1f} ms per step",
                    metrics={f"device_{'gpu' if which == 'cuda' else 'cpu'}_step_ms": round(ms, 2)},
                ),
            )
        choice = self.choice
        winner = "GPU" if choice.device == "cuda" else "CPU"
        how = "cached benchmark" if choice.cached else "benchmark"
        self.ctx.emit(
            stage=STAGE,
            substage="device",
            message=f"Device auto: the {winner} is faster for {config.model.display} at batch "
            f"{config.batch_size} ({how}: GPU {choice.gpu_step_ms:.1f} ms, "
            f"CPU {choice.cpu_step_ms:.1f} ms per step)",
            metrics=dict(choice.metrics()),
        )
        if choice.device == "cpu":
            raise StageHandoff(
                "cpu",
                f"the CPU trains {config.model.display} faster ({choice.cpu_step_ms:.1f} ms "
                f"against {choice.gpu_step_ms:.1f} ms per step)",
            )

    def _use_cpu(self) -> None:
        self.device = "cpu"
        if self.config.loader_workers is None:
            self.workers = 1

    def _sample_batches(self, count: int) -> list[tuple[torch.Tensor, torch.Tensor]]:
        """``count`` batches of the run's own training data, repeating if it is small."""
        sampler = EpochOrder(len(self.train), seed=self.config.seed, epoch=0)
        loader = _loader(
            self.train, batch=self.config.batch_size, sampler=sampler, workers=0, cuda=False
        )
        batches: list[tuple[torch.Tensor, torch.Tensor]] = []
        while len(batches) < count:
            for x, y in loader:
                batches.append((x, y))
                if len(batches) == count:
                    break
        return batches

    def _with_choice(self, result: StageResult) -> StageResult:
        if self.choice is not None:
            result.metrics.update(self.choice.metrics())
        return result

    # ------------------------------------------------------------------ helpers
    def _stop_if_asked(
        self,
        model: nn.Module,
        optimizer: Any,
        scheduler: Any,
        state: ckpt.Checkpoint,
        position: ckpt.Position,
    ) -> None:
        """At a safe point between steps: stop if cancelled; on a pause, checkpoint first
        (RUN-1.2, ADR-0014 rule 6), so the resume continues from this very step."""
        request = self.ctx.stop_request()
        if request == "cancel":
            raise JobCancelled(self.ctx.job_id)
        if request == "pause":
            self._save(
                model, optimizer, scheduler, ckpt.Checkpoint(position, self.hash, state.history)
            )
            self._record_partial(state.history)
            # A last progress report at the checkpoint's step: progress is otherwise reported
            # at most once a second, so the stage's step would lag the pause (SCR-07's toast).
            self.ctx.emit(
                stage=STAGE,
                substage="pause",
                message=f"Paused in epoch {position.epoch + 1}/{self.config.epochs}, after step "
                f"{position.batches_done}/{self.steps_per_epoch}; the checkpoint is kept",
                step=position.global_step,
                total=self.total_steps,
                metrics={"epoch": position.epoch + 1},
            )
            raise JobPaused(self.ctx.job_id)

    def _save(
        self, model: nn.Module, optimizer: Any, scheduler: Any, state: ckpt.Checkpoint
    ) -> int:
        started = time.perf_counter()
        meta = ckpt.save(
            self.folder, model=model, optimizer=optimizer, scheduler=scheduler, checkpoint=state
        )
        size = meta.stat().st_size + meta.with_suffix(".safetensors").stat().st_size
        p = state.position
        with Session(self.run.engine) as session, session.begin():
            store.record_checkpoint(
                session,
                job_id=self.ctx.job_id,
                # A trial's checkpoint belongs to its study, not to a lineage's retention.
                lineage_id=None if self.trial is not None else self.lineage_id,
                path=meta.relative_to(self.run.data_dir).as_posix(),
                size=size,
                global_step=p.global_step,
                epoch=p.epoch,
            )
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
        """Validation loss, and accuracy (classification) or RMSE on the target's scale."""
        regression = self.task == "regression"
        model.eval()
        total_loss, correct, seen = 0.0, 0, 0
        loader = _loader(
            self.val, batch=512, sampler=None, workers=self.workers, cuda=self.device == "cuda"
        )
        with torch.no_grad():
            for x, y in loader:
                if self.ctx.stop_request() == "cancel":  # a pause waits for the epoch's end
                    raise JobCancelled(self.ctx.job_id)
                x, y = x.to(self.device), y.to(self.device)
                logits = model(x)
                out = logits.squeeze(1) if regression else logits
                total_loss += float(loss_fn(out, y)) * len(y)
                if not regression:
                    correct += int((logits.argmax(1) == y).sum())
                seen += len(y)
        self._train_mode(model)
        loss = total_loss / seen
        return loss, self._rmse(loss) if regression else correct / seen

    def _train_mode(self, model: nn.Module) -> None:
        """Train mode, except frozen layers, which stay in eval mode so their BatchNorm
        statistics and dropout do not change (ADR-0014 Addendum A rule 7)."""
        model.train()
        for module in self.frozen:
            module.eval()

    def _epoch_zero(self, model: nn.Module, loss_fn: nn.Module) -> dict[str, Any]:
        """One validation pass before step 1, recorded as epoch 0 (RUN-1.10.AC1, rule 9)."""
        val_loss, val_score = self._evaluate(model, loss_fn)
        score_name = "rmse" if self.task == "regression" else "accuracy"
        record: dict[str, Any] = {
            "epoch": 0,
            "val_loss": round(val_loss, 5),
            f"val_{score_name}": round(val_score, 5),
        }
        if self.parent_val_loss is not None:
            versus = f"the parent's on its own split: {self.parent_val_loss:.4f}"
        elif self.config.reset_head:
            versus = "no parent's value to compare: the head is new"
        else:
            versus = "no parent's value to compare: another split"
        extra = (
            {"parent_val_loss": self.parent_val_loss} if self.parent_val_loss is not None else {}
        )
        self.ctx.emit(
            stage=STAGE,
            substage="validate",
            message=f"Epoch 0, before training: val loss {val_loss:.4f} ({versus})",
            metrics={**record, **extra},
        )
        return record

    def _start_from_parent(self, model: nn.Module) -> None:
        """Load the parent's weights, all but the head when it is reset, then freeze the
        chosen nodes (ADR-0014 Addendum A rules 5 and 7). A reset head keeps its
        initialisation from the run's seed."""
        from stratal.registry.store import WEIGHTS_FILE

        assert self.parent is not None and self.parent.weights_path is not None
        path = self.run.data_dir / self.parent.weights_path / WEIGHTS_FILE
        tensors = weights.read_tensors(path, suggested_action="Re-train the parent (REG-2.7).")
        head = _head_prefix(model)
        if self.config.reset_head:
            tensors = {k: v for k, v in tensors.items() if not k.startswith(head)}
        missing, unexpected = model.load_state_dict(tensors, strict=False)
        wrong = [k for k in missing if not (self.config.reset_head and k.startswith(head))]
        if wrong or unexpected:
            raise StratalError(
                f"The weights of {self.parent.id} do not fit this model: missing "
                f"{wrong[:5]}, unexpected {list(unexpected)[:5]}.",
                suggested_action="Fine-tune with the parent's own model spec.",
            )
        for node_id in self.config.freeze:
            graph = graph_of(model)
            module = graph.get_submodule("head" if node_id == "head" else f"nodes.{node_id}")
            for param in module.parameters():
                param.requires_grad_(False)
            self.frozen.append(module)
        frozen = sum(p.numel() for m in self.frozen for p in m.parameters())
        self.ctx.emit(
            stage=STAGE,
            substage="setup",
            message=f"Starting from {self.parent.id}'s weights"
            + (", head reset" if self.config.reset_head else "")
            + (
                f", {len(self.frozen)} layers frozen ({frozen:,} parameters)" if self.frozen else ""
            ),
            metrics={"init_from": str(self.parent.id), "frozen_parameters": frozen},
        )

    def _rmse(self, standardised_mse: float) -> float:
        """An MSE on the standardised target, as an RMSE on the target's own scale."""
        std = self.prep.target_std if self.prep is not None else 1.0
        return math.sqrt(max(standardised_mse, 0.0)) * std

    def _train_score(self, loss: float, accuracy: float) -> dict[str, float]:
        if self.task == "regression":
            return {"rmse": round(self._rmse(loss), 5)}
        return {"accuracy": round(accuracy, 5)}

    def _predict(self, model: nn.Module, dataset: Any) -> tuple[list[Any], list[Any]]:
        """Predictions and truths over a split, as classes or on the target's scale."""
        model.eval()
        truth: list[Any] = []
        guess: list[Any] = []
        loader = _loader(
            dataset, batch=512, sampler=None, workers=self.workers, cuda=self.device == "cuda"
        )
        with torch.no_grad():
            for x, y in loader:
                logits = model(x.to(self.device)).float().cpu()
                if self.task == "regression":
                    assert self.prep is not None
                    guess += [self.prep.unscale(float(v)) for v in logits.squeeze(1)]
                    truth += [self.prep.unscale(float(v)) for v in y]
                else:
                    guess += [self.classes[int(i)] for i in logits.argmax(1)]
                    truth += [self.classes[int(i)] for i in y]
        self._train_mode(model)
        return truth, guess

    def _final_scores(
        self, model: nn.Module, splits: tuple[str, ...] = ("val", "test")
    ) -> tuple[dict[str, MetricValue], dict[str, Any]]:
        """EVAL-1.2's metric set on validation and test, once the run has finished."""
        scalars: dict[str, MetricValue] = {"task": self.task}
        detail: dict[str, Any] = {"task": self.task}
        for split, dataset in (("val", self.val), ("test", self.test)):
            if split not in splits or len(dataset) == 0:
                continue
            truth, guess = self._predict(model, dataset)
            if self.task == "regression":
                r = task_metrics.regression(truth, guess)
                scalars.update(
                    {
                        f"{split}_mse": round(r.mse, 6),
                        f"{split}_rmse": round(math.sqrt(r.mse), 6),
                        f"{split}_mae": round(r.mae, 6),
                        f"{split}_r2": round(r.r2, 6),
                    }
                )
                detail[split] = {"mse": r.mse, "mae": r.mae, "r2": r.r2, "rows": len(truth)}
            else:
                m = task_metrics.classification(truth, guess, labels=self.classes)
                scalars[f"{split}_accuracy"] = round(m.accuracy, 6)
                scalars[f"{split}_macro_f1"] = round(m.macro_f1, 6)
                detail[split] = {
                    "accuracy": m.accuracy,
                    "macro_f1": m.macro_f1,
                    "per_class_f1": {str(k): v for k, v in m.per_class_f1.items()},
                    "confusion_matrix": m.confusion_matrix,
                    "labels": [str(label) for label in m.labels],
                    "rows": len(truth),
                }
        self.ctx.emit(
            stage=STAGE,
            substage="score",
            message=f"Scored on {' and '.join({'val': 'validation'}.get(s, s) for s in splits)}: "
            + ", ".join(f"{k} {v}" for k, v in scalars.items() if k != "task"),
            metrics=dict(scalars),
        )
        return scalars, detail

    # ------------------------------------------------------------------ registry
    def _meta(self, version_id: str, state: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        """``model.json`` beside the weights: enough to rebuild and trace the model."""
        return {
            "lineage_id": self.lineage_id,
            "version_id": version_id,
            "state": state,
            "parent_version_id": None if state == "base" else self.parent_id,
            "model": self.config.model.model_dump(mode="json"),
            "input_shape": list(self.input.dims),
            "input": self.input.as_json(),
            # A table's columns, so /predict can check a record (ADR-0019 rule 21).
            "table_schema": json.loads(schema_to_json(self.table_schema))
            if self.table_schema is not None
            else None,
            "task": self.task,
            "dataset_type": self.dtype,
            "classes": self.classes,
            "parameters": self.parameters,
            "dataset_version": self.run.input_version_id,
            "config": self.config.model_dump(mode="json"),
            "config_hash": self.hash,
            "history": history,
            **(
                {"trial": {"study_id": self.trial.study_id, "number": self.trial.number}}
                if self.trial is not None
                else {}
            ),
        }

    def _attach_lineage(self, model: nn.Module) -> None:
        """Find or create the run's lineage (ADR-0015 rule 1). A new lineage saves
        ``model``'s initial weights as its base; otherwise ``model`` is loaded with the
        base's weights, so every run of a lineage starts from the base (REG-2.1).

        The model is built from the seed either way, so the random state the run
        continues with does not depend on whether the lineage already existed.

        A fine-tune (ADR-0014 Addendum A rule 6) holds its parent's weights already: it
        never loads the base. Founding a lineage, it saves those starting weights as the
        base. Its version's parent is the version it started from, not the base."""
        config = self.config
        if self.trial is not None:
            self._attach_trial(model)
            return
        with Session(self.run.engine) as session, session.begin():
            lineage = store.ensure_lineage(
                session,
                model_spec=config.model.identity(),
                name=config.model.display,
                dataset_id=self.dataset_id,
                dataset_slug=self.dataset_slug,
                input_shape=self.input.dims,
                classes=self.classes,
                parameters=self.parameters,
                task=self.task,
            )
            self.lineage_id = lineage.id
            base = store.base_of(session, lineage.id)
            fine_tune = config.init_from is not None
            if base is None:
                self.base_id = store.new_version_id()
                size = weights.write(
                    self.run.data_dir / store.version_folder(lineage.id, self.base_id),
                    model,
                    self._meta(self.base_id, "base", []),
                    self._extra_files(),
                )
                store.add_base(
                    session,
                    lineage=lineage,
                    version_id=self.base_id,
                    job_id=self.ctx.job_id,
                    dataset_version_id=self.run.input_version_id,
                    config=config.model_dump(mode="json"),
                    config_hash=self.hash,
                    weights_bytes=size,
                    note=(
                        f"Starting weights of a fine-tune of {config.init_from}"
                        + (", head reset" if config.reset_head else "")
                        + f" (seed {config.seed}), saved before the first step"
                        if fine_tune
                        else f"Initial weights from seed {config.seed}, saved before the first step"
                    ),
                )
                message = (
                    f"New lineage {lineage.name}: base {self.base_id} saved before the first step"
                    + (
                        f" (the starting weights of a fine-tune of {config.init_from})"
                        if fine_tune
                        else ""
                    )
                )
            elif fine_tune:
                self.base_id = base.id
                message = f"Lineage {lineage.name}: fine-tuning {config.init_from}"
            else:
                if base.weights_path is None or base.weights_status != "present":
                    raise StratalError(
                        f"The base {base.id} of {lineage.name} has no weights on disk.",
                        suggested_action="Restore the data folder from a backup; base weights "
                        "are never deleted by retention (REG-2.1).",
                    )
                weights.load_into(model, self.run.data_dir / base.weights_path)
                self.base_id = base.id
                message = f"Lineage {lineage.name}: starting from base {base.id}"
        self.parent_id = config.init_from or self.base_id
        self.ctx.emit(
            stage=STAGE,
            substage="setup",
            message=message,
            metrics={
                "lineage_id": self.lineage_id,
                "base_version_id": self.base_id,
                "parent_version_id": self.parent_id,
            },
        )

    def _attach_trial(self, model: nn.Module) -> None:
        """A trial touches no lineage (ADR-0018 rule 7): it starts from its lineage's base if
        one exists, as any run would; otherwise its own initial weights go to its folder, so
        the study can make them the base of a new lineage if this trial turns out best."""
        assert self.trial is not None
        config = self.config
        lineage_id, _ = store.lineage_key(
            model_spec=config.model.identity(),
            dataset_slug=self.dataset_slug,
            input_shape=self.input.dims,
            classes=self.classes,
        )
        with Session(self.run.engine) as session:
            base = store.base_of(session, lineage_id)
            path = (
                base.weights_path if base is not None and base.weights_status == "present" else None
            )
            base_id = base.id if base is not None and path is not None else None
        self.parent_id = config.init_from or base_id
        if config.init_from is not None and path is not None:
            # A fine-tuning trial holds its parent's weights already (Addendum A rule 13).
            self.lineage_id, self.base_id = lineage_id, base_id
            message = f"Trial {self.trial.number}: fine-tuning {config.init_from}"
        elif path is not None:
            weights.load_into(model, self.run.data_dir / path)
            self.lineage_id, self.base_id = lineage_id, base_id
            message = f"Trial {self.trial.number}: starting from base {base_id}"
        else:
            self.lineage_id, self.base_id = lineage_id, None  # the lineage it would found
            weights.write(
                self.trial.folder / "initial",
                model,
                self._meta("initial", "base", []),
                self._extra_files(),
            )
            message = (
                f"Trial {self.trial.number}: "
                + (
                    f"the starting weights of a fine-tune of {config.init_from}"
                    if config.init_from is not None
                    else f"initial weights from seed {config.seed}"
                )
                + " kept with the trial; the lineage is created only if it is the study's best"
            )
        self.ctx.emit(
            stage=STAGE,
            substage="setup",
            message=message,
            metrics={"trial": self.trial.number, "study_id": self.trial.study_id},
        )

    def _record_partial(self, history: list[dict[str, Any]]) -> None:
        """At a pause: the run's version is ``partial``; its weights are the checkpoint."""
        if self.trial is not None:
            return  # a trial's progress is its checkpoint and its study's trial row
        with Session(self.run.engine) as session, session.begin():
            version_id = store.record_run(
                session,
                lineage_id=self.lineage_id,
                job_id=self.ctx.job_id,
                state="partial",
                parent_version_id=self.parent_id,
                dataset_version_id=self.run.input_version_id,
                config=self.config.model_dump(mode="json"),
                config_hash=self.hash,
                history=history,
            ).id
        self.ctx.emit(
            stage=STAGE,
            substage="registry",
            message=f"Recorded {version_id} as partial",
            metrics={"model_version_id": version_id},
        )

    def _extra_files(self) -> dict[str, str]:
        """A table network's fitted preprocessing, beside its weights (ADR-0019 rule 13)."""
        if self.prep is None:
            return {}
        return {table_prep.PREPROCESS_FILE: self.prep.to_json()}

    def _register(
        self,
        model: nn.Module,
        history: list[dict[str, Any]],
        metrics: dict[str, MetricValue],
        detail: dict[str, Any] | None = None,
    ) -> str:
        """Write the trained weights, then mark the version ``trained`` (REG-1.1). The
        row is committed only once the files are in place; checkpoints go after."""
        if self.trial is not None:  # to the trial's folder; the study registers the best
            size = weights.write(
                self.trial.folder / "trained",
                model,
                self._meta("trained", "trained", history),
                self._extra_files(),
            )
            metrics["weights_bytes"] = size
            result = {"metrics": dict(metrics), "detail": detail, "history": history}
            partial = self.trial.folder / (TRIAL_RESULT + ".part")
            partial.write_text(json.dumps(result), encoding="utf-8")
            os.replace(partial, self.trial.folder / TRIAL_RESULT)
            return f"trial {self.trial.number}"
        with Session(self.run.engine) as session, session.begin():
            store.lock_lineage(session, self.lineage_id)
            existing = store.run_version(session, self.ctx.job_id)
            version_id = existing.id if existing is not None else store.new_version_id()
            size = weights.write(
                self.run.data_dir / store.version_folder(self.lineage_id, version_id),
                model,
                self._meta(version_id, "trained", history),
                self._extra_files(),
            )
            metrics["weights_bytes"] = size
            store.record_run(
                session,
                lineage_id=self.lineage_id,
                job_id=self.ctx.job_id,
                state="trained",
                parent_version_id=self.parent_id,
                dataset_version_id=self.run.input_version_id,
                config=self.config.model_dump(mode="json"),
                config_hash=self.hash,
                history=history,
                metrics={**metrics, "detail": detail} if detail is not None else dict(metrics),
                version_id=version_id,
                weights_bytes=size,
                trained_at=utc_now(),
            )
        return version_id

    def _remove_checkpoints(self) -> int:
        freed = ckpt.remove_all(self.folder)
        with Session(self.run.engine) as session, session.begin():
            store.drop_checkpoint(session, self.ctx.job_id)
        return freed

    def already_registered(self) -> StageResult | None:
        """On a retry of a run that registered its weights before the process died:
        finish the cleanup and return the recorded result, training nothing."""
        if self.trial is not None:
            found = self.trial.folder / TRIAL_RESULT
            if not found.is_file():
                return None
            result = json.loads(found.read_text(encoding="utf-8"))
            self._remove_checkpoints()
            self.trial.complete(result["history"][-1])
            scalars = {
                k: v for k, v in result["metrics"].items() if isinstance(v, int | float | str)
            }
            return StageResult(metrics=scalars)
        with Session(self.run.engine) as session:
            version = store.run_version(session, self.ctx.job_id)
            if version is None or version.state != "trained" or version.weights_status != "present":
                return None
            version_id = version.id
            # Scalars only: the version's metrics also hold EVAL-1.2's detail (a dict).
            metrics: dict[str, MetricValue] = {
                k: v for k, v in (version.metrics or {}).items() if isinstance(v, int | float | str)
            }
        freed = self._remove_checkpoints()
        self.ctx.emit(
            stage=STAGE,
            substage="finish",
            message=f"Run already registered as {version_id}; checkpoints removed "
            f"({freed / MB:.1f} MB)",
            metrics={"model_version_id": version_id},
        )
        return StageResult(metrics=metrics)

    def _oom(self, optimizer: Any) -> StratalError:
        """The suggested batch (RUN-1.5): what stays allocated once the step's tensors and
        gradients are gone is fixed; the rest of the step's peak is per sample."""
        optimizer.zero_grad(set_to_none=True)
        gc.collect()
        peak = torch.cuda.max_memory_allocated() / MB
        total = torch.cuda.get_device_properties(0).total_memory / MB
        cap = total * VRAM_FRACTION
        fixed = torch.cuda.memory_allocated() / MB
        torch.cuda.empty_cache()
        batch = self.config.batch_size
        per_sample = max(peak - fixed, 0.0) / batch
        suggested = self.units.suggest(
            failed_batch=batch, fixed_mb=fixed, per_sample_mb=per_sample, cap_mb=cap
        )
        return self.units.oom_error(
            failed_batch=batch, suggested=suggested, peak_mb=peak, cap_mb=cap
        )


def run_test_eval(run: StageRun, units: TrainUnits = OWNER_UNITS) -> StageResult:
    """A deep-learning study's ``test_eval`` stage: the registered best version scored on
    the test split, once (HPO-1.4, ADR-0018 rule 8). A retry after the write returns
    what was written; the database refuses a second write either way."""
    from stratal.db.models import ModelVersion
    from stratal.hpo import store as hpo_store

    if run.config.get("model_version_id"):
        return _score_version(run, str(run.config["model_version_id"]), units)
    study_id = str(run.config["study_id"])
    with Session(run.engine) as session:
        study = hpo_store.get_study(session, study_id)
        kept, best = study.test_metrics, study.best_version_id
        version = session.get(ModelVersion, best) if best is not None else None
        found = (
            (dict(version.config), version.weights_path, version.dataset_version_id)
            if version is not None and version.weights_path is not None
            else None
        )
    if kept is not None:
        return StageResult(metrics={k: v for k, v in kept.items() if isinstance(v, int | float)})
    if found is None:
        hpo_store.update_study(run.engine, study_id, state="completed", finished_at=utc_now())
        run.ctx.emit(stage="test_eval", message="No best version to test: no trial completed")
        return StageResult(metrics={"tested": 0})
    config_json, weights_path, dataset_version = found
    config = TrainConfig.model_validate(config_json)
    training = _Run(replace(run, input_version_id=dataset_version), config, units)
    training.prepare()
    model = with_table_input(
        build_model(config.model, training.input, training.outputs), training.prep
    )
    weights.load_into(model, run.data_dir / weights_path)
    if training.device == "cuda":
        with gpu_lease(run.engine, holder=f"{run.ctx.job_id}/test_eval", wait_s=60):
            torch.cuda.set_per_process_memory_fraction(VRAM_FRACTION)
            scalars, detail = training._final_scores(model.to("cuda"), ("test",))
            del model
            torch.cuda.empty_cache()
    else:
        scalars, detail = training._final_scores(model, ("test",))
    test = {k: v for k, v in scalars.items() if k.startswith("test_")}
    wrote = hpo_store.record_test_once(run.engine, study_id, {**test, "detail": detail.get("test")})
    if wrote:
        with Session(run.engine) as session, session.begin():
            row = session.get(ModelVersion, best)
            assert row is not None
            metrics = dict(row.metrics or {})
            row.metrics = {
                **metrics,
                **test,
                "detail": {**dict(metrics.get("detail") or {}), "test": detail.get("test")},
            }
    hpo_store.update_study(run.engine, study_id, state="completed", finished_at=utc_now())
    run.ctx.emit(
        stage="test_eval",
        message=f"Best version {best} tested once: "
        + ", ".join(f"{k} {v}" for k, v in test.items()),
        metrics={"model_version_id": str(best), **test},
    )
    return StageResult(metrics={**test, "model_version_id": str(best)})


def _score_version(run: StageRun, version_id: str, units: TrainUnits) -> StageResult:
    """Score on test (M11, EVAL-1.3): one version without test metrics, scored once on its
    own split's test rows. The write is conditional, so a retry or a second job never
    scores it twice."""
    from stratal.db.models import ModelVersion
    from stratal.registry.compare import test_metrics

    with Session(run.engine) as session:
        version = session.get(ModelVersion, version_id)
        if version is None or version.weights_path is None or version.dataset_version_id is None:
            raise StratalError(
                f"{version_id} has no weights to score.",
                suggested_action="Re-train it (REG-2.7).",
            )
        if test_metrics(version):
            kept = {f"test_{k}": v for k, v in test_metrics(version).items()}
            return StageResult(metrics={**kept, "model_version_id": version_id})
        config_json = dict(version.config)
        weights_path, dataset_version = version.weights_path, version.dataset_version_id
    # The weights hold everything; the parent of a fine-tune is not read again.
    config = TrainConfig.model_validate(config_json).model_copy(
        update={"init_from": None, "reset_head": False, "freeze": []}
    )
    training = _Run(replace(run, input_version_id=dataset_version), config, units)
    training.prepare()
    model = with_table_input(
        build_model(config.model, training.input, training.outputs), training.prep
    )
    weights.load_into(model, run.data_dir / weights_path)
    if training.device == "cuda":
        with gpu_lease(run.engine, holder=f"{run.ctx.job_id}/test_eval", wait_s=60):
            torch.cuda.set_per_process_memory_fraction(VRAM_FRACTION)
            scalars, detail = training._final_scores(model.to("cuda"), ("test",))
            del model
            torch.cuda.empty_cache()
    else:
        scalars, detail = training._final_scores(model, ("test",))
    test = {k: v for k, v in scalars.items() if k.startswith("test_")}
    with Session(run.engine) as session, session.begin():
        row = session.get(ModelVersion, version_id, with_for_update=True)
        assert row is not None
        if not test_metrics(row):
            metrics = dict(row.metrics or {})
            row.metrics = {
                **metrics,
                **test,
                "task": scalars.get("task", metrics.get("task")),
                "detail": {**dict(metrics.get("detail") or {}), "test": detail.get("test")},
            }
    run.ctx.emit(
        stage="test_eval",
        message=f"{version_id} scored on test once: "
        + ", ".join(f"{k} {v}" for k, v in test.items()),
        metrics={"model_version_id": version_id, **test},
    )
    return StageResult(metrics={**test, "model_version_id": version_id})


def run_train(run: StageRun, units: TrainUnits = OWNER_UNITS) -> StageResult:
    """Run one training stage (RUN-1.1). ``units`` are the owner's; tests pass fakes."""
    config = TrainConfig.model_validate(run.config)
    training = _Run(run, config, units)
    finished = training.already_registered()
    if finished is not None:
        return finished
    training.prepare()
    return training.execute()
