"""Settings stored in the database and edited on SCR-14 (OC-9; docs/API.md §3.10).

M8 step 2 has the retention section (REG-2.3, REG-2.4, REG-2.10). The training
limits move here with SCR-14's Gate 3.

Saving settings that would delete weights asks first (REG-2.10, ADR-0015 rule 7):
the save is refused with ``409 confirmation_required`` and the plan, and is
applied only when sent again with that plan's ``token``. A token for a plan
that has changed since is refused the same way, with the new plan.
"""

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from stratal.api import schemas
from stratal.api.jobs import Db, Queue, Redis
from stratal.core.errors import ConfirmationRequired, Conflict
from stratal.db.models import ModelLineage, ModelVersion
from stratal.registry import retention, store
from stratal.runtime import jobs as job_service

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
    }


@router.get("/settings", response_model=schemas.SettingsList)
def get_settings_sections(session: Db) -> Json:
    """Each section's values, its JSON schema and a version to send back when saving."""
    return {"sections": [_retention_section(session)]}


class SaveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: Literal["retention"]
    values: retention.RetentionValues
    version: str = Field(description="The section's version as read; a stale one is refused.")
    confirm: str | None = Field(
        default=None, description="The token of the plan shown by a confirmation_required reply."
    )


@router.put("/settings", response_model=schemas.SettingsSaved)
def save_settings(body: SaveSettings, session: Db, enqueue: Queue, bus: Redis) -> Json:
    """Save one section. ``409 conflict`` if it changed since it was read;
    ``409 confirmation_required`` with ``plan`` if saving would delete weights."""
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
        plan = retention.build_plan(session, use=use, values=body.values)
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
        retention.write_values(session, body.values)
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
