"""DATA-5.3 and LLM-2.1 (ADR-0022 rules 1 to 3): ingesting corpora and instruction pairs,
the pairs' adapter, and chunking. Tokens are counted as words here; the worker counts
them with the embedding model's tokenizer."""

import itertools
import json
from pathlib import Path

from stratal.data.corpus import (
    ChunkConfig,
    CorpusIngest,
    PairsIngest,
    chat_messages,
    chunk_document,
    chunk_rows,
    corpus_rows,
    document_title,
    pairs_rows,
    qwen_chat_format,
    sections,
)
from stratal.data.record_clean import CleanTextConfig
from stratal.data.records import normalise_text


def words(text: str) -> int:
    return len(text.split())


DOC = """# Stratal — Data Model

How data is kept.

## 3. Identifiers

| Entity | Format |
|---|---|
| Dataset version | ds_ + 12 hex |

## 4. PostgreSQL schema

Tables are created by migrations.

### 4.1 datasets

```python
# not a heading
x = 1
```

The datasets table holds one row per dataset.
"""


def test_DATA_5_3_corpus_ingest_one_row_per_document(tmp_path: Path) -> None:
    (tmp_path / "decisions").mkdir()
    (tmp_path / "DATA_MODEL.md").write_text(DOC, encoding="utf-8")
    (tmp_path / "decisions" / "ADR-0004.md").write_text("# ADR-0004 — Storage\n\nText.", "utf-8")
    (tmp_path / "S-11-results.json").write_text("{}", encoding="utf-8")
    (tmp_path / "latin.txt").write_bytes("caf\xe9".encode("latin-1"))
    rows = corpus_rows(
        tmp_path,
        ["DATA_MODEL.md", "decisions/ADR-0004.md", "S-11-results.json", "latin.txt"],
        CorpusIngest(),
        stage="ingest",
    )
    by_key = {r["row_key"]: r for r in rows}
    assert by_key["DATA_MODEL.md"]["title"] == "Stratal — Data Model"
    assert by_key["DATA_MODEL.md"]["format"] == "MD" and by_key["DATA_MODEL.md"]["text"] == DOC
    assert by_key["decisions/ADR-0004.md"]["status"] == "accepted"
    assert by_key["S-11-results.json"]["reason_code"] == "unsupported_format"
    assert by_key["latin.txt"]["reason_code"] == "not_utf8"
    assert document_title("no heading here", "notes/todo.txt") == "todo"


def test_DATA_5_3_pairs_ingest_rejects_bad_rows_with_reasons(tmp_path: Path) -> None:
    lines = [
        {"instruction": "Summarise ADR-0006.", "output": "One process per GPU job."},
        {
            "instruction": "Turn this into JSON.",
            "input": "A plane.",
            "output": '{"subject": "plane"}',
        },
        {"instruction": "Explain hit@5.", "output": "   "},
        {"output": "No instruction."},
    ]
    text = "\n".join(json.dumps(x) for x in lines) + "\nnot json\n"
    (tmp_path / "pairs.jsonl").write_text(text, encoding="utf-8")
    rows = pairs_rows(tmp_path, ["pairs.jsonl"], PairsIngest(), stage="ingest")
    assert [r.get("reason_code") for r in rows] == [
        None,
        None,
        "empty_output",
        "missing_field",
        "not_json",
    ]
    record = json.loads(rows[1]["record"])
    assert record == {
        "input": "A plane.",
        "instruction": "Turn this into JSON.",
        "output": '{"subject": "plane"}',
    }
    assert rows[1]["text"] == 'Turn this into JSON.\n\nA plane.\n\n{"subject": "plane"}'


def test_DATA_5_3_pairs_adapter_gives_the_chat_format() -> None:
    messages = chat_messages(
        {"instruction": "Turn this into JSON.", "input": "A plane.", "output": "{}"}
    )
    assert messages == [
        {"role": "user", "content": "Turn this into JSON.\n\nA plane."},
        {"role": "assistant", "content": "{}"},
    ]
    assert (
        chat_messages({"instruction": "Hi", "input": "", "output": "Hello"})[0]["content"] == "Hi"
    )
    assert qwen_chat_format(messages) == (
        "<|im_start|>user\nTurn this into JSON.\n\nA plane.<|im_end|>\n"
        "<|im_start|>assistant\n{}<|im_end|>\n"
    )


def test_LLM_2_1_corpus_clean_keeps_lines_and_old_configs_keep_their_ids() -> None:
    text = "# Title  \r\n\r\n\r\n\r\nA   line\twith  spaces.\nNext line."
    assert normalise_text(text, keep_lines=True) == "# Title\n\nA line with spaces.\nNext line."
    assert normalise_text(text) == "# Title A line with spaces. Next line."
    assert "keep_line_breaks" not in CleanTextConfig().model_dump(mode="json")
    assert (
        CleanTextConfig(keep_line_breaks=True).model_dump(mode="json")["keep_line_breaks"] is True
    )


def test_LLM_2_1_sections_follow_headings_not_code_fences() -> None:
    found = sections(DOC, "Stratal — Data Model")
    assert [s.path for s in found] == [
        ("Stratal — Data Model",),
        ("Stratal — Data Model", "3. Identifiers"),
        ("Stratal — Data Model", "4. PostgreSQL schema"),
        ("Stratal — Data Model", "4. PostgreSQL schema", "4.1 datasets"),
    ]
    assert DOC[found[1].start :].startswith("## 3. Identifiers")
    assert found[-1].end == len(DOC)


def test_LLM_2_1_chunks_keep_their_section_and_offsets() -> None:
    config = ChunkConfig(chunk_tokens=64, overlap_tokens=0, min_tokens=0)
    chunks = chunk_document(DOC, "Stratal — Data Model", config, words)
    for chunk in chunks:
        assert chunk.tokens == words(DOC[chunk.start : chunk.end]) <= 64
    assert ("Stratal — Data Model", "3. Identifiers") in [c.section for c in chunks]
    rows = list(
        chunk_rows(
            {
                "row_key": "DATA_MODEL.md",
                "text": DOC,
                "title": "Stratal — Data Model",
                "format": "MD",
            },
            config,
            words,
        )
    )
    assert rows[1]["row_key"] == "DATA_MODEL.md#0002"
    assert rows[1]["section"] == "Stratal — Data Model › 3. Identifiers"
    assert rows[1]["text"] == DOC[rows[1]["char_start"] : rows[1]["char_end"]]


def test_LLM_2_1_a_short_section_joins_the_next() -> None:
    config = ChunkConfig(chunk_tokens=64, overlap_tokens=0, min_tokens=10)
    chunks = chunk_document(DOC, "Stratal — Data Model", config, words)
    paths = [c.section for c in chunks]
    # "4. PostgreSQL schema" has 9 words: it joins 4.1, and the chunk takes 4.1's path.
    assert ("Stratal — Data Model", "4. PostgreSQL schema") not in paths
    last = chunks[-1]
    assert DOC[last.start :].startswith("## 4. PostgreSQL schema")


def test_LLM_2_1_long_sections_split_with_overlap() -> None:
    sentences = [f"Sentence number {i} says something about version identity." for i in range(60)]
    body = "## Long\n\n" + " ".join(sentences[:30]) + "\n\n" + " ".join(sentences[30:])
    config = ChunkConfig(chunk_tokens=64, overlap_tokens=16, min_tokens=0)
    chunks = chunk_document(body, "Doc", config, words)
    assert len(chunks) > 5
    for chunk in chunks:
        assert chunk.tokens <= 64
    for before, after in itertools.pairwise(chunks):
        assert after.start < before.end  # neighbours share their edge
        shared = body[after.start : before.end]
        assert 0 < words(shared) <= 16
    covered = set[int]()
    for chunk in chunks:
        covered.update(range(chunk.start, chunk.end))
    text_chars = {i for i, c in enumerate(body) if not c.isspace() and i >= body.index("Sentence")}
    assert text_chars <= covered  # nothing is lost


def test_LLM_2_1_chunking_is_deterministic() -> None:
    config = ChunkConfig()
    assert chunk_document(DOC, "T", config, words) == chunk_document(DOC, "T", config, words)
