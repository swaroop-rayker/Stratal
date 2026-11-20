"""What tables and texts share on their way through the pipeline (DATA-1.4, DATA-2.4, ADR-0019).

A tabular or labelled-text dataset has one manifest row per record, not per
file. The row keeps its values in fixed nullable columns (DATA_MODEL §5):

* a table: ``record`` (the feature values as canonical JSON), and the target in
  ``label`` (a category) or ``target_value`` (a number);
* a text: ``text``, its class in ``label``, and ``text_group`` for rows that
  must stay in one split group (the captions of one image).

``sha256`` is the hash of the row's content (the canonical record, or the
text), so exact duplicates are found the way they are for files. The table's
schema travels with every version as manifest metadata (``stratal.table_schema``),
and so does a tokenized version's vocabulary (``stratal.vocab``).
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from stratal.data.dataset_types import Column, TableSchema
from stratal.storage.manifest import read_metadata

TABULAR = "tabular"
LABELLED_TEXT = "labelled_text"
RECORD_TYPES = (TABULAR, LABELLED_TEXT)
"""Dataset types whose rows are records rather than files."""

META_TABLE_SCHEMA = "stratal.table_schema"
META_VOCAB = "stratal.vocab"
INHERITED_METADATA = (META_TABLE_SCHEMA, META_VOCAB)
"""Metadata a stage that keeps the rows passes on to the version it makes."""

Task = Literal["classification", "regression"]


def canonical_json(value: Any) -> str:
    """One fixed text form: keys sorted, no spaces, no NaN or infinity."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def table_task(schema: TableSchema) -> Task:
    """A categorical target is classification; a numeric one regression (ADR-0019 rule 2)."""
    target = next(c for c in schema.columns if c.role == "target")
    return "classification" if target.kind == "categorical" else "regression"


def schema_to_json(schema: TableSchema) -> str:
    return canonical_json(
        {
            "columns": [{"name": c.name, "kind": c.kind, "role": c.role} for c in schema.columns],
            "task": table_task(schema),
        }
    )


def schema_from_json(text: str) -> TableSchema:
    raw: dict[str, Any] = json.loads(text)
    return TableSchema(tuple(Column(str(c["name"]), c["kind"], c["role"]) for c in raw["columns"]))


def table_schema_of(manifest: Path) -> TableSchema | None:
    """The table schema a version carries, or ``None`` for any other kind of data."""
    text = read_metadata(manifest).get(META_TABLE_SCHEMA)
    return schema_from_json(text) if text else None


def inherited_metadata(manifest: Path) -> dict[str, str]:
    """The data-describing metadata of ``manifest``, for the version made from it."""
    found = read_metadata(manifest)
    return {key: found[key] for key in INHERITED_METADATA if key in found}


_SPACE = re.compile(r"\s+")


def normalise_text(text: str, *, form: Literal["NFKC"] = "NFKC") -> str:
    """DATA-2.4: Unicode normalised, whitespace runs collapsed to one space, ends trimmed."""
    return _SPACE.sub(" ", unicodedata.normalize(form, text)).strip()


def row_values(row: Mapping[str, Any], schema: TableSchema) -> dict[str, Any]:
    """A table row's values by column name, the target included, from its manifest row."""
    values: dict[str, Any] = json.loads(row["record"]) if row.get("record") else {}
    target = next(c for c in schema.columns if c.role == "target")
    values[target.name] = (
        row.get("label") if target.kind == "categorical" else row.get("target_value")
    )
    return values
