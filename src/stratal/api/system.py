"""The machine as Stratal sees it, and the stage config schemas (docs/API.md §3.1, §3.7).

``GET /system`` feeds the shell's resource totals (SCR-01, MON-2.1). The config
endpoints feed the Start pipeline drawer's generated forms (SCR-02, UI-2.1 slice;
UI-2.3 later).
"""

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from stratal.api import schemas
from stratal.api.jobs import Db, Redis
from stratal.core.errors import NotFound
from stratal.core.settings import get_settings
from stratal.db.models import ResourceSample
from stratal.label.stage import LabelConfig
from stratal.runtime import limits
from stratal.runtime import stages as stage_registry

router = APIRouter()
Json = dict[str, Any]


@router.get("/system", tags=["monitoring"], response_model=schemas.System)
def system(session: Db, bus: Redis) -> Json:
    """GPU name, VRAM and RAM totals, and the VRAM cap jobs run under (ADR-0006).

    The monitor describes the machine at start (it alone reads NVML); the latest
    resource sample fills in totals when it has not. Unknown values are null,
    never zero (DESIGN-SYSTEM principle 6).
    """
    try:
        info = bus.get_system() or {}
    except Exception:
        info = {}
    sample = session.scalars(
        select(ResourceSample).order_by(ResourceSample.id.desc()).limit(1)
    ).first()
    settings = get_settings()
    vram_total = info.get("vram_total_mb") or (sample.vram_total_mb if sample else None)
    ram_total = info.get("ram_total_mb") or (sample.ram_total_mb if sample else None)
    fraction = LabelConfig().vram_fraction
    training = limits.read(session, settings)
    return {
        "gpu_name": info.get("gpu_name"),
        "gpu_error": info.get("gpu_error"),
        "vram_total_mb": vram_total,
        "vram_cap_fraction": fraction,
        "vram_cap_mb": vram_total * fraction if vram_total is not None else None,
        "ram_total_mb": ram_total,
        "cpu_count": info.get("cpu_count"),
        "gpu_slowdown_c": info.get("gpu_slowdown_c"),
        "thermal_pause_c": training.thermal_pause_c,
        "thermal_resume_c": training.thermal_resume_c,
        "stall_timeout_s": training.stall_timeout_s,
    }


OWN_KINDS = ("train", "retention")
"""Stages that are job kinds of their own, not pipeline stages: not listed for the drawer."""


def _pipeline_stages() -> list[tuple[str, stage_registry.StageDef]]:
    """Registered pipeline stages in pipeline order. ``train`` is its own job kind, not a
    pipeline stage, so the Start pipeline drawer does not list it; its schema is still at
    ``/config/schemas/train``."""
    available = stage_registry.registered()
    order = stage_registry.PIPELINE_ORDER
    named = [(n, s) for n, s in available.items() if n in order]
    extra = [(n, s) for n, s in available.items() if n not in order and n not in OWN_KINDS]
    return sorted(named, key=lambda item: order.index(item[0])) + extra


@router.get("/config/schemas", tags=["config"], response_model=schemas.StageSchemaList)
def config_schemas() -> Json:
    """The stage config models, in pipeline order."""
    items = [
        {"name": name, "queue": stage.queue, "title": stage.config_model.__name__}
        for name, stage in _pipeline_stages()
    ]
    return {"items": items, "next_cursor": None}


@router.get("/config/schemas/{name}", tags=["config"])
def config_schema(name: str) -> Json:
    """JSON Schema of one stage's config, for a generated form."""
    stage = stage_registry.get_stage(name)
    if stage is None:
        raise NotFound(
            f"No stage {name!r}.", suggested_action="List the stages with GET /config/schemas."
        )
    return stage.config_model.model_json_schema()


@router.get("/config/defaults", tags=["config"])
def config_defaults() -> Json:
    """Each stage's default config, as a pipeline would resolve it with no overrides."""
    defaults: Json = {}
    for name, stage in _pipeline_stages():
        try:
            defaults[name] = stage.config_model().model_dump(mode="json")
        except Exception:  # a config with a required field has no defaults to show
            defaults[name] = None
    return defaults
