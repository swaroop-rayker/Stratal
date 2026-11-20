"""Model templates in PostgreSQL (BUILD-1.7, ADR-0017 rule 9).

A template's spec is stored in canonical form with its structure hash, so a
saved spec reloads identically however its nodes and edges were listed. The
built-in templates are written from :mod:`stratal.build.templates` on first use
and cannot be changed or deleted.
"""

import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from stratal.build.spec import GraphSpec, canonical, structure_hash
from stratal.build.templates import BUILTINS
from stratal.core.errors import Conflict, NotFound, ValidationFailed
from stratal.data.dataset_types import TYPE_INFO
from stratal.db.models import ModelTemplate
from stratal.mon.events import utc_now


def ensure_builtins(session: Session) -> None:
    """Write or refresh the built-in templates; harmless to repeat."""
    for builtin in BUILTINS:
        spec = canonical(builtin.spec)
        values = {
            "description": builtin.description,
            "spec": spec.model_dump(mode="json"),
            "structure_hash": structure_hash(spec),
            "dataset_type": builtin.dataset_type,
            "builtin": True,
        }
        statement = insert(ModelTemplate).values(id=_new_id(), name=builtin.name, **values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[ModelTemplate.name],
                set_=values,
                where=ModelTemplate.structure_hash != statement.excluded.structure_hash,
            )
        )


def list_templates(session: Session, *, dataset_type: str | None = None) -> list[ModelTemplate]:
    query = select(ModelTemplate).order_by(ModelTemplate.builtin.desc(), ModelTemplate.name)
    if dataset_type is not None:
        query = query.where(ModelTemplate.dataset_type == dataset_type)
    return list(session.scalars(query))


def get_template(session: Session, template_id: str) -> ModelTemplate:
    template = session.get(ModelTemplate, template_id)
    if template is None:
        raise NotFound(f"No model template {template_id!r}.")
    return template


def create_template(
    session: Session, *, name: str, description: str, spec: GraphSpec, dataset_type: str
) -> ModelTemplate:
    _check_type(dataset_type)
    _check_name(session, name, None)
    form = canonical(spec)
    template = ModelTemplate(
        id=_new_id(),
        name=name,
        description=description,
        spec=form.model_dump(mode="json"),
        structure_hash=structure_hash(form),
        dataset_type=dataset_type,
        builtin=False,
    )
    session.add(template)
    session.flush()
    return template


def update_template(
    session: Session,
    template_id: str,
    *,
    name: str | None,
    description: str | None,
    spec: GraphSpec | None,
) -> ModelTemplate:
    template = get_template(session, template_id)
    if template.builtin:
        raise Conflict(
            f"{template.name} is a built-in template and cannot be changed.",
            suggested_action="Save your changes as a new template.",
        )
    if name is not None and name != template.name:
        _check_name(session, name, template.id)
        template.name = name
    if description is not None:
        template.description = description
    if spec is not None:
        form = canonical(spec)
        template.spec = form.model_dump(mode="json")
        template.structure_hash = structure_hash(form)
    template.updated_at = utc_now()
    session.flush()
    return template


def delete_template(session: Session, template_id: str) -> None:
    template = get_template(session, template_id)
    if template.builtin:
        raise Conflict(f"{template.name} is a built-in template and cannot be deleted.")
    session.delete(template)


def template_view(template: ModelTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "dataset_type": template.dataset_type,
        "builtin": template.builtin,
        "spec": template.spec,
        "structure_hash": template.structure_hash,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def _new_id() -> str:
    return f"mt_{secrets.token_hex(6)}"


def _check_type(dataset_type: str) -> None:
    info = TYPE_INFO.get(dataset_type)
    if info is None or not info.families:
        raise ValidationFailed(
            f"{dataset_type!r} is not a dataset type models are built for.",
            suggested_action="Use labelled_images, tabular or labelled_text.",
        )


def _check_name(session: Session, name: str, own_id: str | None) -> None:
    clash = session.scalar(select(ModelTemplate).where(ModelTemplate.name == name))
    if clash is not None and clash.id != own_id:
        raise Conflict(
            f"A template called {name!r} exists.",
            suggested_action="Choose another name, or update that template.",
        )
