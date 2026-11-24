"""A dataset version's two locked ends: the model's input shape and output size (BUILD-1.3).

Plain Python: the API uses it on every validation, and the train stage uses the
same function, so the builder and training never disagree about a shape
(ADR-0017 rule 5). Versions never change once written (INV-01), so the result
is cached per version.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.build.shapes import Shape
from stratal.build.spec import GraphSpec, Optimizer, SpecReport, validate_graph
from stratal.core.errors import FieldProblem, StratalError, ValidationFailed
from stratal.data import registry
from stratal.data.dataset_types import TYPE_INFO, TypeInfo
from stratal.data.records import LABELLED_TEXT, TABULAR
from stratal.storage.manifest import STATUS_ACCEPTED, Row

IMAGE_COLUMNS = ["status", "split", "label", "width", "height", "derived_width", "derived_height"]
TEXT_COLUMNS = ["status", "split", "label"]
TRAINS_ON: dict[str, str] = {
    "labelled_images": "split",
    TABULAR: "split",
    LABELLED_TEXT: "tokenize",
}
"""The stage whose versions a network trains on, per type: texts need their tokens."""


@dataclass(frozen=True)
class VersionIo:
    version_id: str
    dataset_type: str
    info: TypeInfo
    input_shape: Shape | None
    output_size: int | None
    classes: tuple[str, ...]
    problem: str | None
    """Why this version cannot feed the builder, in words; ``None`` when it can."""


def image_shape(rows: Iterable[Mapping[str, Any]]) -> Shape:
    """The ``(3, H, W)`` input of a labelled-images split: every split row is one size."""
    sizes = {
        (int(r["derived_height"] or r["height"]), int(r["derived_width"] or r["width"]))
        for r in rows
        if r.get("status") == STATUS_ACCEPTED and r.get("split")
    }
    if len(sizes) != 1:
        raise StratalError(
            f"The images of this version come in {len(sizes)} sizes; training needs one.",
            suggested_action="Use a dataset whose images share one size; resizing for "
            "training is not built yet.",
        )
    height, width = sizes.pop()
    return Shape("image", (3, height, width))


def image_classes(rows: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Class names in label order: sorted, as the train stage numbers them."""
    return tuple(
        sorted(
            {str(r["label"]) for r in rows if r.get("status") == STATUS_ACCEPTED and r.get("split")}
        )
    )


def version_io(engine: Engine, data_dir: Path, version_id: str) -> VersionIo:
    return _cached(engine, str(data_dir), version_id)


@lru_cache(maxsize=64)
def _cached(engine: Engine, data_dir: str, version_id: str) -> VersionIo:
    with Session(engine) as session:
        version = registry.get_version(session, version_id)
        dataset_type = version.dtype or "image_caption"
        info = TYPE_INFO.get(dataset_type)
        if info is None:
            raise StratalError(f"{version_id} has an unknown dataset type {dataset_type!r}.")

        def ends(problem: str) -> VersionIo:
            return VersionIo(version_id, dataset_type, info, None, None, (), problem)

        trains_on = TRAINS_ON.get(dataset_type)
        if trains_on is None:
            return ends(
                info.note if not info.trainable else f"{info.title} has no builder input yet."
            )
        if version.stage != trains_on:
            return ends(
                f"{version_id} is a {version.stage} version; {info.title.lower()} models train "
                f"on a {trains_on} version."
            )
        if dataset_type == TABULAR:
            _, manifest = registry.manifest_for(session, Path(data_dir), version_id)
            return _table_ends(version_id, dataset_type, info, manifest)
        if dataset_type == LABELLED_TEXT:
            _, manifest = registry.manifest_for(session, Path(data_dir), version_id)
            rows = list(
                registry.load_version(session, Path(data_dir), version_id, columns=TEXT_COLUMNS)
            )
            return _text_ends(version_id, dataset_type, info, manifest, rows)
        rows = list(
            registry.load_version(session, Path(data_dir), version_id, columns=IMAGE_COLUMNS)
        )
    try:
        shape = image_shape(rows)
    except StratalError as exc:
        return ends(exc.detail)
    classes = image_classes(rows)
    if len(classes) < 2:
        return ends(f"{version_id} has {len(classes)} class; a classifier needs two or more.")
    return VersionIo(version_id, dataset_type, info, shape, len(classes), classes, None)


def _table_ends(version_id: str, dataset_type: str, info: TypeInfo, manifest: Path) -> VersionIo:
    """A table's ends: the input block's width from the train split, and the classes or one
    output for regression (ADR-0017 rule 6, ADR-0019 rules 13 and 14)."""
    from stratal.ml.data import load_tabular
    from stratal.train.table_prep import fit

    try:
        prep = fit(load_tabular(manifest))
    except StratalError as exc:
        return VersionIo(version_id, dataset_type, info, None, None, (), exc.detail)
    if prep.task == "classification" and len(prep.classes) < 2:
        return VersionIo(
            version_id,
            dataset_type,
            info,
            None,
            None,
            (),
            f"{version_id} has {len(prep.classes)} class; a classifier needs two or more.",
        )
    return VersionIo(
        version_id,
        dataset_type,
        info,
        Shape("vector", (prep.width,)),
        prep.outputs,
        prep.classes,
        None,
    )


def _text_ends(
    version_id: str,
    dataset_type: str,
    info: TypeInfo,
    manifest: Path,
    rows: list[Row],
) -> VersionIo:
    """A text's ends: L tokens from a vocabulary, both from the tokenize stage (ADR-0019
    rule 9), and its classes."""
    from stratal.data.tokenize import vocab_of

    vocab = vocab_of(manifest)
    if vocab is None:
        return VersionIo(
            version_id, dataset_type, info, None, None, (), f"{version_id} has no vocabulary."
        )
    classes = image_classes(rows)
    if len(classes) < 2:
        return VersionIo(
            version_id,
            dataset_type,
            info,
            None,
            None,
            (),
            f"{version_id} has {len(classes)} class; a classifier needs two or more.",
        )
    shape = Shape("tokens", (int(vocab["max_length"]),), vocab=int(vocab["size"]))
    return VersionIo(version_id, dataset_type, info, shape, len(classes), classes, None)


def graph_report(
    engine: Engine, data_dir: Path, graph: GraphSpec, version_id: str, optimizer: Optimizer
) -> SpecReport:
    """The builder's report on ``graph`` against this version's ends (ADR-0017 rule 4):
    problems, shapes, parameters and the memory estimate (rule 7)."""
    io = version_io(engine, data_dir, version_id)
    if io.input_shape is None or io.output_size is None:
        raise ValidationFailed(io.problem or f"{version_id} cannot feed the builder.")
    return validate_graph(
        graph,
        input_shape=io.input_shape,
        output_size=io.output_size,
        optimizer=optimizer,
        families=io.info.families,
    )


TRAIN_VRAM_FRACTION = 0.85
"""The share of the card a training process may allocate (the train stage's cap)."""
CONTEXT_MB = 256
"""What a CUDA context and the allocator's slack take beside a model."""
CARD_MB = 6141.0
"""The RTX 4050's memory, when no monitor sample says otherwise."""


def vram_problem(engine: Engine, data_dir: Path, config: Any, version_id: str) -> str | None:
    """Why a network's run would not fit on the GPU, or None (ADR-0017 rule 7, ADR-0018
    rule 2): the builder's estimate of one step at the run's batch size against the cap.
    A run on the CPU, or a reference model, is not estimated. ``config`` is a
    ``TrainConfig``."""
    from sqlalchemy import select

    from stratal.db.models import ResourceSample

    if config.device == "cpu" or config.model.graph is None:
        return None
    optimizer: Optimizer = (
        "adam" if config.optimizer == "adam" else "sgd_momentum" if config.momentum > 0 else "sgd"
    )
    report = graph_report(engine, data_dir, config.model.graph, version_id, optimizer)
    if report.memory is None:
        return None
    memory = report.memory
    if config.freeze:  # a fine-tune's frozen layers need no gradients or optimizer state
        from stratal.train.finetune import frozen_parameters, trainable_memory

        frozen = frozen_parameters(report, config.freeze)
        memory = trainable_memory(memory, report.total_parameters or 0, frozen)
    with Session(engine) as session:
        card = session.scalar(
            select(ResourceSample.vram_total_mb)
            .where(ResourceSample.vram_total_mb.is_not(None))
            .order_by(ResourceSample.ts.desc())
            .limit(1)
        )
    cap = float(card or CARD_MB) * TRAIN_VRAM_FRACTION
    batch = int(config.batch_size)
    need = (memory.total_bytes + memory.activation_bytes_per_sample * batch) / 2**20 + CONTEXT_MB
    if need <= cap:
        return None
    return f"one step at batch {batch} needs about {need:,.0f} MB; the GPU allows {cap:,.0f} MB"


def check_train_graph(
    engine: Engine, data_dir: Path, graph: GraphSpec, version_id: str, optimizer: Optimizer
) -> None:
    """Refuse a training request whose graph has problems (ADR-0017 rule 4), each as a
    field error under ``config.model.graph``."""
    report = graph_report(engine, data_dir, graph, version_id, optimizer)
    if report.valid:
        return
    raise ValidationFailed(
        "The model spec has problems: " + "; ".join(p.message for p in report.problems),
        suggested_action="Fix them on SCR-08 (POST /model-specs/validate lists them).",
        errors=[
            FieldProblem(
                loc=["config", "model", "graph", *([p.node_id] if p.node_id else [])],
                msg=p.message,
                code=p.code,
            )
            for p in report.problems
        ],
    )
