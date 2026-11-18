# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU and ML images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""Register training runs that finished before the registry existed (M8, REG-1.1).

Those runs left their weights in ``jobs/<job_id>/weights/`` and saved no base.
For each completed train job without a version, oldest first:

- the lineage is found or created from the run's ``model.json``;
- a missing base is rebuilt from the run's seed exactly as the stage builds a
  model (Python's and PyTorch's random state seeded, the model built on the
  CPU), which reproduces the run's initial weights bit for bit;
- the run's own initial weights are rebuilt the same way and compared with the
  base: the base is recorded as its parent only if they are identical;
- the weights folder is moved into ``registry/`` and the version recorded in
  the same transaction; if recording fails, the folder is moved back.
"""

import json
import os
import random
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import load_file
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.db.models import Dataset, DatasetVersion, Job, JobStage
from stratal.registry import store
from stratal.train import weights
from stratal.train.config import TrainConfig
from stratal.train.reference import build_model


def _initial(config: TrainConfig, shape: tuple[int, int, int], classes: int) -> torch.nn.Module:
    random.seed(config.seed)
    torch.manual_seed(config.seed)
    return build_model(config.model, shape, classes)


def _same(a: dict[str, torch.Tensor], b: dict[str, torch.Tensor]) -> bool:
    return a.keys() == b.keys() and all(torch.equal(a[k], b[k]) for k in a)


def backfill(engine: Engine, data_dir: Path, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Register every finished training run that has weights but no version."""
    with Session(engine) as session:
        jobs = session.scalars(
            select(Job).where(Job.kind == "train", Job.state == "completed").order_by(Job.id)
        ).all()
        pending = [j.id for j in jobs if store.run_version(session, j.id) is None]
    planned: dict[str, tuple[str, dict[str, torch.Tensor]]] = {}
    return [_one(engine, data_dir, job_id, planned, dry_run=dry_run) for job_id in pending]


def _one(
    engine: Engine,
    data_dir: Path,
    job_id: str,
    planned: dict[str, tuple[str, dict[str, torch.Tensor]]],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Register one run. ``planned`` carries a dry run's would-be bases between runs."""
    folder = data_dir / "jobs" / job_id / "weights"
    meta_path = folder / store.META_FILE
    if not meta_path.is_file() or not (folder / store.WEIGHTS_FILE).is_file():
        return {"job_id": job_id, "skipped": "no weights in jobs/<id>/weights/"}
    original = meta_path.read_text(encoding="utf-8")
    meta = json.loads(original)
    config = TrainConfig.model_validate(meta["config"])
    shape = tuple(meta["input_shape"])
    classes: list[str] = meta["classes"]
    own_start = _initial(config, shape, len(classes))

    with Session(engine) as session:
        job = session.get(Job, job_id)
        stage = session.get(JobStage, (job_id, 0))
        dataset_version = session.get(DatasetVersion, meta["dataset_version"])
        assert job is not None and stage is not None and dataset_version is not None
        dataset = session.get(Dataset, dataset_version.dataset_id)
        assert dataset is not None
        lineage = store.ensure_lineage(
            session,
            model_spec=config.model.model_dump(mode="json"),
            dataset_id=dataset.id,
            dataset_slug=dataset.slug,
            input_shape=shape,
            classes=classes,
            parameters=int(meta["parameters"]),
            task="classification",
        )
        base = store.base_of(session, lineage.id)
        if base is None and lineage.id in planned:  # a dry run's base from an earlier run
            base_id, saved = planned[lineage.id]
            from_base = _same(own_start.state_dict(), saved)
        elif base is None:
            base_id = store.new_version_id()
            planned[lineage.id] = (base_id, own_start.state_dict())
            from_base = True
        else:
            assert base.weights_path is not None
            base_id = base.id
            saved = load_file(str(data_dir / base.weights_path / store.WEIGHTS_FILE))
            from_base = _same(own_start.state_dict(), saved)
        if base is None and not dry_run:
            base_bytes = weights.write(
                data_dir / store.version_folder(lineage.id, base_id),
                own_start,
                {
                    **meta,
                    "lineage_id": lineage.id,
                    "version_id": base_id,
                    "state": "base",
                    "parent_version_id": None,
                    "history": [],
                },
            )
            store.add_base(
                session,
                lineage=lineage,
                version_id=base_id,
                job_id=job_id,
                dataset_version_id=dataset_version.version_id,
                config=meta["config"],
                config_hash=meta["config_hash"],
                weights_bytes=base_bytes,
                note=f"Rebuilt by the registry backfill from seed {config.seed}: {job_id}'s "
                "initial weights, which runs did not save before the registry existed",
                created_at=job.started_at,
            )
        version_id = store.new_version_id()
        note = (
            None
            if from_base
            else f"Trained before the registry from seed {config.seed}'s initial weights, "
            "not from the lineage's base, so it has no parent"
        )
        report = {
            "job_id": job_id,
            "lineage_id": lineage.id,
            "lineage": lineage.name,
            "version_id": version_id,
            "base_version_id": base_id,
            "parent_version_id": base_id if from_base else None,
            "note": note,
        }
        if dry_run:
            session.rollback()
            return {**report, "dry_run": True}
        target = data_dir / store.version_folder(lineage.id, version_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(folder, target)
        try:
            (target / store.META_FILE).write_text(
                json.dumps(
                    {
                        **meta,
                        "lineage_id": lineage.id,
                        "version_id": version_id,
                        "state": "trained",
                        "parent_version_id": report["parent_version_id"],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            size = weights.size(target)
            version = store.record_run(
                session,
                lineage_id=lineage.id,
                job_id=job_id,
                state="trained",
                parent_version_id=report["parent_version_id"],
                dataset_version_id=dataset_version.version_id,
                config=meta["config"],
                config_hash=meta["config_hash"],
                history=meta["history"],
                metrics={**(stage.metrics or {}), "weights_bytes": size},
                version_id=version_id,
                weights_bytes=size,
                note=note,
                trained_at=job.finished_at,
            )
            if job.finished_at is not None:
                version.created_at = job.finished_at
            session.commit()
        except BaseException:
            session.rollback()
            (target / store.META_FILE).write_text(original, encoding="utf-8")
            os.replace(target, folder)
            raise
    return {
        **report,
        "weights_bytes": size,
        "moved_to": store.version_folder(report["lineage_id"], version_id),
    }
