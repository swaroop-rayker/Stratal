"""What a version holds, and how two versions differ (UI-2.2, DATA-3.5; SCR-03 v2, K10).

Every version of a dataset holds every row (INV-06), each keeping the stage that decided
it, so one manifest is enough for the funnel from raw to the version: rows decided at
each stage of its lineage, by reason. "Raw" is every row, "here" the accepted rows and
"removed" the rest. The diff matches two versions' rows by ``row_key``.

Pure functions over manifests and lineage facts; the API caches their results by version
ID, since a version never changes.
"""

import math
from array import array
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from stratal.storage.manifest import COLUMNS, STATUS_ACCEPTED, Row, read_all

__all__ = ["DISTRIBUTION_FIELDS", "Step", "diff", "distributions", "filled_in", "read_all"]

Json = dict[str, Any]


@dataclass(frozen=True)
class Step:
    """One version of the lineage, root first."""

    version_id: str
    stage: str
    config: Mapping[str, Any]


# ---------------------------------------------------------------- numbers


def summary(values: Sequence[float]) -> Json | None:
    """n, min, p10, median, p90, max and mean (nearest-rank percentiles)."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)

    def rank(p: float) -> float:
        return ordered[min(n - 1, max(0, math.ceil(p * n) - 1))]

    return {
        "n": n,
        "min": ordered[0],
        "p10": rank(0.10),
        "median": rank(0.50),
        "p90": rank(0.90),
        "max": ordered[-1],
        "mean": sum(ordered) / n,
    }


def nice_edges(low: float, high: float, bins: int = 8) -> list[float]:
    """Round bin edges covering ``[low, high]``: a step of 1, 2 or 5 times a power of ten."""
    if high <= low:
        return [low, low + 1.0]
    raw = (high - low) / bins
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 5, 10) if m * power >= raw)
    start = math.floor(low / step) * step
    edges = [start]
    while edges[-1] < high:
        edges.append(round(edges[-1] + step, 10))
    if len(edges) == 1:
        edges.append(start + step)
    return edges


def histogram(values: Sequence[float], edges: Sequence[float]) -> list[int]:
    """Counts per bin ``[edges[i], edges[i+1])``; the last bin also holds ``edges[-1]``."""
    counts = [0] * (len(edges) - 1)
    for v in values:
        i = bisect_right(edges, v) - 1
        if 0 <= i < len(counts):
            counts[i] += 1
        elif counts and v == edges[-1]:
            counts[-1] += 1
    return counts


# ---------------------------------------------------------------- distributions (UI-2.2)


@dataclass(frozen=True)
class _Column:
    key: str
    label: str
    unit: str
    value: Callable[[Row], float | None]
    raw_too: bool = True
    """Whether the column exists before the stage that adds it (width does; captions do not)."""


def _num(name: str, scale: float = 1.0) -> Callable[[Row], float | None]:
    def get(row: Row) -> float | None:
        value = row.get(name)
        return None if value is None else float(value) / scale

    return get


def _long_side(row: Row) -> float | None:
    w, h = row.get("derived_width"), row.get("derived_height")
    return None if w is None or h is None else float(max(int(w), int(h)))


def _words(name: str) -> Callable[[Row], float | None]:
    def get(row: Row) -> float | None:
        text = row.get(name)
        return None if not text else float(len(str(text).split()))

    return get


_IMAGE_COLUMNS = (
    _Column("width", "Width", "px", _num("width")),
    _Column("height", "Height", "px", _num("height")),
    _Column("size_kb", "File size", "KB", _num("size_bytes", 1024.0)),
    _Column("long_side", "Preprocessed long side", "px", _long_side, raw_too=False),
    _Column("caption_words", "Caption length", "words", _words("caption"), raw_too=False),
    _Column("clip_score", "CLIP score", "", _num("clip_score"), raw_too=False),
)
_TEXT_COLUMNS = (
    _Column("text_words", "Text length", "words", _words("text")),
    _Column("token_count", "Tokens before the cut", "tokens", _num("token_count"), raw_too=False),
)
_TABLE_COLUMNS = (_Column("target_value", "Target", "", _num("target_value")),)


def _columns_for(dtype: str | None) -> tuple[_Column, ...]:
    if dtype in ("labelled_text",):
        return _TEXT_COLUMNS
    if dtype in ("tabular",):
        return _TABLE_COLUMNS
    return _IMAGE_COLUMNS


DISTRIBUTION_FIELDS = (
    "status",
    "decided_at_stage",
    "reason_code",
    "split",
    "label",
    "format",
    "tokens",
    "token_count",
    "width",
    "height",
    "size_bytes",
    "derived_width",
    "derived_height",
    "caption",
    "clip_score",
    "text",
    "target_value",
)
"""The manifest columns :func:`distributions` reads; the rest need not be loaded (NFR-2, E-024)."""


def _column(
    col: _Column, raw: Sequence[float], here: Sequence[float], removed: Sequence[float]
) -> Json | None:
    if not here and not raw:
        return None
    if min(raw) == max(raw):
        bins = None  # one value: a line, not a chart (SCR-03 v2 frame E: all 32 × 32)
    else:
        edges = nice_edges(min(raw), max(raw))
        bins = [
            {"low": edges[i], "high": edges[i + 1], "raw": a, "here": b}
            for i, (a, b) in enumerate(
                zip(histogram(raw, edges), histogram(here, edges), strict=True)
            )
        ]
    return {
        "key": col.key,
        "label": col.label,
        "unit": col.unit,
        "raw_label": "raw" if col.raw_too else "with a value",
        "raw": summary(raw),
        "here": summary(here),
        "removed": summary(removed),
        "bins": bins,
    }


def _funnel(
    total: int,
    decided: Mapping[str, Counter[tuple[str, str]]],
    counted: Counter[str],
    lineage: Sequence[Step],
) -> list[Json]:
    steps: list[Json] = []
    rows_in = total
    for index, step in enumerate(lineage):
        reasons = sorted(decided.get(step.stage, Counter()).items(), key=lambda kv: (-kv[1], kv[0]))
        removed = [
            {"reason_code": code, "status": status, "rows": n} for (code, status), n in reasons
        ]
        out = rows_in - sum(n for _key, n in reasons)
        parts: dict[str, int] | None = None
        if step.stage == "split" and index == len(lineage) - 1:
            parts = {name: counted.get(name, 0) for name in ("train", "val", "test")}
        steps.append(
            {
                "stage": step.stage,
                "version_id": step.version_id,
                "rows_in": rows_in,
                "removed": removed,
                "rows_out": out,
                "parts": parts,
            }
        )
        rows_in = out
    return steps


def distributions(rows: Iterable[Row], *, dtype: str | None, lineage: Sequence[Step]) -> Json:
    """The funnel from raw to the version, then each column raw against here (UI-2.2).

    One pass over ``rows``, which may be a stream (:func:`stratal.storage.manifest.iter_rows`
    over :data:`DISTRIBUTION_FIELDS`): only counters and each column's numbers, as 8-byte
    floats, are kept, so a table of millions of rows fits the api's memory (NFR-2, E-024).
    """
    cols = _columns_for(dtype)
    raw = {c.key: array("d") for c in cols}
    here = {c.key: array("d") for c in cols}
    removed = {c.key: array("d") for c in cols}
    decided: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    splits: Counter[str] = Counter()
    by: dict[str, Counter[str]] = defaultdict(Counter)
    formats: Counter[str] = Counter()
    total = accepted = 0
    token_length: int | None = None
    token_rows = token_cut_rows = 0
    for r in rows:
        total += 1
        ok = r.get("status") == STATUS_ACCEPTED
        for col in cols:
            v = col.value(r)
            if v is not None:
                raw[col.key].append(v)
                (here if ok else removed)[col.key].append(v)
        if r.get("format"):
            formats[str(r["format"])] += 1
        if not ok:
            stage = str(r.get("decided_at_stage") or "")
            decided[stage][(str(r.get("reason_code") or ""), str(r.get("status")))] += 1
            continue
        accepted += 1
        if r.get("split"):
            splits[str(r["split"])] += 1
        if r.get("label"):
            by[str(r["label"])][str(r.get("split") or "all")] += 1
        if r.get("tokens"):
            if token_length is None:
                token_length = len(str(r["tokens"]).split())
            token_rows += 1
            if int(r.get("token_count") or 0) > token_length:
                token_cut_rows += 1
    columns = [
        c
        for col in cols
        if (c := _column(col, raw[col.key], here[col.key], removed[col.key])) is not None
    ]
    classes: list[Json] | None = None
    if by:
        classes = [
            {
                "label": label,
                "train": c.get("train", 0),
                "val": c.get("val", 0),
                "test": c.get("test", 0),
                "rows": sum(c.values()),
            }
            for label, c in sorted(by.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
        ]
    markers: Json = {}
    for step in lineage:
        if step.stage == "filter" and "clip_threshold" in step.config:
            markers["clip_score"] = step.config["clip_threshold"]
    token_cut: Json | None = None
    if token_length is not None:
        token_cut = {"length": token_length, "cut": token_cut_rows, "rows": token_rows}
    return {
        "row_count": total,
        "accepted": accepted,
        "removed": total - accepted,
        "funnel": _funnel(total, decided, splits, lineage),
        "columns": columns,
        "classes": classes,
        "formats": dict(sorted(formats.items())),
        "markers": markers,
        "token_cut": token_cut,
    }


# ---------------------------------------------------------------- diff (DATA-3.5)

_WORDING = "reason_detail"
_IGNORED = {"row_key", _WORDING}
_SHOWN_LIMIT = 200


def _config_diff(a: Sequence[Step], b: Sequence[Step]) -> list[Json]:
    """Settings that differ, stage by stage along both lineages."""
    by_a = {s.stage: s for s in a}
    by_b = {s.stage: s for s in b}
    out: list[Json] = []
    for stage in [s.stage for s in a] + [s.stage for s in b if s.stage not in by_a]:
        sa, sb = by_a.get(stage), by_b.get(stage)
        if sa is None or sb is None:
            out.append(
                {
                    "stage": stage,
                    "field": None,
                    "a": "present" if sa else "absent",
                    "b": "present" if sb else "absent",
                }
            )
            continue
        for key in sorted(set(sa.config) | set(sb.config)):
            va, vb = sa.config.get(key), sb.config.get(key)
            if va != vb and not key.startswith("continues_"):
                out.append({"stage": stage, "field": key, "a": va, "b": vb})
    return out


def _field_group(field: str) -> str:
    """Fields shown together in "changed, by field"."""
    if field in ("derived_sha256", "derived_width", "derived_height"):
        return "derived size and file"
    if field in ("sha256", "size_bytes", "format", "width", "height", "phash"):
        return "content"
    return field


def filled_in(rows: Iterable[Row]) -> set[str]:
    """The columns at least one row has a value in."""
    return {f for r in rows for f, v in r.items() if v is not None}


def _pairs(a_rows: Iterable[Row], b_rows: Iterable[Row]) -> Iterator[tuple[Row | None, Row | None]]:
    """Both sides' rows matched by ``row_key``, in key order; ``None`` where one side has
    no such row. Both inputs must be sorted by ``row_key``, as manifests are."""
    a_it, b_it = iter(a_rows), iter(b_rows)
    ra, rb = next(a_it, None), next(b_it, None)
    while ra is not None or rb is not None:
        ka = None if ra is None else str(ra["row_key"])
        kb = None if rb is None else str(rb["row_key"])
        if kb is None or (ka is not None and ka < kb):
            yield ra, None
            ra = next(a_it, None)
        elif ka is None or kb < ka:
            yield None, rb
            rb = next(b_it, None)
        else:
            yield ra, rb
            ra, rb = next(a_it, None), next(b_it, None)


def diff(
    a_rows: Iterable[Row],
    b_rows: Iterable[Row],
    *,
    a_steps: Sequence[Step],
    b_steps: Sequence[Step],
    a_filled: Collection[str],
    b_filled: Collection[str],
) -> Json:
    """Rows added, removed and changed from A to B, matched by ``row_key`` (DATA-3.5).

    A row is *changed* when its status, reason code, content or a derived field differs
    (the owner's answer at the low-fi, 2026-11-21); a row whose only difference is
    ``reason_detail`` is counted on its own line as wording only. A field one side has
    for no row (a label version's caption against a clean version) is listed once, not
    per row: ``a_filled`` and ``b_filled`` name each side's columns that have any value.

    Both sides are streams sorted by ``row_key`` and are walked side by side, so neither
    is held in memory (NFR-2, E-025): a manifest of millions of rows can be compared.
    """
    only_a = [f for f in COLUMNS if f not in _IGNORED and f in a_filled and f not in b_filled]
    only_b = [f for f in COLUMNS if f not in _IGNORED and f in b_filled and f not in a_filled]
    compared = [f for f in COLUMNS if f not in _IGNORED and f not in only_a and f not in only_b]
    changed: list[Json] = []
    by_field: Counter[str] = Counter()
    wording: list[Json] = []
    same = added = removed = changed_count = 0
    for ra, rb in _pairs(a_rows, b_rows):
        if rb is None:
            removed += 1
            continue
        if ra is None:
            added += 1
            continue
        key = str(ra["row_key"])
        fields = [f for f in compared if ra.get(f) != rb.get(f)]
        if fields:
            changed_count += 1
            for group in {_field_group(f) for f in fields}:
                by_field[group] += 1
            if len(changed) < _SHOWN_LIMIT:
                changed.append(
                    {
                        "row_key": key,
                        "fields": [{"field": f, "a": ra.get(f), "b": rb.get(f)} for f in fields],
                    }
                )
        elif ra.get(_WORDING) != rb.get(_WORDING):
            if len(wording) < _SHOWN_LIMIT:
                wording.append({"row_key": key, "a": ra.get(_WORDING), "b": rb.get(_WORDING)})
            by_field["__wording"] += 1
        else:
            same += 1
    wording_count = by_field.pop("__wording", 0)
    return {
        "stages_differ": a_steps[-1].stage != b_steps[-1].stage if a_steps and b_steps else False,
        "config_diff": _config_diff(a_steps, b_steps),
        "added": added,
        "removed": removed,
        "changed": changed_count,
        "wording_only": wording_count,
        "same": same,
        "by_field": dict(sorted(by_field.items(), key=lambda kv: (-kv[1], kv[0]))),
        "only_in_a": only_a,
        "only_in_b": only_b,
        "changed_rows": changed,
        "wording_rows": wording,
        "shown_limit": _SHOWN_LIMIT,
    }
