# pyright: basic, reportMissingImports=false
# PyTorch and scikit-learn exist only in the worker images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""Tables and texts at ``/predict`` (ADR-0019 rules 11, 13, 20 and 21).

* :func:`check_record` checks one record against the version's table schema:
  every feature present (a value may be null, and is then imputed), the target
  absent, no unknown field, numbers finite, categories as strings.
* :class:`ClassicalModel` scores a skops pipeline: classes with probabilities
  (or the class alone when the estimator gives none), a value, a k-means
  cluster, or PCA's components. DBSCAN and HDBSCAN are refused: they cannot
  place a new point.
* :func:`table_tensor` and :func:`text_tensor` turn a record or a text into a
  network's input with the version's own fitted encoding or vocabulary, the
  very code training used.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import torch

from stratal.core.errors import NotServable, WrongInput
from stratal.data.records import normalise_text
from stratal.data.tables import coerce
from stratal.data.tokenize import Vocabulary
from stratal.ml.data import records_table
from stratal.train.table_prep import TablePrep

TOP_K = 5


def check_record(record: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    """The record's features, coerced to their kinds, or :class:`WrongInput` naming the field."""
    columns = list(schema["columns"])
    features = {c["name"]: c["kind"] for c in columns if c["role"] == "feature"}
    target = next(c["name"] for c in columns if c["role"] == "target")
    problems: list[tuple[str, str]] = []
    if target in record:
        problems.append((target, "the target is what the model predicts; leave it out"))
    for name in sorted(set(record) - set(features) - {target}):
        problems.append((name, "not a column of this model's table"))
    values: dict[str, Any] = {}
    for name, kind in features.items():
        if name not in record:
            problems.append((name, "missing; send null to have it imputed"))
            continue
        value = coerce(record[name], kind)
        if (
            value is not None
            and kind == "numeric"
            and (not isinstance(value, float) or not math.isfinite(value))
        ):
            problems.append((name, f"{record[name]!r} is not a finite number"))
        elif value is not None and kind == "categorical" and not isinstance(value, str):
            problems.append((name, f"{record[name]!r} is not a category"))
        values[name] = value
    if problems:
        raise WrongInput(
            "The record does not fit this model's table: "
            + "; ".join(f"{name}: {why}" for name, why in problems),
            suggested_action="Send one value per feature column, by name, without the target.",
            errors=[
                {"loc": ["body", "record", name], "msg": why, "code": "wrong_input"}
                for name, why in problems
            ],
        )
    return values


def top_classes(probabilities: list[float], classes: list[str]) -> list[dict[str, Any]]:
    order = sorted(range(len(classes)), key=lambda i: -probabilities[i])[:TOP_K]
    return [{"class": classes[i], "probability": round(float(probabilities[i]), 6)} for i in order]


@dataclass
class ClassicalModel:
    """A loaded skops pipeline and what its ``model.json`` says of it."""

    pipeline: Any
    meta: dict[str, Any]

    def predict(self, record: Mapping[str, Any]) -> dict[str, Any]:
        features = self.meta["features"]
        table = records_table([record], features["numeric"], features["categorical"])
        task = self.meta["task"]
        estimator = self.meta["model"]["estimator"]
        if task == "classification":
            model = self.pipeline.named_steps["model"]
            if hasattr(model, "predict_proba"):
                probabilities = [float(p) for p in self.pipeline.predict_proba(table)[0]]
                classes = [str(c) for c in self.pipeline.classes_]
                return {"task": task, "top": top_classes(probabilities, classes)}
            guess = str(self.pipeline.predict(table)[0])
            return {"task": task, "top": [{"class": guess, "probability": None}]}
        if task == "regression":
            return {"task": task, "value": float(self.pipeline.predict(table)[0])}
        if task == "projection":
            return {
                "task": task,
                "components": [float(v) for v in self.pipeline.transform(table)[0]],
            }
        if estimator != "kmeans":
            raise NotServable(
                f"{estimator} assigns clusters only to the rows it was fitted on; it cannot "
                "place a new record (ADR-0019 rule 21).",
                suggested_action="Read its clusters on the version's page, or train k-means "
                "to assign new records.",
            )
        return {"task": task, "cluster": int(self.pipeline.predict(table)[0])}


def table_tensor(record: Mapping[str, Any], prep: TablePrep) -> torch.Tensor:
    """One record as the network's encoded input, (1, F)."""
    return torch.tensor([prep.encode_record(record)], dtype=torch.float32)


def text_tensor(text: str, vocab: Vocabulary, length: int) -> torch.Tensor:
    """One text as L token IDs, (1, L): normalised as ``clean_text`` does, then encoded."""
    cleaned = normalise_text(text)
    if not cleaned:
        raise WrongInput(
            "The text is empty once whitespace is normalised.",
            errors=[{"loc": ["body", "text"], "msg": "empty", "code": "wrong_input"}],
        )
    ids, _ = vocab.encode(cleaned, length)
    return torch.tensor([ids], dtype=torch.long)


def text_facts(text: str, vocab: Vocabulary, length: int) -> dict[str, Any]:
    """How a text was read (M11, REG-1.4): its tokens before cutting, those outside the
    vocabulary, and L when it was cut there."""
    from stratal.data.tokenize import UNK_ID

    ids, found = vocab.encode(normalise_text(text), length)
    unknown = sum(1 for i in ids[: min(found, length)] if i == UNK_ID)
    if found > length:  # the cut tokens count too
        cut, _ = vocab.encode(normalise_text(text), found)
        unknown = sum(1 for i in cut[:found] if i == UNK_ID)
    return {"tokens": found, "unknown": unknown, "truncated_at": length if found > length else None}


def network_result(
    output: torch.Tensor, task: str, classes: list[str], prep: TablePrep | None
) -> dict[str, Any]:
    """A network's output for one input: classes with probabilities, or a value."""
    if task == "regression":
        value = float(output.float().reshape(-1)[0])
        return {"task": task, "value": prep.unscale(value) if prep is not None else value}
    probabilities = torch.softmax(output.float(), dim=1)[0].cpu().tolist()
    return {"task": "classification", "top": top_classes(probabilities, classes)}
