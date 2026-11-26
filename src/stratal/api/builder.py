"""The model builder's API (M9, ADR-0017; docs/API.md §3.11).

- ``GET /model-specs/schema``: the JSON Schema of a layer and of a graph spec, from
  which SCR-08 generates its forms (UI-2.3).
- ``POST /model-specs/validate``: shapes, parameter counts, the memory estimate and
  every problem, between a dataset version's locked ends (BUILD-1.3 to BUILD-1.6). A
  spec with problems is a ``200`` whose ``problems`` list them; a field out of range
  is a ``422 validation_error`` (BUILD-1.2).
- ``GET /dataset-types``: each type's input, model families and default metrics
  (DATA-5.5).
- ``/model-templates``: saved specs (BUILD-1.7).

Plain Python throughout: the API image has no ML library (ADR-0017 rule 4).
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from stratal.api.jobs import Db
from stratal.build import store
from stratal.build.io import version_io
from stratal.build.layers import Layer
from stratal.build.spec import (
    MAX_EDGES,
    MAX_NODES,
    GraphSpec,
    Optimizer,
    SpecReport,
    validate_graph,
)
from stratal.core.settings import get_settings
from stratal.data.dataset_types import TYPE_INFO, Family, InputKind
from stratal.db.engine import get_engine
from stratal.runtime import stages as stage_registry

router = APIRouter(tags=["builder"])


class SpecSchema(BaseModel):
    layer: dict[str, Any]
    graph: dict[str, Any]
    max_nodes: int
    max_edges: int


@router.get("/model-specs/schema", response_model=SpecSchema)
def spec_schema() -> SpecSchema:
    """JSON Schemas for SCR-08's forms (UI-2.3): one per layer type, under ``$defs``."""
    return SpecSchema(
        layer=TypeAdapter(Layer).json_schema(),
        graph=GraphSpec.model_json_schema(),
        max_nodes=MAX_NODES,
        max_edges=MAX_EDGES,
    )


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: GraphSpec
    dataset_version: str = Field(description="The split the model will train on.")
    optimizer: Optimizer = Field(default="adam", description="For the memory estimate.")


class DatasetEnds(BaseModel):
    version_id: str
    dataset_type: str
    input: dict[str, Any] | None
    input_text: str | None
    output_size: int | None
    classes: list[str]
    families: list[Family]
    trainable: bool
    problem: str | None


class ValidateResponse(BaseModel):
    dataset: DatasetEnds
    report: SpecReport | None
    """``None`` when the dataset version cannot feed the builder (``dataset.problem``)."""


@router.post("/model-specs/validate", response_model=ValidateResponse)
def validate_spec(body: ValidateRequest) -> ValidateResponse:
    """Check a spec against a dataset version's two ends (BUILD-1.3 to BUILD-1.6)."""
    io = version_io(get_engine(), get_settings().data_dir, body.dataset_version)
    ends = DatasetEnds(
        version_id=io.version_id,
        dataset_type=io.dataset_type,
        input=io.input_shape.as_json() if io.input_shape is not None else None,
        input_text=io.input_shape.describe() if io.input_shape is not None else None,
        output_size=io.output_size,
        classes=list(io.classes),
        families=list(io.info.families),
        trainable=io.info.trainable,
        problem=io.problem,
    )
    if io.input_shape is None or io.output_size is None:
        return ValidateResponse(dataset=ends, report=None)
    report = validate_graph(
        body.graph,
        input_shape=io.input_shape,
        output_size=io.output_size,
        optimizer=body.optimizer,
        families=io.info.families,
    )
    return ValidateResponse(dataset=ends, report=report)


class TypeStage(BaseModel):
    name: str
    optional: bool
    """Run only when the request configures it (``transform``, ADR-0019 Addendum B rule 25)."""


class DatasetTypeOut(BaseModel):
    name: str
    title: str
    input_kind: InputKind | None
    families: list[Family]
    default_metrics: list[str]
    trainable: bool
    note: str
    source: str
    """What a source folder of this type holds (the Start pipeline drawer, SCREENS.md v4)."""
    stages: list[TypeStage]
    """The type's pipeline stages in order, as POST /jobs runs them (open point 7b)."""


@router.get("/dataset-types", response_model=list[DatasetTypeOut])
def dataset_types() -> list[DatasetTypeOut]:
    """Each dataset type's input, compatible model families and default metrics (DATA-5.5),
    and its pipeline's stages with what its source folder holds (DATA-5.2)."""
    available = stage_registry.registered()
    return [
        DatasetTypeOut(
            name=t.name,
            title=t.title,
            input_kind=t.input_kind,
            families=list(t.families),
            default_metrics=list(t.default_metrics),
            trainable=t.trainable,
            note=t.note,
            source=t.source,
            stages=[
                TypeStage(name=name, optional=name in stage_registry.OPTIONAL_STAGES)
                for name in stage_registry.PIPELINE_ORDERS[t.name]
                if name in available
            ],
        )
        for t in TYPE_INFO.values()
    ]


# ---------------------------------------------------------------------------- templates


class TemplateOut(BaseModel):
    id: str
    name: str
    description: str
    dataset_type: str
    builtin: bool
    spec: GraphSpec
    structure_hash: str
    created_at: datetime
    updated_at: datetime


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    dataset_type: str
    spec: GraphSpec


class TemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    spec: GraphSpec | None = None


@router.get("/model-templates", response_model=list[TemplateOut])
def list_templates(session: Db, dataset_type: str | None = Query(default=None)) -> list[Any]:
    store.ensure_builtins(session)
    session.commit()
    return [
        store.template_view(t) for t in store.list_templates(session, dataset_type=dataset_type)
    ]


@router.post("/model-templates", response_model=TemplateOut, status_code=201)
def create_template(session: Db, body: TemplateCreate) -> Any:
    template = store.create_template(
        session,
        name=body.name,
        description=body.description,
        spec=body.spec,
        dataset_type=body.dataset_type,
    )
    session.commit()
    return store.template_view(template)


@router.get("/model-templates/{template_id}", response_model=TemplateOut)
def get_template(session: Db, template_id: str) -> Any:
    return store.template_view(store.get_template(session, template_id))


@router.put("/model-templates/{template_id}", response_model=TemplateOut)
def update_template(session: Db, template_id: str, body: TemplateUpdate) -> Any:
    template = store.update_template(
        session, template_id, name=body.name, description=body.description, spec=body.spec
    )
    session.commit()
    return store.template_view(template)


@router.delete("/model-templates/{template_id}", status_code=204)
def delete_template(session: Db, template_id: str) -> Response:
    store.delete_template(session, template_id)
    session.commit()
    return Response(status_code=204)
