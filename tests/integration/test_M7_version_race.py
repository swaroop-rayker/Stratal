"""BUG1: two jobs that make the same version at the same moment both succeed.

Found in M6 (job_0022): two identical demo runs computed the same version ID and
the second insert hit the primary key. Runs against the real PostgreSQL with two
connections, as two workers would.
"""

import threading
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from stratal.core.errors import Conflict
from stratal.data import registry
from stratal.data.commit import VersionRecord
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.db.engine import get_engine
from stratal.db.models import DatasetVersion
from stratal.testing.fakes import ListSink, fake_version_identity
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.integration


def _raw(tmp_path: Path) -> VersionRecord:
    folder = make_image_folder(tmp_path / "src", valid=4)
    return run_ingest(
        source=folder.root,
        data_dir=tmp_path / "data",
        config=IngestConfig(),
        derive=fake_version_identity,
        sink=ListSink(),
    ).record


def test_BUG1_two_jobs_registering_the_same_version_at_once_both_succeed(
    bus: object, tmp_path: Path
) -> None:
    record = _raw(tmp_path)
    engine = get_engine()
    with Session(engine) as setup:
        registry.ensure_dataset(setup, slug="race", modality="image")
        setup.commit()

    first = Session(engine)
    dataset = registry.ensure_dataset(first, slug="race", modality="image")
    _, first_new = registry.register_version(
        first, dataset=dataset, record=record, new_blob_bytes=10
    )

    outcome: dict[str, object] = {}

    def second_job() -> None:
        with Session(engine) as second:
            try:
                other = registry.ensure_dataset(second, slug="race", modality="image")
                version, new = registry.register_version(
                    second, dataset=other, record=record, new_blob_bytes=0
                )
                second.commit()
                outcome["result"] = (version.version_id, new)
            except Exception as error:  # reported to the test below
                outcome["error"] = error

    thread = threading.Thread(target=second_job)
    thread.start()
    thread.join(timeout=1.0)
    assert thread.is_alive(), "the second insert should wait for the first transaction"

    first.commit()
    first.close()
    thread.join(timeout=10.0)

    assert "error" not in outcome, outcome.get("error")
    assert first_new is True
    assert outcome["result"] == (record.version_id, False)
    with Session(engine) as check:
        rows = check.query(DatasetVersion).filter_by(version_id=record.version_id).all()
        assert len(rows) == 1 and rows[0].new_blob_bytes == 10


def test_BUG1_a_different_version_under_the_same_id_is_refused(
    session: Session, tmp_path: Path
) -> None:
    record = _raw(tmp_path)
    dataset = registry.ensure_dataset(session, slug="race-collision", modality="image")
    registry.register_version(session, dataset=dataset, record=record, new_blob_bytes=0)

    impostor = replace(record, content_hash="f" * 64)
    with pytest.raises(Conflict, match="already registered with different content"):
        registry.register_version(session, dataset=dataset, record=impostor, new_blob_bytes=0)
