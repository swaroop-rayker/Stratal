"""Settings stored in the database and edited on SCR-14 (OC-9; docs/API.md §3.10).

Three sections: ``training`` (checkpoint interval and the watchdog's limits; RUN-1.3,
RUN-1.6, RUN-1.7), ``retention`` (REG-2.3, REG-2.4, REG-2.10) and ``monitoring``
(sampling intervals, alert thresholds and the diagnostics' disk figure; MON-2.2,
MON-2.6, MON-3.2; ADR-0020 rule 14). Values are
checked against each section's schema; a field's problem comes back as a
``422 validation_error`` whose ``errors[].loc`` names it (SCR-14 frame D).

Saving settings that would delete weights asks first (REG-2.10, ADR-0015 rule 7):
the save is refused with ``409 confirmation_required`` and the plan, and is
applied only when sent again with that plan's ``token``. A token for a plan
that has changed since is refused the same way, with the new plan.
"""

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select

from stratal.api import schemas
from stratal.api.jobs import Db, Queue, Redis
from stratal.core.bus import Bus
from stratal.core.errors import ConfirmationRequired, Conflict, FieldProblem, ValidationFailed
from stratal.core.settings import get_settings
from stratal.db.models import Job, ModelLineage, ModelVersion, ResourceSample
from stratal.registry import retention, store
from stratal.runtime import jobs as job_service
from stratal.runtime import limits

router = APIRouter(tags=["settings"])
Json = dict[str, Any]
MB = 2**20


def _lineage_rows(session: Db, values: retention.RetentionValues) -> list[Json]:
    rows: list[Json] = []
    for lineage in session.scalars(select(ModelLineage).order_by(ModelLineage.name)):
        versions = list(
            session.scalars(select(ModelVersion).where(ModelVersion.lineage_id == lineage.id))
        )
        latest = store.latest_of(versions)
        intermediates = [
            v
            for v in versions
            if v.state == "trained"
            and v.weights_status == "present"
            and (latest is None or v.id != latest.id)
        ]
        keep, source = retention.effective_keep(values, lineage.id)
        rows.append(
            {
                "lineage_id": lineage.id,
                "name": lineage.name,
                "keep_intermediate": "all" if keep is None else keep,
                "source": source,
                "intermediates": len(intermediates),
                "intermediates_bytes": sum(v.weights_bytes or 0 for v in intermediates),
            }
        )
    return rows


def _retention_section(session: Db) -> Json:
    values = retention.read_values(session)
    return {
        "name": "retention",
        "title": "Retention",
        "requirements": ["REG-2.3", "REG-2.4", "REG-2.10"],
        "values": values.model_dump(mode="json"),
        "schema": retention.RetentionValues.model_json_schema(),
        "version": retention.values_version(values),
        "lineages": _lineage_rows(session, values),
        "context": None,
    }


def _slowdown(bus: Bus) -> float | None:
    try:
        value = (bus.get_system() or {}).get("gpu_slowdown_c")
    except Exception:
        return None
    return float(value) if value else None


def _training_section(session: Db, bus: Bus) -> Json:
    values = limits.read(session, get_settings())
    since = func.now() - timedelta(days=7)
    hottest = session.execute(
        select(ResourceSample.gpu_temp_c, ResourceSample.job_id, ResourceSample.ts)
        .where(ResourceSample.ts >= since, ResourceSample.gpu_temp_c.is_not(None))
        .order_by(ResourceSample.gpu_temp_c.desc())
        .limit(1)
    ).first()
    running = list(
        session.scalars(
            select(Job.id).where(Job.state.in_(("running", "stalled"))).order_by(Job.id)
        )
    )
    return {
        "name": "training",
        "title": "Training",
        "requirements": ["RUN-1.3", "RUN-1.6", "RUN-1.7"],
        "values": values.model_dump(mode="json"),
        "schema": limits.TrainingValues.model_json_schema(),
        "version": limits.version(values),
        "lineages": None,
        "context": {
            "gpu_slowdown_c": _slowdown(bus),
            "gpu_temp_high_7d": None
            if hottest is None
            else {
                "celsius": float(hottest[0] or 0.0),
                "job_id": hottest[1],
                "at": store.iso(hottest[2]),
            },
            "running_jobs": running,
        },
    }


def _monitoring_section(session: Db) -> Json:
    values = limits.read_monitoring(session, get_settings())
    return {
        "name": "monitoring",
        "title": "Monitoring",
        "requirements": ["MON-2.2", "MON-2.6", "MON-3.2"],
        "values": values.model_dump(mode="json"),
        "schema": limits.MonitoringValues.model_json_schema(),
        "version": limits.monitoring_version(values),
        "lineages": None,
        "context": None,
    }


@router.get("/settings", response_model=schemas.SettingsList)
def get_settings_sections(session: Db, bus: Redis) -> Json:
    """Each section's values, its JSON schema and a version to send back when saving."""
    return {
        "sections": [
            _training_section(session, bus),
            _retention_section(session),
            _monitoring_section(session),
        ]
    }


def _validated[M: BaseModel](model: type[M], values: dict[str, Any]) -> M:
    """``values`` as ``model``, or a 422 naming each field at ``["values", field]``."""
    try:
        return model.model_validate(values)
    except ValidationError as exc:
        problems: list[FieldProblem] = []
        for error in exc.errors():
            field: list[str | int] = [str(p) for p in error["loc"]]
            if not field and "thermal_resume_c" in str(error["msg"]):
                field = ["thermal_resume_c"]  # the cross-field rule belongs to resume
            problems.append(
                {
                    "loc": ["values", *field],
                    "msg": str(error["msg"]).removeprefix("Value error, "),
                    "code": str(error["type"]),
                }
            )
        raise ValidationFailed(
            "The settings did not pass validation.",
            suggested_action="Fix the fields listed in 'errors'.",
            errors=problems,
        ) from None


class SaveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: Literal["retention", "training", "monitoring"]
    values: dict[str, Any]
    version: str = Field(description="The section's version as read; a stale one is refused.")
    confirm: str | None = Field(
        default=None, description="The token of the plan shown by a confirmation_required reply."
    )


@router.put("/settings", response_model=schemas.SettingsSaved)
def save_settings(body: SaveSettings, session: Db, enqueue: Queue, bus: Redis) -> Json:
    """Save one section. ``409 conflict`` if it changed since it was read;
    ``409 confirmation_required`` with ``plan`` if saving would delete weights."""
    if body.section == "training":
        return _save_training(body, session, bus)
    if body.section == "monitoring":
        return _save_monitoring(body, session)
    new = _validated(retention.RetentionValues, body.values)
    queued = None
    with session.begin():
        current = retention.read_values(session)
        if retention.values_version(current) != body.version:
            raise Conflict(
                "The retention settings changed since they were read.",
                suggested_action="Reload the settings and make the change again.",
                extensions={"version": retention.values_version(current)},
            )
        loaded, ok = retention.loaded_from(bus.get_inference_loaded)
        use = retention.in_use(session, loaded, redis_ok=ok)
        plan = retention.caused_by(
            retention.build_plan(session, use=use, values=new),
            retention.build_plan(session, use=use, values=current),
            values=new,
        )
        doomed = [d["version_id"] for item in plan["lineages"] for d in item["delete"]]
        if doomed and body.confirm != plan["token"]:
            size = sum(d["weights_bytes"] for item in plan["lineages"] for d in item["delete"])
            again = " The plan changed since it was shown." if body.confirm else ""
            raise ConfirmationRequired(
                f"Saving this deletes the weights of {len(doomed)} version"
                f"{'s' if len(doomed) != 1 else ''}, {size / MB:.1f} MB.{again}",
                suggested_action="Check the versions in 'plan', then send the same request "
                "with confirm set to plan.token.",
                extensions={"plan": plan},
            )
        retention.write_values(session, new)
        if doomed:
            affected = [item["lineage_id"] for item in plan["lineages"] if item["delete"]]
            _, queued = job_service.create_retention_job(
                session, lineage_ids=affected, confirmed=doomed, reason="settings change confirmed"
            )
        section = _retention_section(session)
    if queued is not None:
        enqueue(queued)
        job_service.after_commit(bus, queued.job_id)
    return {"section": section, "job_id": queued.job_id if queued is not None else None}


def _save_training(body: SaveSettings, session: Db, bus: Bus) -> Json:
    """The training section applies at once to the watchdog's next check, and the
    checkpoint interval to new runs (NFR-4). It deletes nothing, so nothing asks first."""
    new = _validated(limits.TrainingValues, body.values)
    with session.begin():
        current = limits.read(session, get_settings())
        if limits.version(current) != body.version:
            raise Conflict(
                "The training settings changed since they were read.",
                suggested_action="Reload the settings and make the change again.",
                extensions={"version": limits.version(current)},
            )
        limits.write(session, new, slowdown_c=_slowdown(bus))
        section = _training_section(session, bus)
    return {"section": section, "job_id": None}


def _save_monitoring(body: SaveSettings, session: Db) -> Json:
    """The sampler reads its intervals within 30 s and the alerts their thresholds within
    5 s; nothing is deleted, so nothing asks first."""
    new = _validated(limits.MonitoringValues, body.values)
    with session.begin():
        current = limits.read_monitoring(session, get_settings())
        if limits.monitoring_version(current) != body.version:
            raise Conflict(
                "The monitoring settings changed since they were read.",
                suggested_action="Reload the settings and make the change again.",
                extensions={"version": limits.monitoring_version(current)},
            )
        limits.write_monitoring(session, new)
        section = _monitoring_section(session)
    return {"section": section, "job_id": None}
