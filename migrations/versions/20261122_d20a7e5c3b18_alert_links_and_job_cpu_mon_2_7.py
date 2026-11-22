"""events.caused_by and process_samples.cpu_cores (MON-2.7, MON-3.2)

``events.caused_by`` holds the ``event_uid`` of the alert that caused an action or
a clearing (ADR-0020 rule 4); ``cpu_cores`` is the job process tree's CPU in cores
(ADR-0020 rule 6), averaged into the one-minute table too.

Revision ID: d20a7e5c3b18
Revises: c4e8a0d2f917
Create Date: 2026-11-22 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d20a7e5c3b18"
down_revision: str | None = "c4e8a0d2f917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("caused_by", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_events_caused_by",
        "events",
        ["caused_by"],
        postgresql_where=sa.text("caused_by IS NOT NULL"),
    )
    op.add_column("process_samples", sa.Column("cpu_cores", sa.REAL(), nullable=True))
    op.add_column("process_samples_1m", sa.Column("cpu_cores", sa.REAL(), nullable=True))


def downgrade() -> None:
    op.drop_column("process_samples_1m", "cpu_cores")
    op.drop_column("process_samples", "cpu_cores")
    op.drop_index("ix_events_caused_by", table_name="events")
    op.drop_column("events", "caused_by")
