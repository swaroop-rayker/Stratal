"""Fine-tuning a trained version: what may start a run, and what it must match (RUN-1.10).

ADR-0014 Addendum A. Torch-free: the API checks a request with it before any job
exists (``POST /jobs`` refuses, ``POST /jobs/validate`` lists), and the train stage
runs the same checks again before step 1 (rule 4).

Each rule of rule 4 compares the parent with the chosen split and yields a
:class:`Check` that names both values, so a refusal says exactly what differs.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.build.spec import Memory, SpecReport
from stratal.core.errors import FieldProblem, NotFound, ValidationFailed, WeightsDeleted
from stratal.data import registry
from stratal.data.records import LABELLED_TEXT, TABULAR
from stratal.db.models import Dataset, DatasetVersion, ModelLineage, ModelVersion
from stratal.registry import store

HEAD = "head"
"""How ``freeze`` names the locked output layer (ADR-0017 rule 8: parameters ``head.…``)."""
LOC_INIT: list[str | int] = ["config", "init_from"]


@dataclass(frozen=True)
class Check:
    """One rule of ADR-0014 Addendum A rule 4: the parent's value, this split's, and whether
    they fit. ``code`` is the refusal's code when they do not."""

    rule: str
    ok: bool
    parent: str
    here: str
    message: str
    code: str


@dataclass(frozen=True)
class Parent:
    version: ModelVersion
    lineage: ModelLineage
    dataset_version: DatasetVersion


@dataclass(frozen=True)
class FineTune:
    """A fine-tune checked against its split: the parent, every check, and where the new
    version will be registered (rule 6)."""

    parent: Parent
    checks: list[Check]
    lineage_id: str
    lineage_name: str
    joins: bool

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


# ---------------------------------------------------------------------------- the parent


def parent_of(session: Session, version_id: str) -> Parent:
    """The version a run may start from (rule 2): trained, with its weights on disk."""
    try:
        version = store.get_version(session, version_id)
    except NotFound as exc:
        raise ValidationFailed(
            f"init_from {version_id!r} is not a registered model version.",
            suggested_action="Pick a trained version on SCR-11 (GET /models/{lineage}).",
            errors=[FieldProblem(loc=LOC_INIT, msg="unknown version", code="not_found")],
        ) from exc
    if version.state != "trained":
        raise ValidationFailed(
            f"{version_id} is a {version.state} version; a run starts only from a trained one "
            "(a base is what a fresh run starts from anyway).",
            suggested_action="Pick a trained version, or leave init_from empty for fresh weights.",
            errors=[FieldProblem(loc=LOC_INIT, msg=f"{version.state} version", code="invalid")],
        )
    if version.weights_status != "present" or version.weights_path is None:
        raise WeightsDeleted(
            f"The weights of {version_id} were deleted by retention, so no run can start "
            "from them.",
            suggested_action="Re-train it (REG-2.7), then fine-tune the result.",
        )
    lineage = session.get(ModelLineage, version.lineage_id)
    dataset_version = session.get(DatasetVersion, version.dataset_version_id)
    assert lineage is not None and dataset_version is not None
    return Parent(version, lineage, dataset_version)


# ---------------------------------------------------------------------------- the checks


def _dims(dims: Sequence[int]) -> str:
    return "×".join(str(d) for d in dims)


def _classes(classes: Sequence[str]) -> str:
    shown = ", ".join(classes[:6]) + (", …" if len(classes) > 6 else "")
    return f"{len(classes)} classes ({shown})" if classes else "one output (regression)"


def _table_columns(prep: Any) -> str:
    numeric = [c.name for c in prep.numeric]
    categorical = [c.name for c in prep.categorical]
    return f"{len(numeric)} numeric, {len(categorical)} categorical: " + ", ".join(
        numeric + categorical
    )


def _vocab(engine: Engine, data_dir: Path, version_id: str) -> dict[str, Any] | None:
    from stratal.data.tokenize import vocab_of

    with Session(engine) as session:
        _, manifest = registry.manifest_for(session, data_dir, version_id)
    return vocab_of(manifest)


def _table_prep(engine: Engine, data_dir: Path, version_id: str) -> Any:
    from stratal.ml.data import load_tabular
    from stratal.train.table_prep import fit

    with Session(engine) as session:
        _, manifest = registry.manifest_for(session, data_dir, version_id)
    return fit(load_tabular(manifest))


def parent_table_prep(data_dir: Path, parent: ModelVersion) -> Any:
    """The parent's fitted preprocessing, saved beside its weights (ADR-0019 rule 13)."""
    from stratal.train.table_prep import PREPROCESS_FILE, TablePrep

    assert parent.weights_path is not None
    path = data_dir / parent.weights_path / PREPROCESS_FILE
    return TablePrep.from_json(path.read_text(encoding="utf-8"))


def check(
    session: Session,
    engine: Engine,
    data_dir: Path,
    *,
    config: Any,
    version: DatasetVersion,
) -> FineTune:
    """Every rule of rule 4 for ``config`` (a ``TrainConfig`` with ``init_from``) on
    ``version``, and the lineage the run would join or found (rule 6)."""
    from stratal.build.io import version_io

    assert config.init_from is not None
    parent = parent_of(session, config.init_from)
    lineage = parent.lineage
    io = version_io(engine, data_dir, version.version_id)
    checks: list[Check] = []

    parent_type = parent.dataset_version.dtype or "none"
    here_type = version.dtype or "none"
    checks.append(
        Check(
            "Dataset type",
            parent_type == here_type,
            parent_type,
            here_type,
            f"Dataset type: {parent_type} in the parent, {here_type} here",
            "init_from_type",
        )
    )

    here_prep = (
        _table_prep(engine, data_dir, version.version_id) if version.dtype == TABULAR else None
    )
    here_task = here_prep.task if here_prep is not None else "classification"
    checks.append(
        Check(
            "Task",
            lineage.task == here_task,
            lineage.task,
            here_task,
            f"Task: {lineage.task} in the parent, {here_task} here; a reset head does not "
            "change it",
            "init_from_task",
        )
    )

    if here_type == TABULAR and parent_type == TABULAR:
        theirs = _table_columns(parent_table_prep(data_dir, parent.version))
        ours = _table_columns(here_prep)
        ok = theirs == ours
        message = f"Columns: {theirs} in the parent; {ours} here"
    elif here_type == LABELLED_TEXT and parent_type == LABELLED_TEXT:
        a = _vocab(engine, data_dir, parent.dataset_version.version_id) or {}
        b = _vocab(engine, data_dir, version.version_id) or {}
        theirs = f"vocabulary {str(a.get('sha256', '?'))[:12]}…, L = {a.get('max_length')}"
        ours = f"vocabulary {str(b.get('sha256', '?'))[:12]}…, L = {b.get('max_length')}"
        ok = (a.get("sha256"), a.get("max_length")) == (b.get("sha256"), b.get("max_length"))
        message = (
            f"Vocabulary: {theirs} in the parent, {ours} here; token IDs would mean other "
            "words. A new model is needed"
        )
    else:
        theirs = _dims(lineage.input_shape)
        ours = _dims(io.input_shape.dims) if io.input_shape is not None else "none"
        ok = theirs == ours
        message = (
            f"Input shape: {theirs} in the parent, {ours} here; resetting the head changes "
            "only the output, so a new model is needed"
        )
    checks.append(
        Check("Input", ok, theirs, ours, message if not ok else f"Input: {ours}", "init_from_input")
    )

    theirs_classes = [str(c) for c in lineage.classes]
    ours_classes = [str(c) for c in io.classes]
    same = theirs_classes == ours_classes
    checks.append(
        Check(
            "Output",
            same or bool(config.reset_head),
            _classes(theirs_classes),
            _classes(ours_classes),
            (
                f"Output: {_classes(ours_classes)}, as the parent"
                if same
                else f"Output: {_classes(theirs_classes)} in the parent, "
                f"{_classes(ours_classes)} here"
                + ("; the head is reset" if config.reset_head else "; reset the head to fit it")
            ),
            "init_from_output",
        )
    )

    identity = config.model.identity()
    same_spec = identity == lineage.model_spec
    checks.append(
        Check(
            "Spec",
            same_spec,
            lineage.name,
            config.model.display,
            "Spec: the parent's architecture"
            if same_spec
            else f"Spec: the model differs from {lineage.name}'s; the architecture is the "
            "parent's (its layers could not take its weights)",
            "init_from_spec",
        )
    )

    dataset = session.get(Dataset, version.dataset_id)
    assert dataset is not None
    dims = io.input_shape.dims if io.input_shape is not None else tuple(lineage.input_shape)
    lineage_id, _ = store.lineage_key(
        model_spec=identity, dataset_slug=dataset.slug, input_shape=dims, classes=ours_classes
    )
    existing = session.get(ModelLineage, lineage_id)
    name = existing.name if existing is not None else f"{config.model.display} · {dataset.slug}"
    return FineTune(parent, checks, lineage_id, name, lineage_id == lineage.id)


def refuse(fine_tune: FineTune) -> None:
    """``POST /jobs``'s refusal: one error per failing check, at ``config.init_from``."""
    failing = [c for c in fine_tune.checks if not c.ok]
    if not failing:
        return
    raise ValidationFailed(
        f"{fine_tune.parent.version.id} cannot start a run on this split: "
        + "; ".join(c.message for c in failing)
        + ".",
        suggested_action="Pick a split that matches the parent, reset the head if only the "
        "classes differ, or build a new model.",
        errors=[FieldProblem(loc=LOC_INIT, msg=c.message, code=c.code) for c in failing],
    )


# ---------------------------------------------------------------------------- freezing


def freeze_problems(config: Any, report: SpecReport | None) -> list[FieldProblem]:
    """Rule 7's refusals, each at ``config.freeze[i]``. ``report`` is the builder's report
    on the graph (``None`` for a reference model, which cannot be frozen)."""
    freeze: list[str] = list(config.freeze)
    if not freeze:
        return []
    if config.model.graph is None or report is None:
        return [
            FieldProblem(
                loc=["config", "freeze"],
                msg="a reference model has no node IDs; it can be fine-tuned and its head "
                "reset, but not frozen",
                code="freeze_unsupported",
            )
        ]
    weighted = {n.id for n in report.nodes if n.parameters}
    problems: list[FieldProblem] = []
    for i, node_id in enumerate(freeze):
        loc: list[str | int] = ["config", "freeze", i]
        if node_id == HEAD:
            if config.reset_head:
                problems.append(
                    FieldProblem(loc=loc, msg="a reset head must train", code="freeze_invalid")
                )
        elif node_id not in {n.id for n in report.nodes}:
            problems.append(
                FieldProblem(loc=loc, msg=f"no node {node_id!r}", code="freeze_invalid")
            )
        elif node_id not in weighted:
            problems.append(
                FieldProblem(
                    loc=loc, msg=f"node {node_id!r} has no weights to freeze", code="freeze_invalid"
                )
            )
    if not problems and weighted | {HEAD} <= set(freeze):
        problems.append(
            FieldProblem(
                loc=["config", "freeze"],
                msg="every layer with weights is frozen: nothing would train",
                code="freeze_invalid",
            )
        )
    return problems


def frozen_parameters(report: SpecReport, freeze: Sequence[str]) -> int:
    chosen = set(freeze)
    nodes = sum(n.parameters or 0 for n in report.nodes if n.id in chosen)
    head = report.head.parameters if report.head is not None and HEAD in chosen else 0
    return nodes + head


def trainable_memory(memory: Memory, total: int, frozen: int) -> Memory:
    """Rule 11: weights for every parameter; gradients and optimizer state only for the
    trainable ones. Activations as before (a safe over-count for a frozen prefix)."""
    if frozen <= 0 or total <= 0:
        return memory
    share = (total - frozen) / total
    gradients = round(memory.gradients_bytes * share)
    optimizer = round(memory.optimizer_bytes * share)
    return memory.model_copy(
        update={
            "gradients_bytes": gradients,
            "optimizer_bytes": optimizer,
            "total_bytes": memory.weights_bytes + gradients + optimizer,
        }
    )
