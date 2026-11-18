"""jobs.pause_reason (RUN-1.6)

Why a paused job is paused, 'user' or 'thermal', so the watchdog resumes only
the jobs it paused for heat (ADR-0014 rule 7).

Revision ID: c81f4d20e7a9
Revises: a3c7e19b52d4
Create Date: 2026-11-18 14:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c81f4d20e7a9"
down_revision: str | None = "a3c7e19b52d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("pause_reason", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_jobs_pause_reason",
        "jobs",
        "pause_reason IS NULL OR pause_reason IN ('user', 'thermal')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_pause_reason", "jobs", type_="check")
    op.drop_column("jobs", "pause_reason")
