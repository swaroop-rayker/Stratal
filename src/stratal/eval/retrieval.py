"""hit@k on a hand-labelled question set (LLM-2.2, LLM-2.3; ADR-0022 rules 9 to 11).

* **The question set** is a versioned JSONL file in this package
  (``stratal/eval/rag/<name>.jsonl``), one question per line: ``id``, ``question``,
  ``answerable``, and ``labels``, a list of ``{document, section}``. Once the owner has
  checked it, it is frozen: a change is a new file, and every report names the file and
  its SHA-256.
* **A hit:** a retrieved chunk from a labelled document whose section path, without the
  document's title, equals the labelled section or sits under it. An empty labelled
  section means the whole document. Labels name sections, not chunks, so index versions
  with different chunk sizes are scored on the same set.
* **hit@k** is the share of answerable questions with at least one hit in their top k.
* **Relevance:** the top-1 relevance of answerable and unanswerable questions, side by
  side, and for thresholds 0.30 to 0.80 how many of each would be answered and abstain.

Scoring was the owner's to take (decision 12); the owner asked the agent to write it.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stratal.data.corpus import SECTION_SEPARATOR

Json = dict[str, Any]
DEFAULT_SET = "stratal-docs-questions-v1"
KS = (1, 5, 10)
THRESHOLDS = tuple(round(0.30 + 0.05 * i, 2) for i in range(11))
BIN_EDGES = tuple(round(0.20 + 0.05 * i, 2) for i in range(15))
"""Histogram edges for the top-1 relevance: 0.20 to 0.90 in 0.05 steps."""
MISSED_K = 5


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document: str = Field(min_length=1)
    section: str = Field(default="", description="The section path without the title; empty: all.")


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answerable: bool
    labels: tuple[Label, ...] = ()


class RetrievalEvalConfig(BaseModel):
    """Settings for ``retrieval_eval`` (ADR-0022 rule 11)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(default="retrieval", json_schema_extra={"readOnly": True})
    question_set: str = Field(default=DEFAULT_SET, pattern=r"^[a-z0-9][a-z0-9_-]{1,80}$")
    ks: tuple[int, ...] = KS


@dataclass(frozen=True)
class QuestionSet:
    name: str
    sha256: str
    questions: tuple[Question, ...]

    @property
    def answerable(self) -> list[Question]:
        return [q for q in self.questions if q.answerable]


def load_question_set(name: str) -> QuestionSet:
    raw = resources.files("stratal.eval").joinpath("rag", f"{name}.jsonl").read_bytes()
    questions = tuple(
        Question.model_validate(json.loads(line))
        for line in raw.decode("utf-8").splitlines()
        if line.strip()
    )
    ids = [q.id for q in questions]
    if len(set(ids)) != len(ids):
        raise ValueError(f"question set {name} repeats an id")
    for q in questions:
        if q.answerable and not q.labels:
            raise ValueError(f"question {q.id} is answerable but has no label")
    return QuestionSet(name, hashlib.sha256(raw).hexdigest(), questions)


def _below_title(section: str) -> str:
    """A chunk's section path without its first part, the document's title."""
    parts = section.split(SECTION_SEPARATOR)
    return SECTION_SEPARATOR.join(parts[1:])


def is_hit(document: str, section: str, label: Label) -> bool:
    if document != label.document:
        return False
    if not label.section:
        return True
    path = _below_title(section)
    return path == label.section or path.startswith(label.section + SECTION_SEPARATOR)


@dataclass(frozen=True)
class Retrieved:
    """One retrieved chunk, as the scorer needs it."""

    row_key: str
    document: str
    section: str
    relevance: float


def first_hit(question: Question, retrieved: Sequence[Retrieved]) -> int | None:
    """The 1-based rank of the first hit, or ``None``."""
    for rank, chunk in enumerate(retrieved, start=1):
        if any(is_hit(chunk.document, chunk.section, label) for label in question.labels):
            return rank
    return None


def _histogram(values: Sequence[float]) -> list[int]:
    counts = [0] * (len(BIN_EDGES) - 1)
    for value in values:
        clipped = min(max(value, BIN_EDGES[0]), BIN_EDGES[-1] - 1e-9)
        index = int((clipped - BIN_EDGES[0]) / 0.05 + 1e-9)
        counts[min(index, len(counts) - 1)] += 1
    return counts


def score(
    questions: QuestionSet, retrieved: Mapping[str, Sequence[Retrieved]], ks: Sequence[int] = KS
) -> Json:
    """The report body: hit@k, the missed questions, and relevance against threshold."""
    answerable = questions.answerable
    unanswerable = [q for q in questions.questions if not q.answerable]
    ranks = {q.id: first_hit(q, retrieved[q.id]) for q in answerable}
    hits: dict[str, Json] = {}
    for k in ks:
        found = sum(1 for r in ranks.values() if r is not None and r <= k)
        total = len(answerable)
        hits[f"hit@{k}"] = {
            "hits": found,
            "questions": total,
            "share": round(found / total, 4) if total else None,
        }

    def top1(q: Question) -> float:
        found = retrieved[q.id]
        return round(found[0].relevance, 4) if found else 0.0

    answerable_top = [top1(q) for q in answerable]
    unanswerable_top = [top1(q) for q in unanswerable]
    missed = [
        {
            "id": q.id,
            "question": q.question,
            "labels": [label.model_dump() for label in q.labels],
            "retrieved": [
                {
                    "row_key": c.row_key,
                    "document": c.document,
                    "section": c.section,
                    "relevance": round(c.relevance, 4),
                }
                for c in retrieved[q.id][:3]
            ],
        }
        for q in answerable
        if ranks[q.id] is None or ranks[q.id] > MISSED_K  # type: ignore[operator]
    ]
    return {
        "question_set": {
            "name": questions.name,
            "sha256": questions.sha256,
            "answerable": len(answerable),
            "unanswerable": len(unanswerable),
        },
        "ks": list(ks),
        "hits": hits,
        "missed": missed,
        "relevance": {
            "answerable": answerable_top,
            "unanswerable": unanswerable_top,
            "bin_edges": list(BIN_EDGES),
            "answerable_bins": _histogram(answerable_top),
            "unanswerable_bins": _histogram(unanswerable_top),
        },
        "thresholds": [
            {
                "threshold": t,
                "answered": sum(1 for v in answerable_top if v >= t),
                "abstained": sum(1 for v in unanswerable_top if v < t),
            }
            for t in THRESHOLDS
        ],
        "per_question": [
            {
                "id": q.id,
                "answerable": q.answerable,
                "first_hit": ranks.get(q.id),
                "top": [
                    {"row_key": c.row_key, "section": c.section, "relevance": round(c.relevance, 4)}
                    for c in retrieved[q.id][: max(ks)]
                ],
            }
            for q in questions.questions
        ],
    }
