"""Snapshot Stratal's documentation as the phase-3 corpus (M12 decision 5, ADR-0022).

Copies what decision 5 names into ``sources/stratal-docs``: the specification and
design documents (``docs/*.md`` except MILESTONES and KICKOFF, which track work in
progress), the ADRs (``docs/decisions/``) and the spike reports (``docs/spikes/``, with
their result files, which ingest rejects as ``unsupported_format``). ``docs/learning/``
is never copied. An existing snapshot is replaced, so the corpus is what the docs say
now; ingest it again to get new versions. Nothing is downloaded.

    docker compose run --rm --no-deps dev python -I scripts/datasets/stratal_docs.py

Then ingest ``/sources/stratal-docs`` with ``dataset_type: text_corpus``.
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
TARGET = ROOT / "sources" / "stratal-docs"
LEFT_OUT = {"MILESTONES.md", "KICKOFF.md"}


def main() -> int:
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    copied = 0
    for path in sorted(DOCS.glob("*.md")):
        if path.name not in LEFT_OUT:
            shutil.copy2(path, TARGET / path.name)
            copied += 1
    for folder in ("decisions", "spikes"):
        for path in sorted((DOCS / folder).rglob("*")):
            if path.is_file():
                destination = TARGET / folder / path.relative_to(DOCS / folder)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                copied += 1
    assert not (TARGET / "learning").exists()
    print(f"{copied} files in {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
