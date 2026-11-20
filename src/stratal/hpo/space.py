"""Tunable markers and the search space they make (HPO-1.1, ADR-0018 rule 2).

Plain Python: the API checks a space before it queues a study, and the worker
uses the same code to apply what Optuna samples.

A marker points into the study's base request with a JSON pointer:

* ``/classical/model/params/<field>``: a classical model's parameter;
* ``/config/<field>``: a network run's training field (learning rate, batch
  size, weight decay, optimizer, schedule, epochs, momentum);
* ``/config/model/graph/nodes/<node id>/layer/<field>``: a layer's field. In a
  list of objects with an ``id``, a segment names the element by its ID.

Each marker's range must lie inside its field's own (checked with the field's
own validator), and a marker none of whose values can ever make a valid request
with the fixed fields is refused. A combination that is invalid only for some
sampled values fails as a trial (``invalid_combination``) and the study goes on.
"""

import copy
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from stratal.build.layers import Layer
from stratal.core.errors import FieldProblem
from stratal.ml.params import ESTIMATORS
from stratal.train.config import TrainConfig

MarkerType = Literal["int", "float", "log_float", "categorical"]
NETWORK_FIELDS = (
    "epochs",
    "batch_size",
    "optimizer",
    "learning_rate",
    "momentum",
    "weight_decay",
    "schedule",
)
"""The training fields a study may tune; the model, device and checkpoints stay fixed."""


class Marker(BaseModel):
    """One tuned field: a range with a scale, or choices (HPO-1.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(pattern=r"^/[A-Za-z0-9_./-]+$", description="A JSON pointer into the base.")
    type: MarkerType
    low: float | None = None
    high: float | None = None
    step: float | None = Field(default=None, gt=0, description="int and float only.")
    choices: list[Any] | None = Field(default=None, min_length=2, max_length=50)

    @model_validator(mode="after")
    def _shape(self) -> "Marker":
        if self.type == "categorical":
            if self.choices is None or self.low is not None or self.high is not None:
                raise ValueError("a categorical marker has choices, and no low or high")
            if len({repr(c) for c in self.choices}) != len(self.choices):
                raise ValueError("a choice is given twice")
            return self
        if self.choices is not None or self.low is None or self.high is None:
            raise ValueError(f"a {self.type} marker has low and high, and no choices")
        if not (math.isfinite(self.low) and math.isfinite(self.high)) or self.low >= self.high:
            raise ValueError("low must be below high")
        if self.type == "log_float" and self.low <= 0:
            raise ValueError("a log scale needs a low above 0")
        if self.type == "log_float" and self.step is not None:
            raise ValueError("a log scale takes no step")
        if self.type == "int" and not (self.low.is_integer() and self.high.is_integer()):
            raise ValueError("an int marker has whole-number low and high")
        return self

    def ends(self) -> list[Any]:
        """The values that bound the marker: its choices, or low and high."""
        if self.choices is not None:
            return list(self.choices)
        assert self.low is not None and self.high is not None
        if self.type == "int":
            return [int(self.low), int(self.high)]
        return [self.low, self.high]


# ------------------------------------------------------------------ pointers


def _segments(path: str) -> list[str]:
    return [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]


def _step(node: Any, segment: str) -> Any:
    if isinstance(node, dict):
        mapping = cast(dict[str, Any], node)
        if segment not in mapping:
            raise KeyError(segment)
        return mapping[segment]
    if isinstance(node, list):
        items = cast(list[Any], node)
        for item in items:
            if isinstance(item, dict) and cast(dict[str, Any], item).get("id") == segment:
                return cast(dict[str, Any], item)
        if segment.isdigit() and int(segment) < len(items):
            return items[int(segment)]
    raise KeyError(segment)


def get_at(document: Any, path: str) -> Any:
    node = document
    for segment in _segments(path):
        node = _step(node, segment)
    return node


def set_at(document: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """A copy of ``document`` with ``value`` at ``path`` (the parent must exist)."""
    result = copy.deepcopy(document)
    *parents, last = _segments(path)
    node: Any = result
    for segment in parents:
        node = _step(node, segment)
    if not isinstance(node, dict):
        raise KeyError(last)
    node[last] = value
    return result


def apply(base: dict[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """``base`` with every sampled value set at its marker's path."""
    result = base
    for path, value in values.items():
        result = set_at(result, path, value)
    return result


# ------------------------------------------------------------------ fields


def _adapter(model: type[BaseModel], name: str) -> TypeAdapter[Any]:
    field = model.model_fields[name]
    form: Any = Annotated[field.annotation, field]  # the field's type and its limits
    return TypeAdapter(form)


def field_validator(base: Mapping[str, Any], path: str) -> TypeAdapter[Any]:
    """The validator of the field ``path`` names, or ``KeyError`` with why it cannot be tuned."""
    parts = _segments(path)
    if parts[:3] == ["classical", "model", "params"] and len(parts) == 4:
        estimator = str(base["classical"]["model"]["estimator"])
        params = ESTIMATORS[estimator].params
        if parts[3] not in params.model_fields:
            raise KeyError(f"{estimator} has no parameter {parts[3]!r}")
        return _adapter(params, parts[3])
    if parts[:1] == ["config"] and len(parts) == 2:
        if parts[1] not in NETWORK_FIELDS:
            raise KeyError(f"{parts[1]!r} is not a tunable training field")
        return _adapter(TrainConfig, parts[1])
    if (
        parts[:4] == ["config", "model", "graph", "nodes"]
        and len(parts) == 7
        and parts[5] == "layer"
    ):
        layer = get_at(base, "/" + "/".join(parts[:6]))
        kind = TypeAdapter(Layer).validate_python(layer)
        if parts[6] == "type" or parts[6] not in type(kind).model_fields:
            raise KeyError(f"node {parts[4]!r} ({layer.get('type')}) has no field {parts[6]!r}")
        return _adapter(type(kind), parts[6])
    raise KeyError("not a path a study can tune")


def check_space(
    markers: Sequence[Marker],
    base: dict[str, Any],
    valid: Callable[[dict[str, Any]], str | None],
    *,
    loc: str = "space",
) -> list[FieldProblem]:
    """Problems with ``markers`` on ``base``, each at ``[loc, i, …]`` (HPO-1.1, ADR-0018 rule 2).

    ``valid`` checks a whole request and returns why it is invalid, or ``None``.
    """
    problems: list[FieldProblem] = []
    seen: set[str] = set()
    for i, marker in enumerate(markers):
        where: list[str | int] = [loc, i]

        def refuse(msg: str, where: list[str | int] = where) -> None:
            problems.append(FieldProblem(loc=[*where, "path"], msg=msg, code="invalid"))

        if marker.path in seen:
            refuse(f"{marker.path} is tuned twice")
            continue
        seen.add(marker.path)
        try:
            adapter = field_validator(base, marker.path)
            get_at(base, marker.path)
        except (KeyError, ValueError, ValidationError) as exc:
            refuse(f"{marker.path}: {exc.args[0] if exc.args else exc}")
            continue
        outside: list[str] = []
        for value in marker.ends():
            try:
                adapter.validate_python(value)
            except ValidationError as exc:
                outside.append(f"{value!r}: {exc.errors()[0]['msg']}")
        if outside:
            refuse(f"{marker.path} goes outside its field's range: {'; '.join(outside)}")
            continue
        reasons = [valid(set_at(base, marker.path, value)) for value in marker.ends()]
        if all(reason is not None for reason in reasons):
            refuse(f"{marker.path}: no value is valid with the fixed fields: {reasons[0]}")
    return problems
