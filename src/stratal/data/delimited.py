"""Detect a table saved as delimited text (DATA-1.4.AC2).

UCI and other archives often publish tables as ``.txt``, ``.dat`` or ``.data``
files: one record per line, fields separated by ``;``, a tab, ``,`` or ``|``,
often with ``?`` for a missing value. :func:`sniff` decides, from the first
64 KiB of a file, whether it is such a table and how it is written; the table
reader then converts it to Parquet once (``stratal.data.tables``).

The rules, so the same file always gives the same answer:

* **A table** when, for one separator, every sampled line (at least two, blank
  lines skipped) splits into the same number of fields, two or more. When several
  separators qualify, the one giving the most fields wins; a tie goes to the
  earlier of ``; \\t , |``.
* **A header row** when the first line's field is not a number in a column whose
  sampled values all are; or when every field of the first line is text, none
  empty, none repeated, and none of them reappears in its column below. Otherwise
  the columns are named ``column_1``, ``column_2`` …
* Quotes follow CSV's rules (``"a;b"`` is one field).

Space-aligned tables are not detected: a run of spaces is not a separator
PyArrow's reader supports, and prose would match it.
"""

import csv
import io
from dataclasses import dataclass
from pathlib import Path

DELIMITED_SUFFIXES = frozenset({".txt", ".tsv", ".dat", ".data"})
"""File types that may hold a delimited table; ``.txt`` may also be plain text."""

SEPARATORS = (";", "\t", ",", "|")
"""Candidate separators, in tie-break order."""

NULL_MARKERS = ("?",)
"""Missing-value markers of delimited text, on top of PyArrow's CSV null words."""

SAMPLE_BYTES = 64 << 10
SAMPLE_LINES = 50

_NULL_WORDS = frozenset(
    {"", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND", "1.#QNAN"}
    | {"N/A", "NA", "NULL", "NaN", "n/a", "nan", "null", *NULL_MARKERS}
)


@dataclass(frozen=True)
class Dialect:
    """How a delimited table is written."""

    delimiter: str
    header: bool
    names: tuple[str, ...]

    def describe(self) -> str:
        """For events: ``';'-separated, header row, 9 columns``."""
        sep = "tab" if self.delimiter == "\t" else repr(self.delimiter)
        head = "header row" if self.header else "no header row"
        return f"{sep}-separated, {head}, {len(self.names)} columns"


def _number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _sample(path: Path) -> list[str] | None:
    with path.open("rb") as handle:
        raw = handle.read(SAMPLE_BYTES + 1)
    truncated = len(raw) > SAMPLE_BYTES
    try:
        text = raw[:SAMPLE_BYTES].decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        if not truncated or exc.start < SAMPLE_BYTES - 4:
            return None  # not UTF-8 text; a cut multi-byte character at the end is fine
        text = raw[: exc.start].decode("utf-8-sig")
    lines = text.splitlines()
    if truncated and lines:
        lines.pop()  # the last line may be cut
    return [line for line in lines if line.strip()][:SAMPLE_LINES]


def _header(rows: list[list[str]]) -> bool:
    first, data = rows[0], rows[1:]
    for i, value in enumerate(first):
        head = value.strip()
        present = [v for v in (r[i].strip() for r in data) if v not in _NULL_WORDS]
        numbers_below = bool(present) and all(_number(v) for v in present)
        if numbers_below and not _number(head) and head not in _NULL_WORDS:
            return True
    names = [v.strip() for v in first]
    if any(not n or _number(n) for n in names) or len(set(names)) != len(names):
        return False
    return all(names[i] not in {r[i].strip() for r in data} for i in range(len(names)))


def sniff(path: Path) -> Dialect | None:
    """The file's dialect if it is a delimited table, else ``None`` (it is text)."""
    lines = _sample(path)
    if lines is None or len(lines) < 2:
        return None
    best: tuple[int, str, list[list[str]]] | None = None
    for sep in SEPARATORS:
        rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=sep))
        widths = {len(r) for r in rows}
        if len(widths) != 1:
            continue
        width = widths.pop()
        if width >= 2 and (best is None or width > best[0]):
            best = (width, sep, rows)
    if best is None:
        return None
    width, sep, rows = best
    header = _header(rows)
    names = tuple(rows[0]) if header else tuple(f"column_{i}" for i in range(1, width + 1))
    return Dialect(sep, header, names)
