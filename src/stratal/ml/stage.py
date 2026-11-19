"""The ``train_classical`` stage: one classical model on a tabular split (ML-1.1 to ML-1.5).

ADR-0019 part D. Imported only in the workers, where scikit-learn is. The stage:

1. reads the split (:func:`stratal.ml.data.load_tabular`) and checks the rules
   that depend on the data (ML-1.4), as the API did before queueing;
2. finds or creates the lineage (one estimator on one dataset, rule 19); a new
   lineage's base is the unfitted pipeline, saved before fitting (INV-13);
3. fits the pipeline on the train split only (ML-1.3), and scores it with
   EVAL-1.2 on validation and test (supervised) or on train (clustering, PCA);
4. saves it with skops (ML-1.5) and records a ``trained`` version (REG-1.1).

A retry of a run that registered before its process died finishes without
fitting again, as the network stage does.
"""

import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from stratal.core.errors import ValidationFailed
from stratal.data import registry
from stratal.data.records import schema_to_json
from stratal.db.models import Dataset
from stratal.ml import persist
from stratal.ml.data import TabularData, load_tabular
from stratal.ml.params import ClassicalConfig, check_data, task_for
from stratal.ml.pipeline import build_pipeline, fit, score
from stratal.mon.events import MetricValue, utc_now
from stratal.registry import store
from stratal.runtime.jobs import config_hash
from stratal.runtime.stages import StageResult, StageRun

STAGE = "train_classical"


def _write_meta(folder: Path, meta: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / store.META_FILE).write_text(
        json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8"
    )


def _folder_bytes(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())


class _Classical:
    def __init__(self, run: StageRun, config: ClassicalConfig) -> None:
        self.run = run
        self.ctx = run.ctx
        self.config = config
        self.spec = config.model.resolved()
        self.hash = config_hash(config.model_dump(mode="json"))

    def already_registered(self) -> StageResult | None:
        with Session(self.run.engine) as session:
            version = store.run_version(session, self.ctx.job_id)
            if version is None or version.state != "trained" or version.weights_status != "present":
                return None
            metrics: dict[str, MetricValue] = {
                k: v for k, v in (version.metrics or {}).items() if isinstance(v, int | float | str)
            }
            metrics["model_version_id"] = version.id
        self.ctx.emit(stage=STAGE, message=f"Run already registered as {version.id}")
        return StageResult(metrics=metrics)

    def load(self) -> TabularData:
        version_id = self.run.input_version_id
        if version_id is None:
            raise ValidationFailed("The train_classical stage was given no dataset version.")
        with Session(self.run.engine) as session:
            version, manifest = registry.manifest_for(session, self.run.data_dir, version_id)
            dataset = session.get(Dataset, version.dataset_id)
            assert dataset is not None
            self.dataset_id, self.dataset_slug = dataset.id, dataset.slug
        data = load_tabular(manifest)
        problems = check_data(self.spec, data.facts())
        if problems:
            field, reason = problems[0]
            raise ValidationFailed(
                f"{self.spec.estimator}: {field}: {reason} (ML-1.4).",
                suggested_action=f"Lower {field}, or train on a larger split.",
            )
        self.task = task_for(self.spec.estimator, data.task)
        return data

    def meta(
        self, data: TabularData, version_id: str, state: str, extra: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "kind": "sklearn",
            "lineage_id": self.lineage_id,
            "version_id": version_id,
            "state": state,
            "model": self.spec.model_dump(mode="json"),
            "task": self.task,
            "features": {"numeric": data.numeric, "categorical": data.categorical},
            "classes": data.classes,
            "table_schema": json.loads(schema_to_json(data.schema)),
            "dataset_version": self.run.input_version_id,
            "config": self.config.model_dump(mode="json"),
            "config_hash": self.hash,
            "file": persist.MODEL_FILE,
            **extra,
        }

    def attach_lineage(self, data: TabularData) -> None:
        """The lineage, and its base: the unfitted pipeline (REG-2.1 for classical models)."""
        with Session(self.run.engine) as session, session.begin():
            lineage = store.ensure_lineage(
                session,
                model_spec=self.spec.identity(),
                name=self.spec.display,
                dataset_id=self.dataset_id,
                dataset_slug=self.dataset_slug,
                input_shape=[len(data.features)],
                classes=data.classes,
                parameters=0,
                task=self.task,
            )
            self.lineage_id = lineage.id
            base = store.base_of(session, lineage.id)
            if base is None:
                self.base_id = store.new_version_id()
                folder = self.run.data_dir / store.version_folder(lineage.id, self.base_id)
                persist.save(build_pipeline(self.spec, data, self.config.seed), folder)
                _write_meta(folder, self.meta(data, self.base_id, "base", {}))
                store.add_base(
                    session,
                    lineage=lineage,
                    version_id=self.base_id,
                    job_id=self.ctx.job_id,
                    dataset_version_id=self.run.input_version_id,
                    config=self.config.model_dump(mode="json"),
                    config_hash=self.hash,
                    weights_bytes=_folder_bytes(folder),
                    note="The unfitted pipeline, saved before fitting",
                )
                message = f"New lineage {lineage.name}: base {self.base_id} (unfitted pipeline)"
            else:
                self.base_id = base.id
                message = f"Lineage {lineage.name}: base {base.id}"
        self.ctx.emit(
            stage=STAGE,
            substage="setup",
            message=message,
            metrics={"lineage_id": self.lineage_id, "base_version_id": self.base_id},
        )

    def execute(self) -> StageResult:
        data = self.load()
        sizes = {split: len(part) for split, part in data.splits.items()}
        self.ctx.emit(
            stage=STAGE,
            substage="setup",
            message=f"{self.spec.estimator} for {self.task} on {sizes['train']:,} train rows · "
            f"{len(data.numeric)} numeric and {len(data.categorical)} categorical columns",
            metrics={f"{k}_rows": v for k, v in sizes.items()},
        )
        self.attach_lineage(data)
        started = time.monotonic()
        self.ctx.emit(stage=STAGE, substage="fit", message="Fitting on the train split")
        pipeline = fit(build_pipeline(self.spec, data, self.config.seed), data, self.spec)
        fit_s = round(time.monotonic() - started, 3)
        self.ctx.raise_if_cancelled()
        splits = ("val", "test") if self.task in ("classification", "regression") else ("train",)
        scores = score(pipeline, data, self.spec, splits)
        metrics: dict[str, MetricValue] = {**scores.scalars, "fit_s": fit_s}
        self.ctx.emit(
            stage=STAGE,
            substage="score",
            message="Scored: "
            + ", ".join(f"{k} {v}" for k, v in scores.scalars.items() if k != "task"),
            metrics=metrics,
        )
        version_id = self.register(pipeline, data, metrics, scores.detail)
        metrics["model_version_id"] = version_id
        return StageResult(metrics=metrics)

    def register(
        self,
        pipeline: Any,
        data: TabularData,
        metrics: dict[str, MetricValue],
        detail: dict[str, Any],
    ) -> str:
        with Session(self.run.engine) as session, session.begin():
            store.lock_lineage(session, self.lineage_id)
            existing = store.run_version(session, self.ctx.job_id)
            version_id = existing.id if existing is not None else store.new_version_id()
            folder = self.run.data_dir / store.version_folder(self.lineage_id, version_id)
            persist.save(pipeline, folder)
            _write_meta(folder, self.meta(data, version_id, "trained", {"metrics": detail}))
            size = _folder_bytes(folder)
            metrics["weights_bytes"] = size
            store.record_run(
                session,
                lineage_id=self.lineage_id,
                job_id=self.ctx.job_id,
                state="trained",
                parent_version_id=self.base_id,
                dataset_version_id=self.run.input_version_id,
                config=self.config.model_dump(mode="json"),
                config_hash=self.hash,
                history=[],
                metrics={**metrics, "detail": detail},
                version_id=version_id,
                weights_bytes=size,
                trained_at=utc_now(),
            )
        self.ctx.emit(
            stage=STAGE,
            substage="registry",
            message=f"Registered {version_id} in {self.lineage_id} ({size / 1024:.0f} KB, skops)",
            metrics={"model_version_id": version_id},
        )
        return version_id


def run_train_classical(run: StageRun) -> StageResult:
    config = ClassicalConfig.model_validate(run.config)
    training = _Classical(run, config)
    finished = training.already_registered()
    if finished is not None:
        return finished
    return training.execute()
