"""The ``chunk`` stage: a corpus's cleaned documents into chunks (LLM-2.1, ADR-0022 rule 3).

Each accepted document becomes its chunks (``stratal.data.corpus``), each a row keyed
``<document>#<n>`` with its section path and offsets; a document that was rejected or a
duplicate keeps its row as it was, so the version's counts still show it. Tokens are
counted with the embedding model's own tokenizer, given in by the worker.
"""

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from stratal.core.errors import StratalError
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.corpus import ChunkConfig, TokenCounter, chunk_rows
from stratal.data.versioning import VersionDeriver
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.storage.manifest import STATUS_ACCEPTED, Row, iter_rows, read_metadata

STAGE = "chunk"


@dataclass(frozen=True)
class ChunkResult:
    record: VersionRecord
    metrics: dict[str, MetricValue]


def run_chunk(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: ChunkConfig,
    derive: VersionDeriver,
    sink: EventSink,
    count: TokenCounter,
    checkpoint: Callable[[], None] = lambda: None,
) -> ChunkResult:
    started = time.monotonic()
    made_by = read_metadata(parent_manifest).get("stratal.stage")
    if made_by != "clean_text":
        raise StratalError(
            f"Chunking needs a corpus's clean_text version; {parent_version_id} was made by "
            f"{made_by!r}.",
            suggested_action="Run the text-corpus pipeline through clean_text first.",
        )
    documents = 0
    chunks = 0
    tokens: list[int] = []

    def rows() -> Iterator[Row]:
        nonlocal documents, chunks
        for row in iter_rows(parent_manifest):
            if row["status"] != STATUS_ACCEPTED:
                yield row
                continue
            documents += 1
            checkpoint()
            for chunk in chunk_rows(row, config, count):
                chunks += 1
                tokens.append(int(chunk["token_count"]))
                yield chunk
            if documents % 10 == 0:
                sink.emit(
                    stage=STAGE,
                    message=f"Chunked {documents} documents · {chunks:,} chunks",
                    step=documents,
                )

    sink.emit(
        stage=STAGE,
        message=f"Cutting documents at headings, then into {config.chunk_tokens}-token chunks "
        f"with {config.overlap_tokens} tokens of overlap",
    )
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=sorted(rows(), key=lambda r: str(r["row_key"])),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
    )
    median = sorted(tokens)[len(tokens) // 2] if tokens else 0
    sink.emit(
        stage=STAGE,
        message=f"{chunks:,} chunks from {documents} documents · median {median} tokens",
        metrics={"documents": documents, "chunks": chunks, "median_tokens": median},
    )
    return ChunkResult(
        record,
        {
            "documents": documents,
            "chunks": chunks,
            "median_tokens": median,
            "seconds": round(time.monotonic() - started, 2),
        },
    )
