"""LLM-2.2 (ADR-0022 rules 9 to 11): the retrieval report of an index version, built by a
job with the real embedder and index, stored and listed per dataset (marker ``ml``)."""
# pyright: basic, reportMissingImports=false

import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from stratal.core.errors import ValidationFailed
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.db.models import EvalReport
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import PipelineRequest
from stratal.testing.harness import Runner

pytestmark = [pytest.mark.integration, pytest.mark.ml]
pytest.importorskip("sentence_transformers")
pytest.importorskip("faiss")

REAL_MODELS = Path("/data/models")


def _run(runner: Runner, created: tuple[str, Any]) -> dict[str, Any]:
    job_id, first = created
    runner.queue.append(first)
    runner.run_queued()
    runner.collect()
    return runner.job(job_id)


def test_LLM_2_2_report_on_an_index_version(runner: Runner, tmp_path: Path) -> None:
    if not (REAL_MODELS / "bge-small-en-v1.5").is_dir():
        pytest.skip("bge-small-en-v1.5 is not pulled")
    link = get_settings().data_dir / "models"
    if not link.exists():
        os.symlink(REAL_MODELS, link)
    source = tmp_path / "docs"
    (source / "decisions").mkdir(parents=True)
    (source / "DATA_MODEL.md").write_text(
        "# Stratal — Data Model\n\n## 3. Identifiers\n\nA dataset version ID is ds_ followed by "
        "twelve hex characters of a SHA-256 over the content hash and the config hash.\n",
        encoding="utf-8",
    )
    (source / "decisions" / "ADR-0007-memory-budget.md").write_text(
        "# ADR-0007 — Memory budget\n\n## Decision\n\nCap the WSL VM at 10 GB with 4 GB of swap, "
        "and keep all containers under 9 GB together.\n",
        encoding="utf-8",
    )
    body = {
        "kind": "pipeline",
        "dataset": "smalldocs",
        "source": {"path": str(source)},
        "dataset_type": "text_corpus",
    }
    with Session(get_engine()) as session, session.begin():
        job, first = job_service.create_pipeline_job(session, PipelineRequest.model_validate(body))
        pipeline = (job.id, first)
    built = _run(runner, pipeline)
    stages = {s["name"]: s["output_version_id"] for s in built["stages"]}

    with (
        Session(get_engine()) as session,
        session.begin(),
        pytest.raises(ValidationFailed, match="index version"),
    ):
        job_service.create_report_job(session, stages["chunk"], {"kind": "retrieval"})

    with Session(get_engine()) as session, session.begin():
        report_job, first = job_service.create_report_job(
            session, stages["index"], {"kind": "retrieval"}
        )
        created = (report_job.id, first)
    job = _run(runner, created)
    assert job["state"] == "completed", job
    with Session(get_engine()) as session:
        row = session.scalars(select(EvalReport).where(EvalReport.kind == "retrieval")).one()
        report = row.report
    assert row.version_id == stages["index"]
    assert report["question_set"]["name"] == "stratal-docs-questions-v2"
    assert report["question_set"]["answerable"] == 50
    assert set(report["hits"]) == {"hit@1", "hit@5", "hit@10"}
    # The two planted sections answer a01 and a19 (IDs and the VM's memory cap).
    first = {q["id"]: q["first_hit"] for q in report["per_question"]}
    assert first["a01"] == 1
    assert first["a19"] == 1
    assert report["index"]["chunk_tokens"] == 256 and report["index"]["chunks"] >= 2
    assert len(report["thresholds"]) == 11
    assert len(report["relevance"]["unanswerable"]) == 15
