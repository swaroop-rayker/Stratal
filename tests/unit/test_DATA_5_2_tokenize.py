"""DATA-5.2 for labelled text: words to IDs, the vocabulary from train only.

ADR-0019 rules 8 to 11.
"""

from pathlib import Path

import pytest

from stratal.core.errors import StratalError
from stratal.data.commit import commit_version
from stratal.data.records import META_VOCAB
from stratal.data.tokenize import (
    PAD_ID,
    UNK_ID,
    TokenizeConfig,
    Vocabulary,
    choose_max_length,
    decode_tokens,
    fit_vocabulary,
    run_tokenize,
    vocab_of,
    words,
)
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import Row, iter_rows, read_metadata
from stratal.testing.fakes import ListSink, fake_version_identity


def test_DATA_5_2_words_are_casefolded_runs_and_single_marks() -> None:
    assert words("A Dog's ball, red!") == ["a", "dog", "'", "s", "ball", ",", "red", "!"]


def test_DATA_5_2_the_vocabulary_is_deterministic_with_reserved_ids() -> None:
    texts = ["b a a", "c b a", "d"]

    vocab = fit_vocabulary(texts, vocab_size=10, min_freq=1)

    assert vocab.tokens == ("<pad>", "<unk>", "a", "b", "c", "d"), "by count, then alphabet"
    assert fit_vocabulary(list(reversed(texts)), vocab_size=10, min_freq=1) == vocab
    assert fit_vocabulary(texts, vocab_size=4, min_freq=1).tokens == ("<pad>", "<unk>", "a", "b")
    assert fit_vocabulary(texts, vocab_size=10, min_freq=2).tokens == ("<pad>", "<unk>", "a", "b")


def test_DATA_5_2_encoding_pads_cuts_and_maps_unknown_words() -> None:
    vocab = Vocabulary(("<pad>", "<unk>", "a", "b"))

    assert vocab.encode("a b zzz", 5) == ([2, 3, UNK_ID, PAD_ID, PAD_ID], 3)
    assert vocab.encode("a a a a", 2) == ([2, 2], 4)
    assert Vocabulary.from_json(vocab.to_json()) == vocab


def test_DATA_5_2_the_length_is_the_95th_percentile_rounded_up_to_8() -> None:
    assert choose_max_length([3] * 95 + [100] * 5) == 8
    assert choose_max_length([9] * 100) == 16
    assert choose_max_length([2000] * 10) == 512
    assert choose_max_length([]) == 8


def _split_version(tmp_path: Path, rows: list[Row]) -> Path:
    record = commit_version(
        data_dir=tmp_path,
        stage="split",
        parent_version_id="ds_parent",
        rows=rows,
        config={},
        derive=fake_version_identity,
        sink=ListSink(),
    )
    return tmp_path / record.manifest_relpath


def _row(key: str, text: str, split: str | None, status: str = "accepted") -> Row:
    row: Row = {"row_key": key, "status": status, "text": text, "label": "x", "split": split}
    if status != "accepted":
        row.update(decided_at_stage="clean_text", reason_code="empty_text")
    return row


def test_DATA_5_2_the_vocabulary_comes_from_train_rows_only(tmp_path: Path) -> None:
    manifest = _split_version(
        tmp_path,
        [
            _row("a", "red dog", "train"),
            _row("b", "red cat", "train"),
            _row("c", "blue whale", "test"),
            _row("d", "", None, status="rejected"),
        ],
    )

    result = run_tokenize(
        parent_manifest=manifest,
        parent_version_id="ds_split",
        data_dir=tmp_path,
        config=TokenizeConfig(min_freq=1, max_length=4),
        derive=fake_version_identity,
        sink=ListSink(),
    )

    out = tmp_path / result.record.manifest_relpath
    rows = {r["row_key"]: r for r in iter_rows(out)}
    assert "blue" not in result.vocabulary.tokens and "whale" not in result.vocabulary.tokens
    assert decode_tokens(rows["c"]["tokens"]) == [UNK_ID, UNK_ID, PAD_ID, PAD_ID]
    assert rows["a"]["token_count"] == 2
    assert rows["d"]["tokens"] is None, "only accepted rows are tokenized"
    meta = vocab_of(out)
    assert meta is not None
    assert meta == {"sha256": result.report["vocab_sha256"], "size": 5, "max_length": 4}
    assert META_VOCAB in read_metadata(out)
    stored = BlobStore(tmp_path).path_for(str(meta["sha256"])).read_text(encoding="utf-8")
    assert Vocabulary.from_json(stored) == result.vocabulary
    assert result.record.counts["tokenize"]["train_lengths"]["p95"] == 2


def test_DATA_5_2_tokenizing_needs_a_split_version(tmp_path: Path) -> None:
    record = commit_version(
        data_dir=tmp_path,
        stage="clean_text",
        parent_version_id=None,
        rows=[_row("a", "x", None)],
        config={},
        derive=fake_version_identity,
        sink=ListSink(),
    )
    with pytest.raises(StratalError, match="needs a split version"):
        run_tokenize(
            parent_manifest=tmp_path / record.manifest_relpath,
            parent_version_id="ds_x",
            data_dir=tmp_path,
            config=TokenizeConfig(),
            derive=fake_version_identity,
            sink=ListSink(),
        )
