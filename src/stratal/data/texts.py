"""Text ingestion: JSONL or TXT files into manifest rows (DATA-1.4, ADR-0019 rule 3).

* ``.jsonl``: one JSON object per line, with the text, its class and, if
  given, a group (rows of one group stay in one split, DATA-4.2). Blank lines
  are not records.
* ``.txt`` in a class folder: one text per line; the class is the first folder,
  as for labelled images.

Each record becomes one row keyed ``<file>#<line number>``. A line that cannot
be read, or has no class, is rejected with its reason; any other file is one
rejected ``unsupported_format`` row. Empty and over-long texts are accepted
here and dropped by the clean stage, which normalises the text first (DATA-2.4).
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stratal.data.records import content_hash
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_REJECTED, Row

TEXT_FORMATS = {".jsonl": "JSONL", ".txt": "TXT"}
_DIGITS = 7


class TextIngest(BaseModel):
    """How texts are read (DATA-1.4). Part of the ingest config, so of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text_field: str = Field(default="text", min_length=1, description="JSONL: the text's key.")
    label_field: str = Field(default="label", min_length=1, description="JSONL: the class's key.")
    group_field: str | None = Field(
        default="group",
        description="JSONL: the key whose value groups rows that must share a split, e.g. the "
        "image a caption describes. Absent or null in a row: the row is its own group.",
    )


def _reject(row: Row, code: str, detail: str, stage: str) -> Row:
    row.update(
        status=STATUS_REJECTED, decided_at_stage=stage, reason_code=code, reason_detail=detail[:500]
    )
    return row


def _text_row(key: str, text: str, fmt: str) -> Row:
    return {
        "row_key": key,
        "sha256": content_hash(text),
        "size_bytes": len(text.encode("utf-8")),
        "format": fmt,
        "text": text,
        "status": STATUS_ACCEPTED,
    }


def _jsonl_rows(source: Path, relpath: str, config: TextIngest, stage: str) -> Iterator[Row]:
    with (source / relpath).open(encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            key = f"{relpath}#{number:0{_DIGITS}d}"
            try:
                obj: Any = json.loads(line)
            except json.JSONDecodeError as exc:
                yield _reject(
                    {"row_key": key, "format": "JSONL"},
                    "unreadable",
                    f"line {number}: {exc}",
                    stage,
                )
                continue
            fields: dict[str, Any] = obj if isinstance(obj, dict) else {}  # pyright: ignore[reportUnknownVariableType]
            text = fields.get(config.text_field)
            if not isinstance(text, str):
                yield _reject(
                    {"row_key": key, "format": "JSONL"},
                    "unreadable",
                    f"line {number}: no string '{config.text_field}'",
                    stage,
                )
                continue
            row = _text_row(key, text, "JSONL")
            label = fields.get(config.label_field)
            if config.group_field is not None and fields.get(config.group_field) is not None:
                row["text_group"] = str(fields[config.group_field])
            if isinstance(label, int | float) and not isinstance(label, bool):
                label = str(label)
            if not isinstance(label, str) or not label.strip():
                yield _reject(
                    row, "missing_label", f"line {number}: no '{config.label_field}'", stage
                )
                continue
            row["label"] = label
            yield row


def _txt_rows(source: Path, relpath: str, stage: str) -> Iterator[Row]:
    head, sep, _ = relpath.partition("/")
    label = head if sep and head else None
    with (source / relpath).open(encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            row = _text_row(f"{relpath}#{number:0{_DIGITS}d}", line.rstrip("\r\n"), "TXT")
            if label is None:
                yield _reject(
                    row,
                    "no_label",
                    "file is at the source's root, so it has no class folder",
                    stage,
                )
                continue
            row["label"] = label
            yield row


def text_rows(source: Path, relpaths: list[str], config: TextIngest, *, stage: str) -> list[Row]:
    """Every record of every file, sorted by key (line keys need not sort like their paths)."""
    rows: list[Row] = []
    for relpath in relpaths:
        fmt = TEXT_FORMATS.get(Path(relpath).suffix.lower())
        try:
            if fmt == "JSONL":
                rows.extend(_jsonl_rows(source, relpath, config, stage))
            elif fmt == "TXT":
                rows.extend(_txt_rows(source, relpath, stage))
            else:
                rows.append(
                    _reject(
                        {"row_key": relpath},
                        "unsupported_format",
                        "not a .jsonl or .txt file",
                        stage,
                    )
                )
        except OSError as exc:
            # The detail is part of the version: no absolute path in it (E-010).
            detail = f"{type(exc).__name__}: {exc}".replace(str(source / relpath), relpath)
            rows.append(_reject({"row_key": relpath}, "unreadable", detail, stage))
    rows.sort(key=lambda row: str(row["row_key"]))
    return rows
