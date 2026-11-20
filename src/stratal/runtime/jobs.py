"""Creating, cancelling and retrying jobs, and the shape the API returns (API.md §3.3).

Functions here change rows inside the caller's transaction. Anything that must
happen after the commit (enqueueing a task, raising a control flag, notifying
live viewers) is returned to the caller or done through :func:`after_commit`.
"""

import contextlib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from stratal.core.bus import LIVE_GLOBAL, Bus, live_channel
from stratal.core.errors import (
    Conflict,
    FieldProblem,
    InvalidStateTransition,
    NotFound,
    SourceNotFound,
    ValidationFailed,
)
from stratal.data import registry
from stratal.db.models import Checkpoint, Dataset, DatasetVersion, Job, JobStage
from stratal.hpo.config import StudyRequest
from stratal.ml.params import ClassicalConfig
from stratal.mon.events import utc_now
from stratal.registry import store as model_registry
from stratal.runtime import stages as stage_registry
from stratal.runtime.states import (
    TERMINAL,
    Actor,
    check_transition,
    record_creation,
    transition,
)
from stratal.train.config import TrainConfig, TrainRequest

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


class FolderSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["folder"] = "folder"
    path: str = Field(description="Folder as the workers see it, e.g. /sources/coco10k.")


class PipelineRequest(BaseModel):
    """``POST /jobs`` with ``kind: pipeline``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["pipeline"]
    dataset: str | None = Field(
        default=None,
        description="Dataset slug; created if it does not exist. Required with 'source'; "
        "with 'from_version' it defaults to that version's dataset and must match it.",
    )
    source: FolderSource | None = Field(
        default=None, description="Start from a folder: the pipeline begins with ingest."
    )
    from_version: str | None = Field(
        default=None,
        description="Start from an existing version: the pipeline runs the stages after "
        "the one that made it.",
    )
    stages: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: dict[str, dict[str, Any]](),
        description="Per-stage overrides of the default config.",
    )
    until_stage: str | None = Field(default=None, description="Stop after this stage.")
    dataset_type: Literal["image_caption", "labelled_images", "tabular", "labelled_text"] | None = (
        Field(
            default=None,
            description="Decides the stages and their defaults (DATA-5.2). 'image_caption': ingest "
            "to evaluate. 'labelled_images': ingest, clean, preprocess, split, with labels from "
            "class folders or a CSV. 'tabular': ingest (one CSV or Parquet table, "
            "stages.ingest.table.target required), clean_table, split. 'labelled_text': ingest "
            "(JSONL or TXT), clean_text, split, tokenize. Null: the type of 'from_version' if it "
            "has one, else image_caption.",
        )
    )

    @model_validator(mode="after")
    def _one_start(self) -> "PipelineRequest":
        if (self.source is None) == (self.from_version is None):
            raise ValueError("give exactly one of 'source' and 'from_version'")
        if self.source is not None and self.dataset is None:
            raise ValueError("'dataset' is required when starting from 'source'")
        return self


@dataclass(frozen=True)
class Enqueue:
    """A task to send once the transaction that decided it has committed."""

    job_id: str
    idx: int
    queue: str


def config_hash(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def next_job_id(session: Session) -> str:
    number = session.execute(text("SELECT nextval('job_id_seq')")).scalar_one()
    return f"job_{int(number):04d}"


def create_job_from_plan(
    session: Session,
    *,
    kind: str,
    spec: dict[str, Any],
    plan: list[tuple[str, dict[str, Any]]],
    dataset_id: int | None = None,
    input_version_id: str | None = None,
    queues: dict[str, str] | None = None,
) -> tuple[Job, Enqueue]:
    """Insert a queued job with one stage per ``(name, config)`` in ``plan``.

    ``input_version_id`` is the version the first stage consumes, if any. ``queues``
    overrides a stage's queue by name (a training run on the CPU, ADR-0014 rule 8).
    """
    job = Job(
        id=next_job_id(session),
        kind=kind,
        state="queued",
        spec=spec,
        dataset_id=dataset_id,
        input_version_id=input_version_id,
        attempt=0,
        current_stage_idx=0,
    )
    session.add(job)
    session.flush()
    first_queue = "cpu"
    for idx, (name, config) in enumerate(plan):
        definition = stage_registry.get_stage(name)
        if definition is None:
            raise ValidationFailed(f"Unknown stage {name!r}.")
        queue = (queues or {}).get(name, definition.queue)
        if idx == 0:
            first_queue = queue
        session.add(
            JobStage(
                job_id=job.id,
                idx=idx,
                name=name,
                queue=queue,
                status="queued" if idx == 0 else "pending",
                config=config,
                config_hash=config_hash(config),
                input_version_id=input_version_id if idx == 0 else None,
                cache_hit=False,
            )
        )
    session.flush()
    record_creation(session, job)
    return job, Enqueue(job.id, 0, first_queue)


def _field_problems(stage: str, exc: ValidationError) -> list[FieldProblem]:
    return [
        FieldProblem(
            loc=["stages", stage, *error["loc"]],
            msg=error["msg"],
            code=error["type"],
        )
        for error in exc.errors()
    ]


def _start_from_version(
    session: Session, request: PipelineRequest
) -> tuple[str, str, str, str | None]:
    """For ``from_version``: the version ID, its dataset's slug, the stage that made it
    and its dataset type."""
    version_id = str(request.from_version)
    try:
        version = registry.get_version(session, version_id)
    except NotFound as exc:
        raise ValidationFailed(
            f"from_version {version_id!r} is not a known dataset version.",
            suggested_action="List versions with GET /datasets/{slug}/versions and use one.",
            errors=[FieldProblem(loc=["from_version"], msg="unknown version", code="not_found")],
        ) from exc
    dataset = session.get(Dataset, version.dataset_id)
    slug = dataset.slug if dataset is not None else ""
    if request.dataset is not None and request.dataset != slug:
        raise ValidationFailed(
            f"Version {version_id} belongs to dataset {slug!r}, not {request.dataset!r}.",
            suggested_action="Leave 'dataset' out, or name the version's own dataset.",
        )
    return version_id, slug, version.stage, version.dtype


def _dataset_type(request: PipelineRequest, version_dtype: str | None) -> str:
    """The pipeline's dataset type: as requested, else the input version's, else image_caption."""
    if request.dataset_type is None:
        return version_dtype or stage_registry.IMAGE_CAPTION
    # Versions of an image–caption pipeline have no type until the filter stage validates it.
    actual = version_dtype or stage_registry.IMAGE_CAPTION
    if request.from_version is not None and actual != request.dataset_type:
        raise ValidationFailed(
            f"Version {request.from_version} is {actual}, not {request.dataset_type}.",
            suggested_action="Leave 'dataset_type' out to use the version's own type.",
            errors=[FieldProblem(loc=["dataset_type"], msg="does not match", code="conflict")],
        )
    return request.dataset_type


def _stage_overrides(dtype: str, request: PipelineRequest, name: str) -> dict[str, Any]:
    """The type's defaults for stage ``name`` with the request's overrides on top."""
    return {
        **stage_registry.TYPE_DEFAULTS.get(dtype, {}).get(name, {}),
        **request.stages.get(name, {}),
    }


_INGEST_KIND: dict[str, str | None] = {
    "image_caption": None,
    "labelled_images": "label_from",
    "tabular": "table",
    "labelled_text": "text",
}
"""The ingest field each dataset type needs set (and every other type leaves unset)."""


def _label_problems(dtype: str, plan: list[tuple[str, dict[str, Any]]]) -> list[FieldProblem]:
    """Ingest's labels, table or text settings must match the dataset type (DATA-5.2)."""
    wanted = _INGEST_KIND.get(dtype)
    for name, config in plan:
        if name != "ingest":
            continue
        for field_name in ("label_from", "table", "text"):
            given = config.get(field_name) is not None
            if given == (field_name == wanted):
                continue
            msg = (
                f"{dtype} needs stages.ingest.{field_name}"
                if field_name == wanted
                else f"{field_name} is only for dataset_type "
                f"{next(t for t, f in _INGEST_KIND.items() if f == field_name)}"
            )
            return [FieldProblem(loc=["stages", "ingest", field_name], msg=msg, code="invalid")]
    return []


def create_pipeline_job(session: Session, request: PipelineRequest) -> tuple[Job, Enqueue]:
    """Validate a pipeline request, resolve every stage's config and queue the job."""
    available = stage_registry.registered()
    input_version_id: str | None = None
    version_dtype: str | None = None
    made_by: str | None = None
    if request.from_version is not None:
        input_version_id, slug, made_by, version_dtype = _start_from_version(session, request)
    else:
        slug = str(request.dataset)
    dtype = _dataset_type(request, version_dtype)
    order = stage_registry.PIPELINE_ORDERS[dtype]
    names = [name for name in order if name in available]
    all_names = list(names)
    if made_by is not None:
        if made_by not in order:
            raise ValidationFailed(
                f"Version {input_version_id} was made by {made_by!r}, which a {dtype} "
                "pipeline does not run.",
                suggested_action=f"A {dtype} pipeline runs: {', '.join(order)}.",
            )
        names = [n for n in names if order.index(n) > order.index(made_by)]
        if not names:
            raise ValidationFailed(
                f"Version {input_version_id} was made by {made_by!r}, the last stage available; "
                "there is nothing after it to run.",
                suggested_action=f"Stages available now: {', '.join(all_names)}.",
            )
    if not _SLUG.match(slug):
        raise ValidationFailed(
            f"Dataset name {slug!r} is not a valid slug.",
            suggested_action="Use 2 to 63 lowercase letters, digits, '-' or '_'.",
        )
    unknown = sorted(set(request.stages) - set(names))
    if unknown:
        raise ValidationFailed(
            f"Unknown, not yet available or not run here: stage(s) {', '.join(unknown)}.",
            suggested_action=f"Stages this request would run: {', '.join(names)}.",
        )
    if request.until_stage is not None:
        if request.until_stage not in names:
            raise ValidationFailed(
                f"until_stage {request.until_stage!r} is not one of the stages this request runs.",
                suggested_action=f"Stages this request would run: {', '.join(names)}.",
            )
        names = names[: names.index(request.until_stage) + 1]
    if request.source is not None and not Path(request.source.path).is_dir():
        raise SourceNotFound(
            f"Source folder {request.source.path} does not exist or is not a folder.",
            suggested_action="Put the folder under the mounted sources directory "
            "(/sources in the containers) and use that path.",
        )

    plan: list[tuple[str, dict[str, Any]]] = []
    problems: list[FieldProblem] = []
    for name in names:
        try:
            model = available[name].config_model.model_validate(
                _stage_overrides(dtype, request, name)
            )
        except ValidationError as exc:
            problems.extend(_field_problems(name, exc))
            continue
        plan.append((name, model.model_dump(mode="json")))
    problems.extend(_label_problems(dtype, plan))
    if problems:
        raise ValidationFailed(
            "A stage config did not pass validation.",
            suggested_action="Fix the fields listed in 'errors'.",
            errors=problems,
        )

    dataset = registry.ensure_dataset(
        session,
        slug=slug,
        modality=stage_registry.modality_of(dtype),
        source_uri=request.source.path if request.source is not None else None,
    )
    spec = request.model_dump(mode="json")
    spec["dataset"] = slug
    spec["dataset_type"] = dtype
    spec["stages"] = dict(plan)
    return create_job_from_plan(
        session,
        kind="pipeline",
        spec=spec,
        plan=plan,
        dataset_id=dataset.id,
        input_version_id=input_version_id,
    )


def create_train_job(session: Session, request: TrainRequest) -> tuple[Job, Enqueue]:
    """Validate a training request and queue a one-stage ``train`` job (RUN-1.1, ADR-0014).

    The dataset version must be a split of a labelled-images dataset. A run on
    ``device: cpu`` goes to the CPU queue; ``gpu`` and ``auto`` to the GPU queue.
    """
    version_id = request.dataset_version
    loc: list[str | int] = ["dataset_version"]
    try:
        version = registry.get_version(session, version_id)
    except NotFound as exc:
        raise ValidationFailed(
            f"dataset_version {version_id!r} is not a known dataset version.",
            suggested_action="List versions with GET /datasets/{slug}/versions and use one.",
            errors=[FieldProblem(loc=loc, msg="unknown version", code="not_found")],
        ) from exc
    if request.classical is not None:
        return _create_classical_job(session, request.classical, version)
    assert request.config is not None
    resolved = check_network(session, version, request.config)
    return _create_network_job(session, version, resolved, kind="train", spec={"kind": "train"})


def check_network(session: Session, version: DatasetVersion, config: TrainConfig) -> TrainConfig:
    """A network's run checked against its dataset version, as ``POST /jobs`` does
    (RUN-1.1, ADR-0017 rule 4), with the checkpoint interval fixed into it. Raises
    :class:`ValidationFailed` naming the field."""
    from stratal.build.io import TRAINS_ON

    version_id = version.version_id
    loc: list[str | int] = ["dataset_version"]
    trains_on = TRAINS_ON.get(version.dtype or "")
    if trains_on is None or version.stage != trains_on:
        made = f"a {version.stage} version of type {version.dtype or 'none'}"
        raise ValidationFailed(
            f"Version {version_id} is {made}; a network trains on a split of labelled images "
            "or of a table, or on a tokenize version of labelled text (ADR-0019 rule 12).",
            suggested_action="Run the dataset's pipeline to the end and train on its output.",
            errors=[FieldProblem(loc=loc, msg="not a trainable version", code="invalid")],
        )
    if config.model.graph is None and version.dtype != stage_registry.LABELLED_IMAGES:
        raise ValidationFailed(
            f"The reference model {config.model.name} takes images; {version_id} is "
            f"{version.dtype}.",
            suggested_action="Build a model for this dataset on SCR-08, or start from one of "
            "its templates.",
            errors=[
                FieldProblem(loc=["config", "model", "name"], msg="images only", code="invalid")
            ],
        )
    resolved = config
    if resolved.model.graph is not None:  # a builder spec: checked against the version's ends
        from stratal.build.io import check_train_graph
        from stratal.core.settings import get_settings
        from stratal.db.engine import get_engine

        optimizer = (
            "adam"
            if resolved.optimizer == "adam"
            else "sgd_momentum"
            if resolved.momentum > 0
            else "sgd"
        )
        check_train_graph(
            get_engine(), get_settings().data_dir, resolved.model.graph, version_id, optimizer
        )
    if "checkpoint_every_s" not in resolved.model_fields_set:
        # The default comes from the settings saved on SCR-14 (OC-9), fixed into the
        # run's config now, so a later change never alters a running job (NFR-4).
        from stratal.core.settings import get_settings
        from stratal.runtime import limits

        minutes = limits.read(session, get_settings()).checkpoint_interval_min
        resolved = resolved.model_copy(update={"checkpoint_every_s": 60 * minutes})
    return resolved


def _create_network_job(
    session: Session,
    version: DatasetVersion,
    resolved: TrainConfig,
    *,
    kind: str,
    spec: dict[str, Any],
) -> tuple[Job, Enqueue]:
    dataset = session.get(Dataset, version.dataset_id)
    config = resolved.model_dump(mode="json")
    full: dict[str, Any] = {
        **spec,
        "dataset": dataset.slug if dataset is not None else None,
        "dataset_version": version.version_id,
        "stages": {"train": config},
    }
    return create_job_from_plan(
        session,
        kind=kind,
        spec=full,
        plan=[("train", config)],
        dataset_id=version.dataset_id,
        input_version_id=version.version_id,
        queues={"train": "cpu" if resolved.device == "cpu" else "gpu"},
    )


def create_trial_job(
    session: Session,
    *,
    study_job_id: str,
    study_id: str,
    number: int,
    version: DatasetVersion,
    config: TrainConfig,
) -> tuple[Job, Enqueue]:
    """A deep-learning study's trial: a ``train`` run of kind ``hpo_trial`` that knows its
    study and number (ADR-0018 rule 5). ``config`` was checked by :func:`check_network`."""
    return _create_network_job(
        session,
        version,
        config,
        kind="hpo_trial",
        spec={
            "kind": "hpo_trial",
            "study_id": study_id,
            "study_job": study_job_id,
            "trial": number,
        },
    )


def network_task(session: Session, version: DatasetVersion) -> str:
    """What a network on this version learns: a table's target decides; images and texts
    are classified (ADR-0019 rule 12)."""
    if version.dtype == stage_registry.TABULAR:
        return str(_tabular_data(session, version).task)
    return "classification"


def _tabular_data(session: Session, version: DatasetVersion) -> Any:
    """A tabular split's data, or ``validation_error`` at ``dataset_version``."""
    from stratal.core.settings import get_settings
    from stratal.ml.data import load_tabular

    version_id = version.version_id
    if version.dtype != stage_registry.TABULAR or version.stage != "split":
        made = f"a {version.stage} version of type {version.dtype or 'none'}"
        raise ValidationFailed(
            f"Version {version_id} is {made}; a classical model needs a split of a tabular "
            "dataset.",
            suggested_action="Run a tabular pipeline through the split stage and train on it.",
            errors=[
                FieldProblem(loc=["dataset_version"], msg="not a tabular split", code="invalid")
            ],
        )
    _, manifest = registry.manifest_for(session, get_settings().data_dir, version_id)
    return load_tabular(manifest)


def _classical_problems(config: ClassicalConfig, data: Any) -> list[FieldProblem]:
    """What makes a classical model unfit for a table (ML-1.4), each at its field."""
    from stratal.ml.params import check_data, check_task

    spec = config.model.resolved()
    problems: list[FieldProblem] = []
    wrong_task = check_task(spec.estimator, data.task)
    if wrong_task is not None:
        problems.append(
            FieldProblem(loc=["classical", "model", "estimator"], msg=wrong_task, code="invalid")
        )
    for field_name, reason in check_data(spec, data.facts()):
        problems.append(
            FieldProblem(
                loc=["classical", "model", "params", field_name], msg=reason, code="invalid"
            )
        )
    return problems


def _create_classical_job(
    session: Session, config: ClassicalConfig, version: DatasetVersion
) -> tuple[Job, Enqueue]:
    """A classical model's run on a tabular split, on the CPU queue (ML-1.1, ML-1.4)."""
    version_id = version.version_id
    problems = _classical_problems(config, _tabular_data(session, version))
    if problems:
        raise ValidationFailed(
            "The classical model does not fit this dataset (ML-1.4).",
            suggested_action="Fix the fields listed in 'errors'.",
            errors=problems,
        )
    spec = config.model.resolved()
    resolved = config.model_copy(update={"model": spec}).model_dump(mode="json")
    dataset = session.get(Dataset, version.dataset_id)
    spec_json: dict[str, Any] = {
        "kind": "train",
        "dataset": dataset.slug if dataset is not None else None,
        "dataset_version": version_id,
        "stages": {"train_classical": resolved},
    }
    return create_job_from_plan(
        session,
        kind="train",
        spec=spec_json,
        plan=[("train_classical", resolved)],
        dataset_id=version.dataset_id,
        input_version_id=version_id,
        queues={"train_classical": "cpu"},
    )


def create_study_job(session: Session, request: StudyRequest) -> tuple[Job, Enqueue]:
    """An HPO study: a one-stage ``hpo_study`` job and its ``studies`` row (HPO-1.1 to
    HPO-1.4, ADR-0018 rule 1). The base run is checked as a single run would be; the space
    against each field's own range and the fixed fields (ADR-0018 rule 2)."""
    from stratal.hpo import space as hpo_space
    from stratal.hpo.config import objectives
    from stratal.hpo.store import create_study

    version_id = request.dataset_version
    try:
        version = registry.get_version(session, version_id)
    except NotFound as exc:
        raise ValidationFailed(
            f"dataset_version {version_id!r} is not a known dataset version.",
            errors=[FieldProblem(loc=["dataset_version"], msg="unknown version", code="not_found")],
        ) from exc
    if request.config is not None:
        return _create_deep_study_job(session, request, version)
    assert request.classical is not None
    data = _tabular_data(session, version)
    problems = _classical_problems(request.classical, data)
    if problems:
        raise ValidationFailed(
            "The base run does not fit this dataset (ML-1.4).",
            suggested_action="Fix the fields listed in 'errors'.",
            errors=problems,
        )
    if data.task not in ("classification", "regression"):
        raise ValidationFailed("Studies tune supervised models.")
    from stratal.ml.params import check_task, task_for

    estimator = request.classical.model.estimator
    if task_for(estimator, data.task) not in ("classification", "regression"):
        raise ValidationFailed(
            f"{estimator} has no validation score to tune; studies tune classifiers and "
            "regressors (HPO-1.4).",
            errors=[
                FieldProblem(
                    loc=["classical", "model", "estimator"], msg="unsupervised", code="invalid"
                )
            ],
        )
    choices = objectives("classical", data.task)
    objective = request.objective or next(iter(choices))
    if objective not in choices:
        raise ValidationFailed(
            f"objective {objective!r} does not suit a classical {data.task}; choose one of "
            f"{', '.join(choices)}.",
            errors=[FieldProblem(loc=["objective"], msg="unknown objective", code="invalid")],
        )
    base = {
        "classical": request.classical.model_copy(
            update={"model": request.classical.model.resolved()}
        ).model_dump(mode="json")
    }

    def invalid(document: dict[str, Any]) -> str | None:
        try:
            config = ClassicalConfig.model_validate(document["classical"])
        except ValidationError as exc:
            return str(exc.errors()[0]["msg"])
        found = _classical_problems(config, data)
        wrong = check_task(config.model.estimator, data.task)
        return found[0]["msg"] if found else wrong

    space_problems = hpo_space.check_space(request.space, base, invalid)
    if space_problems:
        raise ValidationFailed(
            "The search space has problems (HPO-1.1).",
            suggested_action="Fix the markers listed in 'errors'.",
            errors=space_problems,
        )
    dataset = session.get(Dataset, version.dataset_id)
    spec: dict[str, Any] = {
        **request.model_dump(mode="json", exclude={"classical", "config"}),
        "base": base,
        "objective": objective,
        "direction": choices[objective],
        "study_kind": request.study_kind,
        "task": data.task,
        "dataset": dataset.slug if dataset is not None else None,
    }
    job, enqueue = create_job_from_plan(
        session,
        kind="hpo_study",
        spec=spec,
        plan=[("study", spec)],
        dataset_id=version.dataset_id,
        input_version_id=version_id,
        queues={"study": "cpu"},
    )
    study_id = create_study(session, job_id=job.id, spec=spec)
    job.spec = {**job.spec, "study_id": study_id}
    stage = session.get(JobStage, (job.id, 0))
    assert stage is not None
    stage.config = {**stage.config, "study_id": study_id}
    return job, enqueue


def _create_deep_study_job(
    session: Session, request: StudyRequest, version: DatasetVersion
) -> tuple[Job, Enqueue]:
    """A study of a network: a ``study`` stage advanced in short steps on the CPU queue,
    one ``hpo_trial`` job per trial, then ``test_eval`` (ADR-0018 rules 5 and 8)."""
    from stratal.hpo import space as hpo_space
    from stratal.hpo.config import objectives
    from stratal.hpo.store import create_study

    assert request.config is not None
    resolved = check_network(session, version, request.config)
    task = network_task(session, version)
    choices = objectives("deep", task)
    objective = request.objective or next(iter(choices))
    if objective not in choices:
        raise ValidationFailed(
            f"objective {objective!r} does not suit a network's {task}; choose one of "
            f"{', '.join(choices)}.",
            errors=[FieldProblem(loc=["objective"], msg="unknown objective", code="invalid")],
        )
    base = {"config": resolved.model_dump(mode="json")}

    def invalid(document: dict[str, Any]) -> str | None:
        try:
            check_network(session, version, TrainConfig.model_validate(document["config"]))
        except ValidationError as exc:
            return str(exc.errors()[0]["msg"])
        except ValidationFailed as exc:
            return str(exc.errors[0]["msg"]) if exc.errors else exc.detail
        return None

    space_problems = hpo_space.check_space(request.space, base, invalid)
    if space_problems:
        raise ValidationFailed(
            "The search space has problems (HPO-1.1).",
            suggested_action="Fix the markers listed in 'errors'.",
            errors=space_problems,
        )
    dataset = session.get(Dataset, version.dataset_id)
    spec: dict[str, Any] = {
        **request.model_dump(mode="json", exclude={"classical", "config"}),
        "base": base,
        "objective": objective,
        "direction": choices[objective],
        "study_kind": "deep",
        "task": task,
        "dataset": dataset.slug if dataset is not None else None,
    }
    job, enqueue = create_job_from_plan(
        session,
        kind="hpo_study",
        spec=spec,
        plan=[("study", spec), ("test_eval", {})],
        dataset_id=version.dataset_id,
        input_version_id=version.version_id,
        queues={"study": "cpu", "test_eval": "cpu" if resolved.device == "cpu" else "gpu"},
    )
    study_id = create_study(session, job_id=job.id, spec=spec)
    job.spec = {**job.spec, "study_id": study_id}
    for stage in stages_of(session, job.id):
        stage.config = {**stage.config, "study_id": study_id}
    return job, enqueue


def get_job(session: Session, job_id: str, *, lock: bool = False) -> Job:
    job = session.get(Job, job_id, with_for_update=lock)
    if job is None:
        raise NotFound(f"No job {job_id!r}.")
    return job


def stages_of(session: Session, job_id: str) -> list[JobStage]:
    query = select(JobStage).where(JobStage.job_id == job_id).order_by(JobStage.idx)
    return list(session.scalars(query))


def cancel_unfinished_stages(session: Session, job_id: str) -> None:
    for stage in stages_of(session, job_id):
        if stage.status in ("pending", "queued", "running", "paused", "waiting"):
            stage.status = "cancelled"
            stage.finished_at = stage.finished_at or utc_now()


def request_cancel(session: Session, job_id: str) -> tuple[Job, bool]:
    """Cancel a job. Returns the job and whether a running child must be told.

    A job that is not running is cancelled at once. A running one gets
    ``desired_state = cancelled`` and stops at its next safe point; the
    supervisor makes the transition (ARCHITECTURE.md §4.3).
    """
    job = get_job(session, job_id, lock=True)
    check_transition(job.id, job.state, "cancelled")
    waiting = any(s.status == "waiting" for s in stages_of(session, job_id))
    if job.state in ("running", "stalled") and not waiting:  # a child is at work: it stops
        job.desired_state = "cancelled"
        return job, True
    transition(session, job, "cancelled", reason="cancelled by the user", actor="user")
    cancel_unfinished_stages(session, job_id)
    return job, False


def request_pause(
    session: Session, job_id: str, *, reason: Literal["user", "thermal"] = "user"
) -> Job:
    """Ask a running job to pause (RUN-1.2, ADR-0014 rule 6).

    Sets ``desired_state = paused``. The stage stops at its next safe point (a
    training run writes a checkpoint first) and the supervisor makes the
    transition; a job between stages pauses before its next stage starts.
    """
    job = get_job(session, job_id, lock=True)
    check_transition(job.id, job.state, "paused")
    if job.desired_state == "cancelled":
        raise Conflict(
            f"Job {job_id} is being cancelled.",
            suggested_action="Wait for the cancellation to finish.",
        )
    job.desired_state = "paused"
    job.pause_reason = reason
    if job.kind == "hpo_study":  # a study waiting for its trial pauses through it
        from stratal.hpo.control import forward_pause

        forward_pause(session, job)
    return job


def signal_targets(session: Session, job: Job, *, running: bool) -> list[str]:
    """The jobs whose running process must hear a pause or cancel through Redis: the job,
    if a child runs it, and a study's current trial (ADR-0018 rule 5)."""
    from stratal.hpo.control import trial_to_signal

    return ([job.id] if running else []) + trial_to_signal(session, job)


def request_resume(
    session: Session, job_id: str, *, actor: Actor = "user", reason: str = "resumed by the user"
) -> tuple[Job, Enqueue]:
    """Requeue a paused job from the stage that was paused (RUN-1.2); the watchdog also
    resumes jobs it paused for heat (RUN-1.6)."""
    job = get_job(session, job_id, lock=True)
    if job.state != "paused":
        raise InvalidStateTransition(
            f"Job {job_id} is {job.state}, not paused.",
            suggested_action="Only a paused job can be resumed; a failed one is retried.",
        )
    stages = stages_of(session, job_id)
    resume = next((s for s in stages if s.status == "paused"), None) or next(
        (s for s in stages if s.status not in ("completed", "skipped")), None
    )
    if resume is None:
        raise ValidationFailed(f"Job {job_id} has no stage left to resume.")
    resume.status = "queued"
    resume.error = None
    resume.finished_at = None
    job.current_stage_idx = resume.idx
    job.desired_state = None
    job.pause_reason = None
    transition(session, job, "queued", reason=reason, actor=actor)
    return job, Enqueue(job.id, resume.idx, resume.queue)


def request_retry(session: Session, job_id: str) -> tuple[Job, Enqueue]:
    """Requeue a failed job from its first unfinished stage; finished stages are kept."""
    job = get_job(session, job_id, lock=True)
    check_transition(job.id, job.state, "queued")
    stages = stages_of(session, job_id)
    resume = next((s for s in stages if s.status not in ("completed", "skipped")), None)
    if resume is None:
        raise ValidationFailed(f"Job {job_id} has no unfinished stage to retry.")
    for stage in stages:
        if stage.idx == resume.idx:
            stage.status = "queued"
            stage.error = None
            stage.finished_at = None
        elif stage.idx > resume.idx:
            stage.status = "pending"
    job.error = None
    job.current_stage_idx = resume.idx
    transition(session, job, "queued", reason="retry requested by the user", actor="user")
    return job, Enqueue(job.id, resume.idx, resume.queue)


def after_commit(bus: Bus, job_id: str) -> None:
    """Tell live viewers that a job or its events changed. Never raises (NFR-13)."""
    with contextlib.suppress(Exception):
        for kind in ("event", "job"):
            message = {"kind": kind, "job_id": job_id}
            bus.publish(live_channel(job_id), message)
            bus.publish(LIVE_GLOBAL, message)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def stage_view(stage: JobStage) -> dict[str, Any]:
    return {
        "idx": stage.idx,
        "name": stage.name,
        "queue": stage.queue,
        "status": stage.status,
        "config": stage.config,
        "input_version_id": stage.input_version_id,
        "output_version_id": stage.output_version_id,
        "cache_hit": stage.cache_hit,
        "step": stage.step,
        "total": stage.total,
        "throughput": stage.throughput,
        "eta_seconds": stage.eta_seconds,
        "activity": stage.activity,
        "last_progress_at": _iso(stage.last_progress_at),
        "metrics": stage.metrics,
        "peaks": stage.peaks,
        "started_at": _iso(stage.started_at),
        "finished_at": _iso(stage.finished_at),
        "error": stage.error,
    }


def job_view(session: Session, job: Job) -> dict[str, Any]:
    """A job with its stages, as the API returns it."""
    stages = stages_of(session, job.id)
    current = next((s.name for s in stages if s.idx == job.current_stage_idx), None)
    version = model_registry.run_version(session, job.id) if job.kind == "train" else None
    return {
        "id": job.id,
        "kind": job.kind,
        "state": job.state,
        "desired_state": job.desired_state,
        "pause_reason": job.pause_reason,
        "heartbeat_at": _iso(job.heartbeat_at),
        "attempt": job.attempt,
        "dataset": job.spec.get("dataset"),
        "current_stage": current if job.state not in TERMINAL else None,
        "stages": [stage_view(stage) for stage in stages],
        "output_version_id": job.output_version_id,
        "model_version_id": version.id if version is not None else None,
        "model_lineage_id": version.lineage_id if version is not None else None,
        "error": job.error,
        "summary": job.summary,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }


def create_report_job(
    session: Session, version_id: str, config: dict[str, Any]
) -> tuple[Job, Enqueue]:
    """Queue a one-stage job that reports on ``version_id`` (EVAL-1.1, API.md §3.6)."""
    from stratal.eval.report import EvaluateConfig
    from stratal.label import review as reviews

    version = registry.get_version(session, version_id)
    try:
        resolved = EvaluateConfig.model_validate(config)
    except ValidationError as exc:
        raise ValidationFailed(
            "The report request did not pass validation.",
            suggested_action="Fix the fields listed in 'errors'.",
            errors=_field_problems("evaluate", exc),
        ) from None
    if resolved.review_id is not None:
        reviews.review_for_report(session, version_id, resolved.review_id)
    plan = [("evaluate", resolved.model_dump(mode="json"))]
    spec: dict[str, Any] = {"kind": "report", "version_id": version_id, "stages": dict(plan)}
    return create_job_from_plan(
        session,
        kind="report",
        spec=spec,
        plan=plan,
        dataset_id=version.dataset_id,
        input_version_id=version_id,
    )


# -- registry jobs (M8, REG-2.2 to REG-2.10, ADR-0015) -----------------------------


def create_retention_job(
    session: Session,
    *,
    lineage_ids: list[str] | None = None,
    dry_run: bool = False,
    confirmed: list[str] | None = None,
    reason: str = "requested",
) -> tuple[Job, Enqueue]:
    """Queue a one-stage ``retention`` job on the CPU queue (REG-2.9)."""
    config = stage_registry.RetentionConfig(
        lineage_ids=lineage_ids, dry_run=dry_run, confirmed=confirmed, reason=reason
    ).model_dump(mode="json")
    spec: dict[str, Any] = {
        "kind": "retention",
        "lineage_ids": lineage_ids,
        "dry_run": dry_run,
        "reason": reason,
        "stages": {"retention": config},
    }
    return create_job_from_plan(session, kind="retention", spec=spec, plan=[("retention", config)])


def retention_after_run(session: Session, job: Job) -> Enqueue | None:
    """After a training run completes or is cancelled, clean its lineage (REG-2.2, REG-2.6).
    ``None`` when the run never reached a lineage."""
    if job.kind != "train" or job.state not in ("completed", "cancelled"):
        return None
    version = model_registry.run_version(session, job.id)
    lineage_id = version.lineage_id if version is not None else None
    if lineage_id is None:
        checkpoint = session.get(Checkpoint, job.id)
        lineage_id = checkpoint.lineage_id if checkpoint is not None else None
    if lineage_id is None:
        return None
    _, enqueue = create_retention_job(
        session, lineage_ids=[lineage_id], reason=f"after {job.id} {job.state}"
    )
    return enqueue


def create_retrain_job(session: Session, version_id: str) -> tuple[Job, Enqueue]:
    """A new run with a version's exact config and dataset version (REG-2.7)."""
    version = model_registry.get_version(session, version_id)
    if version.state != "trained" or version.dataset_version_id is None:
        raise Conflict(
            f"{version_id} is a {version.state} version; only trained versions are re-trained.",
            suggested_action="Re-train a trained version, or start a new run.",
        )
    model_spec: dict[str, Any] = dict(version.config.get("model") or {})
    classical = model_spec.get("kind") == "sklearn"
    request = TrainRequest(
        kind="train",
        dataset_version=version.dataset_version_id,
        config=None if classical else TrainConfig.model_validate(version.config),
        classical=ClassicalConfig.model_validate(version.config) if classical else None,
    )
    job, enqueue = create_train_job(session, request)
    job.spec = {**job.spec, "retrain_of": version_id}
    return job, enqueue
