"""Text corpora and instruction pairs (DATA-5.3, ADR-0022 rules 1 to 3).

* **Ingest a corpus:** every ``.md`` and ``.txt`` file at any depth is one row: its
  relative path as the key, its text, its title (the first ``#`` heading, else the
  file's name). A file that is not UTF-8 is rejected ``not_utf8``; any other file
  ``unsupported_format``. Empty documents are dropped later by ``clean_text``.
* **Ingest instruction pairs:** each line of a ``.jsonl`` file with ``instruction``,
  an optional ``input`` and ``output``. The fields are kept as the row's ``record``;
  its ``text`` joins them, so ``clean_text`` finds empty and duplicate pairs.
* **Chunk:** a document is cut at its ``#``, ``##`` and ``###`` headings (not inside
  code fences), each piece keeping its section path; a section longer than
  ``chunk_tokens`` is split at paragraphs, then sentences, then words, neighbours
  sharing up to ``overlap_tokens``; a section shorter than ``min_tokens`` joins the
  next one of its document. Each chunk keeps its offsets in the cleaned document.

Token counts come from a function given in (the embedding model's tokenizer in the
worker), so this module needs no ML library.
"""

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stratal.data.records import canonical_json, content_hash
from stratal.pretrained.catalog import get_model
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_REJECTED, Row

CORPUS_FORMATS = {".md": "MD", ".markdown": "MD", ".txt": "TXT"}
SECTION_SEPARATOR = " › "
TokenCounter = Callable[[str], int]

_HEADING = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^(```|~~~)")
_PARAGRAPH = re.compile(r"\n[ \t]*\n")
_SENTENCE = re.compile(r"(?<=[.!?:;])\s+|\n")
_WORD = re.compile(r"\S+")


# ---------------------------------------------------------------------------- ingest


class CorpusIngest(BaseModel):
    """How a text corpus is read (DATA-5.3). Part of the ingest config, so of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    extensions: tuple[str, ...] = Field(
        default=(".md", ".markdown", ".txt"),
        description="The files that are documents; any other file is rejected.",
    )


class PairsIngest(BaseModel):
    """How instruction pairs are read (DATA-5.3). Part of the ingest config."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction_field: str = Field(default="instruction", min_length=1)
    input_field: str = Field(default="input", min_length=1)
    output_field: str = Field(default="output", min_length=1)
    max_chars: int = Field(
        default=20_000, ge=1, description="A pair longer than this in all is text_too_long."
    )


def _reject(row: Row, code: str, detail: str, stage: str) -> Row:
    row.update(
        status=STATUS_REJECTED, decided_at_stage=stage, reason_code=code, reason_detail=detail[:500]
    )
    return row


def document_title(text: str, path: str) -> str:
    """The first top-level heading, else the file's name without its extension."""
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2).strip()
    return Path(path).stem


def corpus_rows(
    source: Path, relpaths: list[str], config: CorpusIngest, *, stage: str
) -> list[Row]:
    """One row per document, sorted by path."""
    rows: list[Row] = []
    for relpath in sorted(relpaths):
        suffix = Path(relpath).suffix.lower()
        if suffix not in config.extensions or suffix not in CORPUS_FORMATS:
            rows.append(
                _reject(
                    {"row_key": relpath},
                    "unsupported_format",
                    f"not a document ({', '.join(config.extensions)})",
                    stage,
                )
            )
            continue
        try:
            raw = (source / relpath).read_bytes()
        except OSError as exc:
            rows.append(_reject({"row_key": relpath}, "unreadable", type(exc).__name__, stage))
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            rows.append(
                _reject(
                    {"row_key": relpath, "size_bytes": len(raw)},
                    "not_utf8",
                    f"byte {exc.start} is not UTF-8",
                    stage,
                )
            )
            continue
        rows.append(
            {
                "row_key": relpath,
                "sha256": content_hash(text),
                "size_bytes": len(raw),
                "format": CORPUS_FORMATS[suffix],
                "text": text,
                "title": document_title(text, relpath),
                "document": relpath,
                "status": STATUS_ACCEPTED,
            }
        )
    return rows


def pair_text(instruction: str, given: str, output: str) -> str:
    """The text ``clean_text`` checks and deduplicates: the three fields, in order."""
    return "\n\n".join(part for part in (instruction, given, output) if part)


def pairs_rows(source: Path, relpaths: list[str], config: PairsIngest, *, stage: str) -> list[Row]:
    rows: list[Row] = []
    for relpath in sorted(relpaths):
        if Path(relpath).suffix.lower() != ".jsonl":
            rows.append(_reject({"row_key": relpath}, "unsupported_format", "not .jsonl", stage))
            continue
        with (source / relpath).open(encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                key = f"{relpath}#{number:07d}"
                rows.append(_pair_row(key, number, line, config, stage))
    rows.sort(key=lambda row: str(row["row_key"]))
    return rows


def _pair_row(key: str, number: int, line: str, config: PairsIngest, stage: str) -> Row:
    base: Row = {"row_key": key, "format": "JSONL"}
    try:
        obj: Any = json.loads(line)
    except json.JSONDecodeError as exc:
        return _reject(base, "not_json", f"line {number}: {exc}", stage)
    fields: dict[str, Any] = obj if isinstance(obj, dict) else {}  # pyright: ignore[reportUnknownVariableType]
    values: dict[str, str] = {}
    for name, field_name, required in (
        ("instruction", config.instruction_field, True),
        ("input", config.input_field, False),
        ("output", config.output_field, True),
    ):
        value = fields.get(field_name)
        if value is None and not required:
            value = ""
        if not isinstance(value, str):
            return _reject(base, "missing_field", f"line {number}: no string '{field_name}'", stage)
        values[name] = value
    if not values["output"].strip():
        return _reject(base, "empty_output", f"line {number}: the output is empty", stage)
    text = pair_text(values["instruction"], values["input"], values["output"])
    row: Row = {
        **base,
        "sha256": content_hash(text),
        "size_bytes": len(text.encode("utf-8")),
        "text": text,
        "record": canonical_json(values),
        "status": STATUS_ACCEPTED,
    }
    if len(text) > config.max_chars:
        return _reject(row, "text_too_long", f"{len(text):,} characters", stage)
    return row


def chat_messages(record: dict[str, Any]) -> list[dict[str, str]]:
    """The pairs' adapter (ADR-0022 rule 2): a pair as chat messages, the instruction and its
    input as the user's turn, the output as the assistant's. M13's QLoRA renders these with
    the model's chat template."""
    instruction = str(record.get("instruction") or "")
    given = str(record.get("input") or "")
    user = f"{instruction}\n\n{given}" if given else instruction
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": str(record.get("output") or "")},
    ]


def qwen_chat_format(messages: list[dict[str, str]]) -> str:
    """Qwen3's chat format (ChatML), as the GGUF's template renders a plain exchange; shown on
    SCR-03 and checked against the model's own template in M13."""
    return "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages)


# ---------------------------------------------------------------------------- chunk


class ChunkConfig(BaseModel):
    """Settings for ``chunk`` (ADR-0022 rule 3). The resolved values are part of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_tokens: int = Field(
        default=256, ge=64, le=480, description="The most tokens in a chunk (bge reads 512)."
    )
    overlap_tokens: int = Field(
        default=32, ge=0, le=128, description="Tokens a chunk repeats from the one before it."
    )
    min_tokens: int = Field(
        default=32, ge=0, le=256, description="A shorter section joins the next one."
    )
    tokenizer: str = Field(
        default="bge-small-en-v1.5",
        json_schema_extra={"readOnly": True},
        description="The allow-listed model whose tokenizer counts the tokens.",
    )
    tokenizer_revision: str = Field(
        default="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        json_schema_extra={"readOnly": True},
    )
    algorithm_version: int = Field(
        default=1,
        ge=1,
        json_schema_extra={"readOnly": True},
        description="Version of the chunking code; a new value gives a new version ID.",
    )


@dataclass(frozen=True)
class Section:
    path: tuple[str, ...]
    start: int
    end: int


@dataclass(frozen=True)
class Chunk:
    section: tuple[str, ...]
    start: int
    end: int
    tokens: int


def sections(text: str, title: str) -> list[Section]:
    """The document cut at ``#``, ``##`` and ``###`` headings outside code fences; each with
    its path (title › level-2 › level-3) and its span, heading line included."""
    starts: list[tuple[int, tuple[str, ...]]] = []
    level2: str | None = None
    offset = 0
    fenced = False
    if text and not _HEADING.match(text.split("\n", 1)[0]):
        starts.append((0, (title,)))
    for line in text.split("\n"):
        if _FENCE.match(line.strip()):
            fenced = not fenced
        match = None if fenced else _HEADING.match(line)
        if match:
            level, name = len(match.group(1)), match.group(2).strip()
            if level == 1:
                path: tuple[str, ...] = (title,)
                level2 = None
            elif level == 2:
                level2 = name
                path = (title, name)
            else:
                path = (title, level2, name) if level2 else (title, name)
            starts.append((offset, path))
        offset += len(line) + 1
    found: list[Section] = []
    for i, (start, path) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        if text[start:end].strip():
            found.append(Section(path, start, end))
    return found


@dataclass(frozen=True)
class _Piece:
    start: int
    end: int
    tokens: int
    paragraph: int


def _spans(text: str, pattern: re.Pattern[str], start: int, end: int) -> list[tuple[int, int]]:
    """The non-blank stretches of ``text[start:end]`` between matches of ``pattern``."""
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in pattern.finditer(text, start, end):
        if text[cursor : match.start()].strip():
            spans.append(_trim(text, cursor, match.start()))
        cursor = match.end()
    if text[cursor:end].strip():
        spans.append(_trim(text, cursor, end))
    return spans


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _pieces(text: str, section: Section, count: TokenCounter, limit: int) -> list[_Piece]:
    """Sentences (lines count as sentences), grouped by paragraph; a sentence longer than
    ``limit`` is cut into runs of words."""
    pieces: list[_Piece] = []
    for number, (p_start, p_end) in enumerate(_spans(text, _PARAGRAPH, section.start, section.end)):
        for s_start, s_end in _spans(text, _SENTENCE, p_start, p_end):
            tokens = count(text[s_start:s_end])
            if tokens <= limit:
                pieces.append(_Piece(s_start, s_end, tokens, number))
                continue
            words = list(_WORD.finditer(text, s_start, s_end))
            run_start, run_tokens = words[0].start(), 0
            last_end = words[0].end()
            for word in words:
                word_tokens = count(word.group())
                if run_tokens and run_tokens + word_tokens > limit:
                    pieces.append(_Piece(run_start, last_end, run_tokens, number))
                    run_start, run_tokens = word.start(), 0
                run_tokens += word_tokens
                last_end = word.end()
            pieces.append(_Piece(run_start, last_end, run_tokens, number))
    return pieces


def _pack(pieces: list[_Piece], config: ChunkConfig) -> list[list[_Piece]]:
    """Whole paragraphs while they fit, else sentence by sentence; each new chunk starts
    with the previous chunk's last sentences, up to ``overlap_tokens``."""
    chunks: list[list[_Piece]] = []
    current: list[_Piece] = []
    fresh = 0  # pieces of ``current`` that are not overlap

    def total(items: list[_Piece]) -> int:
        return sum(p.tokens for p in items)

    def close() -> None:
        nonlocal current, fresh
        if fresh:
            chunks.append(current)
            tail: list[_Piece] = []
            for piece in reversed(current):
                if total(tail) + piece.tokens > config.overlap_tokens:
                    break
                tail.insert(0, piece)
            current, fresh = tail, 0

    by_paragraph: dict[int, list[_Piece]] = {}
    for piece in pieces:
        by_paragraph.setdefault(piece.paragraph, []).append(piece)
    limit = config.chunk_tokens
    for paragraph in by_paragraph.values():
        if fresh and total(current) + total(paragraph) > limit and total(paragraph) <= limit:
            close()  # a paragraph that fits on its own starts the next chunk
        for piece in paragraph:
            if fresh and total(current) + piece.tokens > limit:
                close()
            while current and total(current) + piece.tokens > limit:
                current.pop(0)  # the overlap gives way to a piece that would not fit
            current.append(piece)
            fresh += 1
    if fresh:
        chunks.append(current)
    return chunks


def chunk_document(text: str, title: str, config: ChunkConfig, count: TokenCounter) -> list[Chunk]:
    """A cleaned document's chunks, in order (ADR-0022 rule 3)."""
    found = sections(text, title)
    merged: list[Section] = []
    carry: Section | None = None
    for section in found:
        if carry is not None:
            section = Section(section.path, carry.start, section.end)
            carry = None
        if (
            count(text[section.start : section.end]) < config.min_tokens
            and section is not found[-1]
        ):
            carry = section
            continue
        merged.append(section)
    if carry is not None:
        merged.append(carry)
    chunks: list[Chunk] = []
    for section in merged:
        pieces = _pieces(text, section, count, config.chunk_tokens)
        for group in _pack(pieces, config):
            start, end = group[0].start, group[-1].end
            chunks.append(Chunk(section.path, start, end, count(text[start:end])))
    return chunks


def chunk_rows(document: Row, config: ChunkConfig, count: TokenCounter) -> Iterator[Row]:
    """An accepted document's chunk rows, keyed ``<document>#<chunk number, 4 digits>``."""
    text = str(document.get("text") or "")
    path = str(document["row_key"])
    title = str(document.get("title") or Path(path).stem)
    for number, chunk in enumerate(chunk_document(text, title, config, count), start=1):
        body = text[chunk.start : chunk.end]
        yield {
            "row_key": f"{path}#{number:04d}",
            "sha256": content_hash(body),
            "size_bytes": len(body.encode("utf-8")),
            "format": document.get("format"),
            "text": body,
            "title": title,
            "document": path,
            "section": SECTION_SEPARATOR.join(chunk.section),
            "char_start": chunk.start,
            "char_end": chunk.end,
            "token_count": chunk.tokens,
            "status": STATUS_ACCEPTED,
        }


# ---------------------------------------------------------------------------- index

META_RAG_INDEX = "stratal.rag_index"
EMBEDDING_MODEL = "bge-small-en-v1.5"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class IndexConfig(BaseModel):
    """Settings for ``index`` (ADR-0022 rule 4). The resolved values are part of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    embedding_model: str = Field(
        default=EMBEDDING_MODEL,
        json_schema_extra={"readOnly": True},
        description="The allow-listed embedding model (M12 decision 4).",
    )
    embedding_revision: str = Field(
        default=get_model(EMBEDDING_MODEL).revision, json_schema_extra={"readOnly": True}
    )
    query_prefix: str = Field(
        default=BGE_QUERY_PREFIX,
        json_schema_extra={"readOnly": True},
        description="Put before a question when it is embedded, as bge expects for short queries.",
    )
    batch_size: int = Field(default=32, ge=1, le=256)
    algorithm_version: int = Field(default=1, ge=1, json_schema_extra={"readOnly": True})
