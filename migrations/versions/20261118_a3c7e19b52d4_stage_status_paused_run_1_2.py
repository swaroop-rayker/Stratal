"""stage status paused (RUN-1.2)

A stage stopped by a pause, or the next stage of a job paused between stages,
waits as ``paused`` until the job is resumed (ADR-0014 rule 6). Alembic does not
detect CHECK constraint changes, so this migration is written by hand.

Revision ID: a3c7e19b52d4
Revises: f08fed6f6ad9
Create Date: 2026-11-18 11:20:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3c7e19b52d4"
down_revision: str | None = "f08fed6f6ad9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WITH = "'pending', 'queued', 'running', 'paused', 'completed', 'failed', 'skipped', 'cancelled'"
_WITHOUT = "'pending', 'queued', 'running', 'completed', 'failed', 'skipped', 'cancelled'"


def upgrade() -> None:
    op.drop_constraint("ck_job_stages_status", "job_stages", type_="check")
    op.create_check_constraint("ck_job_stages_status", "job_stages", f"status IN ({_WITH})")


def downgrade() -> None:
    op.execute("UPDATE job_stages SET status = 'queued' WHERE status = 'paused'")
    op.drop_constraint("ck_job_stages_status", "job_stages", type_="check")
    op.create_check_constraint("ck_job_stages_status", "job_stages", f"status IN ({_WITHOUT})")
