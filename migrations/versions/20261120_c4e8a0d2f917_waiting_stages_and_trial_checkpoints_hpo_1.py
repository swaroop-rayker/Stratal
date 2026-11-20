"""Deep-learning studies: a stage status ``waiting``, and checkpoints without a lineage
(HPO-1.2, ADR-0018 rules 5 and 7)

A study job's ``study`` stage is ``waiting`` between its short steps, while a trial
job trains. A trial whose lineage does not exist yet (a new architecture) writes
checkpoints before any lineage row exists, so ``checkpoints.lineage_id`` may be null.

Revision ID: c4e8a0d2f917
Revises: bbc1dd28651d
Create Date: 2026-11-20 14:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a0d2f917"
down_revision: str | None = "bbc1dd28651d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = ("pending", "queued", "running", "paused", "completed", "failed", "skipped", "cancelled")
_AFTER = (
    "pending",
    "queued",
    "running",
    "paused",
    "waiting",
    "completed",
    "failed",
    "skipped",
    "cancelled",
)


def _status_check(values: tuple[str, ...]) -> str:
    return f"status IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint("ck_job_stages_status", "job_stages", type_="check")
    op.create_check_constraint("ck_job_stages_status", "job_stages", _status_check(_AFTER))
    op.alter_column("checkpoints", "lineage_id", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM checkpoints WHERE lineage_id IS NULL")
    op.alter_column("checkpoints", "lineage_id", existing_type=sa.Text(), nullable=False)
    op.execute("UPDATE job_stages SET status = 'paused' WHERE status = 'waiting'")
    op.drop_constraint("ck_job_stages_status", "job_stages", type_="check")
    op.create_check_constraint("ck_job_stages_status", "job_stages", _status_check(_BEFORE))
