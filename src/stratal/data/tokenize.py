"""The ``tokenize`` stage: words to integer IDs for labelled text (ADR-0019 rules 8–11, K3).

* **Words.** Casefolded text is split by ``\\w+|[^\\w\\s]``: runs of word
  characters, and single punctuation marks.
* **A vocabulary from the train split only** (as ML-1.3 asks of preprocessing):
  the tokens seen at least ``min_freq`` times in train rows, most frequent
  first, ties broken alphabetically, capped at ``vocab_size`` entries in all.
  ID 0 is padding (the built-in text templates use ``padding_idx: 0``), ID 1 is
  an unknown token, then the vocabulary in that order. The same data and
  settings always give the same IDs.
* **A fixed length L**: from the config, or else the train split's
  95th-percentile length rounded up to a multiple of 8, at most 512. Longer
  texts are cut at the end; shorter ones padded with 0 on the right.

Every accepted row gets ``tokens`` (L IDs as one string of space-separated
numbers: the version hash takes only scalar values, DATA-3.2) and
``token_count`` (its length before cutting). The vocabulary is a blob; its hash,
size and L are in the manifest's ``stratal.vocab`` metadata and the version's
counts, so the builder can lock its input (ADR-0017 rule 5) and ``/predict``
can tokenize the same way.
"""

import dataclasses
import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stratal.core.errors import StratalError
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.records import META_VOCAB, canonical_json, inherited_metadata
from stratal.data.versioning import VersionDeriver
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import STATUS_ACCEPTED, Row, iter_rows, read_metadata

STAGE = "tokenize"
PAD_ID = 0
UNK_ID = 1
PAD, UNK = "<pad>", "<unk>"
PATTERN = r"\w+|[^\w\s]"
MAX_LENGTH_CAP = 512
_TOKEN = re.compile(PATTERN)


class TokenizeConfig(BaseModel):
    """Settings for the tokenize stage. Every field is part of the version (DATA-3.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vocab_size: int = Field(
        default=20_000,
        ge=3,
        le=200_000,
        description="Entries in the vocabulary, padding and unknown included.",
    )
    min_freq: int = Field(
        default=2, ge=1, description="A token must appear this often in the train split to be kept."
    )
    max_length: int | None = Field(
        default=None,
        ge=1,
        le=MAX_LENGTH_CAP,
        description="L, the tokens per text. Empty: the train split's 95th-percentile length, "
        "rounded up to a multiple of 8, at most 512.",
    )
    algorithm_version: int = Field(default=1, ge=1, json_schema_extra={"readOnly": True})


def words(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


@dataclass(frozen=True)
class Vocabulary:
    tokens: tuple[str, ...]
    """Index is the ID: ``tokens[0]`` is padding, ``tokens[1]`` unknown."""

    def __post_init__(self) -> None:
        if self.tokens[:2] != (PAD, UNK):
            raise ValueError("a vocabulary starts with the padding and unknown tokens")

    @property
    def ids(self) -> dict[str, int]:
        return {token: index for index, token in enumerate(self.tokens)}

    def encode(
        self, text: str, length: int, ids: dict[str, int] | None = None
    ) -> tuple[list[int], int]:
        """``length`` IDs for ``text``, cut or padded, and its length before that."""
        lookup = self.ids if ids is None else ids
        found = [lookup.get(token, UNK_ID) for token in words(text)]
        return (found[:length] + [PAD_ID] * max(0, length - len(found)), len(found))

    def to_json(self) -> str:
        return canonical_json(
            {
                "format": "stratal.vocab/1",
                "pattern": PATTERN,
                "casefold": True,
                "pad_id": PAD_ID,
                "unk_id": UNK_ID,
                "tokens": list(self.tokens),
            }
        )

    @classmethod
    def from_json(cls, text: str) -> "Vocabulary":
        raw: dict[str, Any] = json.loads(text)
        if raw.get("format") != "stratal.vocab/1":
            raise ValueError(f"not a Stratal vocabulary: {raw.get('format')!r}")
        return cls(tuple(str(t) for t in raw["tokens"]))


def fit_vocabulary(texts: Iterable[str], *, vocab_size: int, min_freq: int) -> Vocabulary:
    """The train split's vocabulary: deterministic for the same texts and settings."""
    counts = Counter(token for text in texts for token in words(text))
    kept = sorted(
        (token for token, n in counts.items() if n >= min_freq),
        key=lambda token: (-counts[token], token),
    )
    return Vocabulary((PAD, UNK, *kept[: vocab_size - 2]))


def choose_max_length(lengths: Sequence[int]) -> int:
    """The 95th-percentile length (nearest rank), rounded up to a multiple of 8, in 8..512."""
    if not lengths:
        return 8
    ordered = sorted(lengths)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return min(MAX_LENGTH_CAP, max(8, math.ceil(p95 / 8) * 8))


def length_summary(lengths: Sequence[int], max_length: int) -> dict[str, Any]:
    """The length distribution shown when choosing L (ADR-0019 rule 10)."""
    if not lengths:
        return {"rows": 0}
    ordered = sorted(lengths)

    def rank(p: float) -> int:
        return ordered[max(0, math.ceil(p * len(ordered)) - 1)]

    edges = [8, 16, 32, 64, 128, 256, 512]
    histogram: list[dict[str, int | None]] = [
        {"up_to": edge, "rows": sum(1 for n in ordered if lo < n <= edge)}
        for lo, edge in zip([-1, *edges[:-1]], edges, strict=True)
    ]
    histogram.append({"up_to": None, "rows": sum(1 for n in ordered if n > edges[-1])})
    return {
        "rows": len(ordered),
        "min": ordered[0],
        "p50": rank(0.5),
        "p95": rank(0.95),
        "max": ordered[-1],
        "histogram": histogram,
        "truncated": sum(1 for n in ordered if n > max_length),
        "truncated_share": round(sum(1 for n in ordered if n > max_length) / len(ordered), 4),
    }


@dataclass(frozen=True)
class TokenizeResult:
    record: VersionRecord
    vocabulary: Vocabulary
    max_length: int
    report: dict[str, Any]
    metrics: dict[str, MetricValue]
    seconds: float


def _apply(rows: Iterator[Row], vocab: Vocabulary, length: int) -> Iterator[Row]:
    ids = vocab.ids
    for row in rows:
        if row["status"] == STATUS_ACCEPTED:
            encoded, count = vocab.encode(str(row["text"] or ""), length, ids)
            row.update(tokens=" ".join(map(str, encoded)), token_count=count)
        yield row


def decode_tokens(value: str) -> list[int]:
    """A row's ``tokens`` back to its IDs."""
    return [int(part) for part in value.split()]


def run_tokenize(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: TokenizeConfig,
    derive: VersionDeriver,
    sink: EventSink,
    checkpoint: Callable[[], None] = lambda: None,
) -> TokenizeResult:
    """Fit the vocabulary on the train rows of a ``split`` version and tokenize every row."""
    started = time.monotonic()
    made_by = read_metadata(parent_manifest).get("stratal.stage")
    if made_by != "split":
        raise StratalError(
            f"Tokenizing needs a split version, so the vocabulary comes from train only; "
            f"{parent_version_id} was made by {made_by!r}.",
            suggested_action="Run the labelled-text pipeline through its split stage first.",
        )
    train: list[str] = []
    for row in iter_rows(parent_manifest, columns=["status", "split", "text"]):
        if row["status"] == STATUS_ACCEPTED and row["split"] == "train":
            train.append(str(row["text"] or ""))
    if not train:
        raise StratalError(
            f"{parent_version_id} has no accepted train rows to fit a vocabulary on.",
            suggested_action="Check the split's proportions and the clean stage's rejections.",
        )
    sink.emit(
        stage=STAGE,
        substage="vocabulary",
        message=f"Fitting the vocabulary on {len(train):,} train texts",
        metrics={"train_rows": len(train)},
    )
    vocab = fit_vocabulary(train, vocab_size=config.vocab_size, min_freq=config.min_freq)
    lengths = [len(words(text)) for text in train]
    length = config.max_length or choose_max_length(lengths)
    summary = length_summary(lengths, length)
    report: dict[str, Any] = {
        "vocab_size": len(vocab.tokens),
        "max_length": length,
        "max_length_chosen": config.max_length is None,
        "min_freq": config.min_freq,
        "train_lengths": summary,
    }
    checkpoint()
    stored = BlobStore(data_dir).put_bytes(vocab.to_json().encode("utf-8"))
    report["vocab_sha256"] = stored.sha256
    sink.emit(
        stage=STAGE,
        substage="tokens",
        message=f"Tokenizing corpus, vocab {len(vocab.tokens):,} · L {length} · "
        f"{summary['truncated_share']:.1%} of train texts cut",
        metrics={"vocab_size": len(vocab.tokens), "max_length": length},
    )
    metadata = {
        **inherited_metadata(parent_manifest),
        META_VOCAB: canonical_json(
            {"sha256": stored.sha256, "size": len(vocab.tokens), "max_length": length}
        ),
    }
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=_apply(iter_rows(parent_manifest), vocab, length),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
        metadata=metadata,
    )
    record_with = dataclasses.replace(record, counts={**record.counts, "tokenize": report})
    metrics: dict[str, MetricValue] = {
        "vocab_size": len(vocab.tokens),
        "max_length": length,
        "train_rows": len(train),
        "truncated_share": float(summary["truncated_share"]),
        "accepted": int(record.counts.get("accepted", 0)),
    }
    return TokenizeResult(
        record_with, vocab, length, report, metrics, round(time.monotonic() - started, 3)
    )


def vocab_of(manifest: Path) -> dict[str, Any] | None:
    """A tokenized version's ``{sha256, size, max_length}``, or ``None``."""
    text = read_metadata(manifest).get(META_VOCAB)
    return dict(json.loads(text)) if text else None
