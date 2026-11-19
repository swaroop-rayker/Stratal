# pyright: basic, reportMissingImports=false
"""REG-2.1 to REG-2.11, NFR-14, NFR-15, BUG7: weight retention end to end.

Real training runs (a 1-epoch MLP on the CPU), real retention jobs (queued by
the supervisor after every run, or by the API), real files in the test data
folder. Needs PyTorch for the runs, so it runs in the GPU worker's image.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytest.importorskip("torch")

from stratal.core.bus import Bus
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.registry import retention
from stratal.runtime.context import CRASH_AT_ENV
from stratal.testing.harness import Runner, wait_until
from stratal.testing.labelled import labelled_split
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.torch, pytest.mark.usefixtures("fake_units")]

CONFIG: dict[str, Any] = {
    "model": {"name": "mlp"},
    "epochs": 1,
    "batch_size": 8,
    "device": "cpu",
    "loader_workers": 0,
    "seed": 3,
}


class Registry:
    """One labelled dataset, the API, and helpers to train and inspect a lineage."""

    def __init__(self, runner: Runner, tmp_path: Path, slug: str) -> None:
        self.runner = runner
        self.api = PipelineApi(runner)
        self.version = labelled_split(runner, tmp_path / "src", slug)
        self.data = get_settings().data_dir

    def flush(self) -> None:
        """Run everything the API queued, and what that queues (retention after a run)."""
        self.runner.queue.extend(self.api.queued)
        self.api.queued.clear()
        self.runner.run_queued()
        self.runner.collect()

    def get(self, path: str) -> Any:
        response = self.api.client.get(f"/api/v1{path}")
        assert response.status_code == 200, response.text
        return response.json()

    def post(self, path: str, body: dict[str, Any] | None = None, status: int = 200) -> Any:
        response = self.api.client.post(f"/api/v1{path}", json=body)
        assert response.status_code == status, response.text
        return response.json()

    def start(self, **config: Any) -> str:
        body = {"kind": "train", "dataset_version": self.version, "config": {**CONFIG, **config}}
        return str(self.post("/jobs", body, status=202)["id"])

    def train(self, **config: Any) -> str:
        """One completed run; returns its version ID."""
        job_id = self.start(**config)
        self.flush()
        job = self.get(f"/jobs/{job_id}")
        assert job["state"] == "completed", job
        return str(job["model_version_id"])

    def lineage_of(self, version_id: str) -> str:
        return str(self.get(f"/model-versions/{version_id}")["lineage_id"])

    def versions(self, lineage_id: str) -> dict[str, dict[str, Any]]:
        return {v["id"]: v for v in self.get(f"/models/{lineage_id}")["versions"]}

    def base(self, lineage_id: str) -> str:
        return next(i for i, v in self.versions(lineage_id).items() if v["state"] == "base")

    def on_disk(self, lineage_id: str) -> set[str]:
        folder = self.data / "registry" / lineage_id
        return {p.name for p in folder.iterdir()} if folder.is_dir() else set()

    def settings(self) -> dict[str, Any]:
        sections = self.get("/settings")["sections"]
        return next(s for s in sections if s["name"] == "retention")

    def save(self, values: dict[str, Any], *, confirm: bool = True) -> Any:
        """PUT the retention section; confirms the plan if asked to."""
        section = self.settings()
        body = {"section": "retention", "values": values, "version": section["version"]}
        response = self.api.client.put("/api/v1/settings", json=body)
        if response.status_code == 409 and confirm:
            token = response.json()["plan"]["token"]
            response = self.api.client.put("/api/v1/settings", json={**body, "confirm": token})
        self.flush()
        return response

    def keep(self, lineage_id: str, keep: int | str, *, confirm: bool = True) -> Any:
        values = self.settings()["values"]
        return self.save(
            {**values, "overrides": {**values["overrides"], lineage_id: keep}}, confirm=confirm
        )

    def retention_jobs(self) -> list[dict[str, Any]]:
        return self.get("/jobs?kind=retention&limit=200")["items"]

    def disk(self, lineage_id: str) -> dict[str, Any]:
        return self.get(f"/models/{lineage_id}/disk")


def _du(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


@pytest.fixture
def reg(runner: Runner, tmp_path: Path, request: pytest.FixtureRequest) -> Registry:
    slug = "r" + request.node.name.removeprefix("test_").lower().replace("_", "")[:28]
    return Registry(runner, tmp_path, slug)


def test_REG_2_2_default_settings_three_runs_leave_only_base_and_run_3(reg: Registry) -> None:
    runs = [reg.train() for _ in range(3)]
    lineage = reg.lineage_of(runs[0])

    assert reg.on_disk(lineage) == {reg.base(lineage), runs[2]}
    versions = reg.versions(lineage)
    for gone in runs[:2]:
        assert versions[gone]["weights_status"] == "weights_deleted"
        assert versions[gone]["main_metric"] is not None, "metadata is kept (REG-2.7)"
    assert versions[runs[2]]["is_latest"]
    deletions = [
        e
        for job in reg.retention_jobs()
        for e in reg.runner.events(job["id"])
        if e["substage"] == "delete"
    ]
    assert sorted(e["metrics"]["version_id"] for e in deletions) == sorted(runs[:2])
    assert all(e["metrics"]["bytes_freed"] > 0 for e in deletions), "REG-2.9: bytes logged"


def test_REG_2_3_n_2_five_runs_keep_base_runs_3_and_4_and_run_5(reg: Registry) -> None:
    first = reg.train()
    lineage = reg.lineage_of(first)
    assert reg.keep(lineage, 2).status_code == 200
    runs = [first, *(reg.train() for _ in range(4))]

    assert reg.on_disk(lineage) == {reg.base(lineage), runs[2], runs[3], runs[4]}
    actions = {v: reg.versions(lineage)[v]["retention_action"] for v in runs[2:]}
    assert set(actions.values()) == {"keep"}


def test_REG_2_4_global_0_and_lineage_x_at_3_only_x_keeps_intermediates(reg: Registry) -> None:
    x_runs = [reg.train()]
    y_runs = [reg.train(model={"name": "small_cnn"})]
    x, y = reg.lineage_of(x_runs[0]), reg.lineage_of(y_runs[0])
    assert x != y
    assert reg.settings()["values"]["keep_intermediate"] == 0, "the global default"
    assert reg.keep(x, 3).status_code == 200
    x_runs += [reg.train() for _ in range(2)]
    y_runs += [reg.train(model={"name": "small_cnn"}) for _ in range(2)]

    assert reg.on_disk(x) == {reg.base(x), *x_runs}, "X keeps its intermediates"
    assert reg.on_disk(y) == {reg.base(y), y_runs[-1]}, "Y follows the global 0"
    rows = {r["lineage_id"]: r for r in reg.settings()["lineages"]}
    assert (rows[x]["keep_intermediate"], rows[x]["source"]) == (3, "lineage")
    assert (rows[y]["keep_intermediate"], rows[y]["source"]) == (0, "global")
    assert (rows[x]["intermediates"], rows[y]["intermediates"]) == (2, 0)


def test_REG_2_5_a_pinned_run_2_survives_n_0_cleanup(reg: Registry) -> None:
    runs = [reg.train(), reg.train()]
    pinned = reg.post(f"/model-versions/{runs[1]}/pin")
    assert pinned["pinned"] and pinned["retention_action"] == "keep"
    runs.append(reg.train())
    lineage = reg.lineage_of(runs[0])

    assert reg.on_disk(lineage) == {reg.base(lineage), runs[1], runs[2]}
    unpinned = reg.api.client.delete(f"/api/v1/model-versions/{runs[1]}/pin").json()
    assert (unpinned["pinned"], unpinned["retention_action"]) == (False, "delete")
    assert runs[1] in reg.on_disk(lineage), "unpinning deletes nothing by itself"
    deleted_pin = reg.api.client.post(f"/api/v1/model-versions/{runs[0]}/pin")
    assert deleted_pin.status_code == 409, "nothing to keep once the weights are gone"


def _spooled(reg: Registry, job_id: str, substage: str) -> int:
    spool = reg.data / "jobs" / job_id / "events.jsonl"
    if not spool.is_file():
        return 0
    lines = spool.read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if json.loads(line).get("substage") == substage)


def _pause(reg: Registry, job_id: str) -> None:
    reg.runner.queue.extend(reg.api.queued)
    reg.api.queued.clear()
    thread = reg.runner.run_in_thread()
    wait_until(lambda: _spooled(reg, job_id, "validate") >= 2, timeout_s=60, what="two epochs")
    reg.post(f"/jobs/{job_id}/pause", status=202)
    thread.join(60)
    assert reg.get(f"/jobs/{job_id}")["state"] == "paused"


def test_REG_2_6_no_checkpoint_after_a_run_and_a_paused_run_survives_cleanup(
    reg: Registry,
) -> None:
    done = reg.train()
    done_job = reg.get(f"/model-versions/{done}")["job_id"]
    lineage = reg.lineage_of(done)
    assert not list((reg.data / "jobs" / done_job / "checkpoints").glob("ckpt-*"))

    paused = reg.start(epochs=25)
    _pause(reg, paused)
    reg.post("/retention/run", {"lineage_id": lineage}, status=202)
    reg.flush()

    assert list((reg.data / "jobs" / paused / "checkpoints").glob("ckpt-*.json")), "kept"
    assert reg.get(f"/models/{lineage}")["checkpoint_bytes"] > 0
    reg.post(f"/jobs/{paused}/resume", status=202)
    reg.flush()
    resumed = reg.get(f"/jobs/{paused}")
    assert resumed["state"] == "completed", "still resumable after cleanup"
    resumes = [e for e in reg.runner.events(paused) if e["substage"] == "resume"]
    assert resumes and resumes[0]["metrics"]["resumed_at_step"] > 0
    assert reg.get(f"/models/{lineage}")["checkpoint_bytes"] == 0

    cancelled = reg.start(epochs=25)
    _pause(reg, cancelled)
    reg.post(f"/jobs/{cancelled}/cancel", status=202)
    reg.flush()  # cancelling a paused run queues retention for its lineage
    assert not list((reg.data / "jobs" / cancelled / "checkpoints").glob("ckpt-*"))
    partial = reg.get(f"/jobs/{cancelled}")["model_version_id"]
    detail = reg.get(f"/model-versions/{partial}")
    assert (detail["state"], detail["weights_status"]) == ("partial", "weights_deleted")


def test_REG_2_7_a_deleted_version_keeps_its_metrics_and_can_be_retrained(reg: Registry) -> None:
    first, _second = reg.train(), reg.train()

    gone = reg.get(f"/model-versions/{first}")
    assert gone["weights_status"] == "weights_deleted"
    assert gone["metrics"]["val_accuracy"] is not None and gone["history"]
    assert gone["weights_deleted_by_job_id"] in [j["id"] for j in reg.retention_jobs()]
    job = reg.post(f"/model-versions/{first}/retrain", status=202)
    reg.flush()

    again = reg.get(f"/model-versions/{reg.get(f'/jobs/{job["id"]}')['model_version_id']}")
    assert again["state"] == "trained" and again["id"] != first
    assert (again["config_hash"], again["dataset_version_id"]) == (
        gone["config_hash"],
        gone["dataset_version_id"],
    )
    assert again["parent_version_id"] == gone["parent_version_id"], "the same base"
    assert again["metrics"]["val_loss"] == pytest.approx(gone["metrics"]["val_loss"], abs=1e-5)
    base = reg.post(f"/model-versions/{gone['parent_version_id']}/retrain", status=409)
    assert base["code"] == "conflict"


def test_REG_2_8_an_in_use_or_parent_version_is_blocked_and_logged(reg: Registry, bus: Bus) -> None:
    first = reg.train()
    lineage = reg.lineage_of(first)
    bus.set_inference_loaded({"version_id": first, "device": "cpu"})
    second = reg.train()

    assert first in reg.on_disk(lineage), "loaded for inference: kept"
    warned = [
        e
        for job in reg.retention_jobs()
        for e in reg.runner.events(job["id"])
        if e["substage"] == "blocked" and e["metrics"].get("version_id") == first
    ]
    assert warned and warned[0]["level"] == "WARN"
    assert "loaded for inference" in warned[0]["reason"] and warned[0]["suggested_action"]
    assert reg.versions(lineage)[first]["retention_action"] == "blocked"

    bus.set_inference_loaded(None)
    with get_engine().begin() as connection:  # a fine-tune of `second` (REG-2.8b)
        connection.execute(
            text(
                "INSERT INTO model_versions (id, lineage_id, state, weights_status, "
                "parent_version_id, config, config_hash) VALUES ('mv_child0000000', :l, "
                "'trained', 'weights_deleted', :p, '{}', 'x')"
            ),
            {"l": lineage, "p": second},
        )
    third = reg.train()

    assert first not in reg.on_disk(lineage), "no longer in use: deleted"
    assert second in reg.on_disk(lineage), "the parent of mv_child0000000: kept"
    plan = reg.post("/retention/plan", {"lineage_id": lineage})["lineages"][0]
    assert [d["version_id"] for d in plan["blocked"]] == [second]
    assert "mv_child0000000" in plan["blocked"][0]["reason"]
    assert third in reg.on_disk(lineage)


def test_REG_2_8_without_redis_nothing_beyond_the_rule_is_deleted(reg: Registry) -> None:
    first = reg.train()
    lineage = reg.lineage_of(first)
    reg.keep(lineage, "all")
    runs = [first, reg.train(), reg.train()]
    with Session(get_engine()) as session:
        use = retention.in_use(session, None, redis_ok=False)
        values = retention.RetentionValues(keep_intermediate=0)
        plan = retention.build_plan(session, use=use, lineage_ids=[lineage], values=values)

    assert plan["versions_to_delete"] == 0
    assert sorted(d["version_id"] for d in plan["lineages"][0]["blocked"]) == sorted(runs[:2])
    assert all("Redis" in d["reason"] for d in plan["lineages"][0]["blocked"])


def test_REG_2_9_the_dry_run_lists_what_goes_and_the_run_frees_those_bytes(
    reg: Registry,
) -> None:
    first = reg.train()
    lineage = reg.lineage_of(first)
    reg.keep(lineage, "all")
    runs = [first, reg.train(), reg.train()]
    assert reg.on_disk(lineage) == {reg.base(lineage), *runs}
    values = reg.settings()["values"]
    what_if = {**values, "overrides": {lineage: 0}}

    plan = reg.post("/retention/plan", {"lineage_id": lineage, "values": what_if})
    dry = reg.post("/retention/run", {"lineage_id": lineage, "dry_run": True}, status=202)
    reg.flush()

    [item] = plan["lineages"]
    assert sorted(d["version_id"] for d in item["delete"]) == sorted(runs[:2])
    assert reg.on_disk(lineage) == {reg.base(lineage), *runs}, "a dry run changes nothing"
    assert reg.get(f"/jobs/{dry['id']}")["stages"][0]["metrics"]["would_delete"] == 0, (
        "the dry run used the saved settings (all)"
    )
    before = _du(reg.data / "registry" / lineage)
    saved = reg.save(what_if).json()

    job = reg.get(f"/jobs/{saved['job_id']}")
    freed = job["stages"][0]["metrics"]["bytes_freed"]
    assert freed == item["bytes_to_free"] == before - _du(reg.data / "registry" / lineage)
    assert reg.on_disk(lineage) == {reg.base(lineage), runs[2]}


def test_REG_2_10_lowering_n_lists_the_exact_versions_and_sizes_first(reg: Registry) -> None:
    first = reg.train()
    lineage = reg.lineage_of(first)
    reg.keep(lineage, 2)
    runs = [first, reg.train(), reg.train()]
    sizes = {v: reg.get(f"/model-versions/{v}")["weights_bytes"] for v in runs}
    section = reg.settings()
    body = {
        "section": "retention",
        "values": {**section["values"], "overrides": {lineage: 0}},
        "version": section["version"],
    }

    refused = reg.api.client.put("/api/v1/settings", json=body)

    assert refused.status_code == 409
    problem = refused.json()
    assert problem["code"] == "confirmation_required"
    [item] = [i for i in problem["plan"]["lineages"] if i["lineage_id"] == lineage]
    listed = {d["version_id"]: d["weights_bytes"] for d in item["delete"]}
    assert listed == {runs[0]: sizes[runs[0]], runs[1]: sizes[runs[1]]}, "REG-2.10.AC1"
    assert f"{(sizes[runs[0]] + sizes[runs[1]]) / 2**20:.1f} MB" in problem["detail"]
    assert reg.settings()["values"]["overrides"][lineage] == 2, "nothing saved yet"
    assert reg.on_disk(lineage) == {reg.base(lineage), *runs}

    reg.post(f"/model-versions/{runs[0]}/pin")  # the plan changes
    stale = reg.api.client.put(
        "/api/v1/settings", json={**body, "confirm": problem["plan"]["token"]}
    )
    assert stale.status_code == 409 and "changed" in stale.json()["detail"]
    fresh = stale.json()["plan"]
    assert [d["version_id"] for i in fresh["lineages"] for d in i["delete"]] == [runs[1]]

    saved = reg.api.client.put("/api/v1/settings", json={**body, "confirm": fresh["token"]})
    assert saved.status_code == 200, saved.text
    reg.flush()
    assert reg.on_disk(lineage) == {reg.base(lineage), runs[0], runs[2]}
    old = reg.api.client.put("/api/v1/settings", json=body)
    assert old.status_code == 409 and old.json()["code"] == "conflict", "a stale version"


def test_REG_2_11_disk_use_per_lineage_matches_the_bytes_on_disk(reg: Registry) -> None:
    first = reg.train()
    lineage = reg.lineage_of(first)
    reg.keep(lineage, 1)
    second = reg.train()
    reg.post(f"/model-versions/{first}/pin")
    runs = [first, second, reg.train()]
    paused = reg.start(epochs=25)
    _pause(reg, paused)

    report = reg.disk(lineage)

    on_disk = _du(reg.data / "registry" / lineage) + _du(reg.data / "jobs" / paused / "checkpoints")
    assert report["disk_bytes"] == on_disk, "REG-2.11.AC1"
    assert report["registry_bytes"] == on_disk and report["matches"]
    parts = report["parts"]
    folder = reg.data / "registry" / lineage
    assert parts["base"] == _du(folder / reg.base(lineage))
    assert parts["latest"] == _du(folder / runs[2])
    assert parts["intermediate"] == _du(folder / runs[1])
    assert parts["pinned"] == _du(folder / runs[0])
    assert parts["checkpoints"] == _du(reg.data / "jobs" / paused / "checkpoints") > 0
    assert report["orphans"] == [] and report["missing"] == []


def _ten_runs(reg: Registry) -> tuple[str, list[str]]:
    runs = [reg.train() for _ in range(10)]
    return reg.lineage_of(runs[0]), runs


def test_REG_2_1_base_weights_are_still_present_after_10_runs(reg: Registry) -> None:
    first = reg.train()
    lineage = reg.lineage_of(first)
    base = reg.base(lineage)
    before = (reg.data / "registry" / lineage / base / "model.safetensors").read_bytes()
    for _ in range(9):
        reg.train()

    assert base in reg.on_disk(lineage)
    assert (reg.data / "registry" / lineage / base / "model.safetensors").read_bytes() == before
    assert reg.versions(lineage)[base]["retention_action"] == "keep"


def test_NFR_14_after_10_runs_the_disk_holds_base_latest_and_one_checkpoint(
    reg: Registry,
) -> None:
    lineage, runs = _ten_runs(reg)
    paused = reg.start(epochs=25)
    _pause(reg, paused)

    report = reg.disk(lineage)

    folder = reg.data / "registry" / lineage
    bound = (
        _du(folder / reg.base(lineage))
        + _du(folder / runs[-1])
        + _du(reg.data / "jobs" / paused / "checkpoints")
    )
    assert report["matches"]
    assert report["disk_bytes"] == bound, "NFR-14.AC1: base + latest + one checkpoint"
    assert (report["parts"]["intermediate"], report["parts"]["pinned"]) == (0, 0)
    assert reg.on_disk(lineage) == {reg.base(lineage), runs[-1]}


def test_BUG7_pauses_cancels_and_crashes_leave_no_bloat(
    reg: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = reg.train()
    lineage = reg.lineage_of(first)
    cancelled = reg.start(epochs=25)
    _pause(reg, cancelled)
    reg.post(f"/jobs/{cancelled}/cancel", status=202)
    reg.flush()
    monkeypatch.setenv(CRASH_AT_ENV, "train:step:5")  # epoch 2, after epoch 1's checkpoint
    crashed = reg.start(epochs=2)
    reg.flush()
    assert reg.get(f"/jobs/{crashed}")["state"] == "failed"
    monkeypatch.delenv(CRASH_AT_ENV)
    reg.post(f"/jobs/{crashed}/retry", status=202)
    reg.flush()
    last = reg.get(f"/jobs/{crashed}")["model_version_id"]

    report = reg.disk(lineage)
    assert report["matches"] and report["orphans"] == []
    assert report["parts"]["checkpoints"] == 0, "no checkpoint left behind"
    assert reg.on_disk(lineage) == {reg.base(lineage), last}
    for job_id in (cancelled, crashed):
        assert not list((reg.data / "jobs" / job_id / "checkpoints").glob("ckpt-*")), job_id


def test_NFR_15_a_kill_during_cleanup_then_a_rerun_leaves_a_consistent_registry(
    reg: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = reg.train()
    lineage = reg.lineage_of(first)
    reg.keep(lineage, "all")
    runs = [first, reg.train(), reg.train()]
    orphan = reg.data / "registry" / lineage / "mv_orphan000000"  # a crash before a commit
    orphan.mkdir()
    (orphan / "model.safetensors").write_bytes(b"x" * 100)
    monkeypatch.setenv(CRASH_AT_ENV, "retention:substage:deleting")

    saved = reg.save({**reg.settings()["values"], "overrides": {lineage: 0}}).json()

    killed = reg.get(f"/jobs/{saved['job_id']}")
    assert killed["state"] == "failed", "killed after marking a version deleting"
    states = {v: reg.get(f"/model-versions/{v}")["weights_status"] for v in runs[:2]}
    assert "deleting" in states.values()
    monkeypatch.delenv(CRASH_AT_ENV)
    reg.post(f"/jobs/{saved['job_id']}/retry", status=202)
    reg.flush()

    rerun = reg.get(f"/jobs/{saved['job_id']}")
    assert rerun["state"] == "completed", rerun
    metrics = rerun["stages"][0]["metrics"]
    assert (metrics["recovered"], metrics["orphans_removed"]) == (1, 1)
    for v in runs[:2]:
        assert reg.get(f"/model-versions/{v}")["weights_status"] == "weights_deleted"
    assert reg.on_disk(lineage) == {reg.base(lineage), runs[2]}, "no orphaned files"
    report = reg.disk(lineage)
    assert report["matches"] and report["missing"] == [], "no dangling rows"


def test_REG_2_10_a_save_asks_only_about_the_deletions_it_causes(reg: Registry) -> None:
    pending = reg.train(model={"name": "small_cnn"})
    y = reg.lineage_of(pending)
    reg.keep(y, "all")
    reg.train(model={"name": "small_cnn"})
    with get_engine().begin() as connection:  # Y's override vanishes with no cleanup after it
        connection.execute(text("DELETE FROM retention_settings WHERE lineage_id = :l"), {"l": y})
    x = reg.lineage_of(reg.train())
    assert pending in reg.on_disk(y), "pending: the current settings would delete it"

    raised = reg.keep(x, 2, confirm=False)

    assert raised.status_code == 200, "raising X's N deletes nothing, so nothing asks"
    assert raised.json()["job_id"] is None
    assert pending in reg.on_disk(y), "the save leaves Y to its own next cleanup"
