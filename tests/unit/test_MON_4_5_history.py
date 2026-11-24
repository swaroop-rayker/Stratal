"""MON-4.5: the rules behind SCR-13's History tab (stratal.mon.history), without a database."""

from typing import Any

import pytest

from stratal.build.templates import BUILTINS
from stratal.mon import history as hist


def _graph(index: int = 0, label: str | None = None) -> dict[str, Any]:
    spec = BUILTINS[index].spec
    if label is not None:
        spec = spec.model_copy(update={"label": label})
    return spec.model_dump(mode="json")


def _train(**over: Any) -> hist.StageRun:
    fields: dict[str, Any] = {
        "job_id": "job_0001",
        "kind": "train",
        "stage": "train",
        "queue": "gpu",
        "started_at": None,
        "config": {"model": {"name": "small_cnn"}, "batch_size": 128, "epochs": 10},
        "metrics": {},
        "peaks": {"vram_used_mb": 758.0, "ram_used_mb": 4262.0},
        "throughput": 82.0,
        "stage_seconds": 30.0,
        "running_s": 60.0,
        "dataset_slug": "cifar10",
    }
    fields.update(over)
    return hist.StageRun(**fields)


def test_MON_4_5_training_rate_is_samples_a_second_while_training() -> None:
    assert hist.rate(_train()) == 82.0 * 128
    assert hist.rate(_train(throughput=None)) is None


def test_MON_4_5_time_is_running_time_across_attempts_per_epoch() -> None:
    value, unit = hist.time_value(_train())
    assert (value, unit) == (6.0, "s/epoch"), (
        "60 s running over 10 epochs, not the last attempt's 30 s"
    )
    pruned = _train(metrics={"pruned_at_step": 2})
    assert hist.time_value(pruned)[0] == 30.0, "a pruned trial ran 2 epochs"
    assert hist.time_value(_train(running_s=None))[0] == 3.0, (
        "without attempts, the stage's own time"
    )


def test_MON_4_5_label_rate_counts_captions_computed_not_reused() -> None:
    label = hist.StageRun(
        job_id="job_0002",
        kind="pipeline",
        stage="label",
        queue="gpu",
        started_at=None,
        config={"captioner": "florence-2-base", "caption_batch_size": 16},
        metrics={"images": 1991, "captions_computed": 1991},
        stage_seconds=236.8,
        peaks={"vram_used_mb": 4202.0},
    )
    assert hist.rate(label) == pytest.approx(8.41)
    reused = hist.StageRun(
        job_id="job_0003",
        kind="pipeline",
        stage="label",
        queue="gpu",
        started_at=None,
        metrics={"images": 994, "captions_computed": 0},
        stage_seconds=11.7,
        peaks={"vram_used_mb": 221.0},
    )
    assert hist.rate(reused) is None
    assert "reused_work" in hist.notes(reused)


def test_MON_4_5_other_stages_use_their_own_rate_or_none() -> None:
    def run(stage: str, metrics: dict[str, Any]) -> hist.StageRun:
        return hist.StageRun(
            job_id="j",
            kind="pipeline",
            stage=stage,
            queue="cpu",
            started_at=None,
            metrics=metrics,
            stage_seconds=2.0,
        )

    assert hist.rate(run("ingest", {"files_per_s": 1616.5})) == 1616.5
    assert hist.rate(run("preprocess", {"images_per_s": 120.0})) == 120.0
    assert hist.rate(run("split", {"rows": 100})) is None
    assert hist.RATE_UNITS.get("split") is None
    assert (
        hist.rate(
            hist.StageRun(
                job_id="j",
                kind="pipeline",
                stage="ingest",
                queue="cpu",
                started_at=None,
                metrics={"files_per_s": 9.0},
                cache_hit=True,
            )
        )
        is None
    )


def test_MON_4_5_notes_explain_unusual_runs() -> None:
    odd = _train(
        paused=True,
        worker_lost=True,
        config={"init_from": "mv_x", "batch_size": 128},
        metrics={"pruned_at_step": 2},
        peaks={},
    )
    assert hist.notes(odd) == ["paused", "resumed", "fine_tune", "pruned", "peaks_missing"]
    assert hist.notes(_train()) == []


def test_MON_4_5_groups_follow_the_structure_not_its_label() -> None:
    a = _train(
        config={
            "model": {"name": "small_cnn", "graph": _graph(label="small_cnn")},
            "batch_size": 128,
        }
    )
    b = _train(
        config={"model": {"name": "renamed", "graph": _graph(label="renamed")}, "batch_size": 128}
    )
    assert hist.group(a)[0] == hist.group(b)[0], "a renamed spec stays in its group"
    other_batch = _train(config={"model": a.config["model"], "batch_size": 256})
    assert hist.group(other_batch)[0] != hist.group(a)[0]
    reference = _train()
    assert hist.group(reference)[0] != hist.group(a)[0]
    assert hist.group(reference)[1] == "small_cnn (reference) · cifar10 · batch 128"
    named = _train(config=a.config, lineage_name="small_cnn · cifar10")
    assert hist.group(named)[1] == "small_cnn · cifar10 · batch 128"


def test_MON_4_5_label_groups_by_captioner_and_batch_other_stages_are_one_group() -> None:
    def label(batch: int) -> hist.StageRun:
        return hist.StageRun(
            job_id="j",
            kind="pipeline",
            stage="label",
            queue="gpu",
            started_at=None,
            config={"captioner": "florence-2-base", "caption_batch_size": batch},
        )

    assert hist.group(label(16))[1] == "florence-2-base · batch 16"
    assert hist.group(label(16))[0] != hist.group(label(8))[0]
    split = hist.StageRun(job_id="j", kind="pipeline", stage="split", queue="cpu", started_at=None)
    assert hist.group(split)[1] == "all runs"


def test_MON_4_5_median_ignores_missing_values() -> None:
    assert hist.median([6.46, 5.79, 18.87, None, 7.12]) == pytest.approx(6.79)
    assert hist.median([None]) is None
