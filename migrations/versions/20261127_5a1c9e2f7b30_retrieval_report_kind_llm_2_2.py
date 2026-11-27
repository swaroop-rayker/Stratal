"""A second report kind, ``retrieval``: hit@k of an index version (LLM-2.2, ADR-0022 rule 11)

Revision ID: 5a1c9e2f7b30
Revises: 4eeb5c167ea6
Create Date: 2026-11-27 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5a1c9e2f7b30"
down_revision: str | None = "4eeb5c167ea6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_eval_reports_kind", "eval_reports", type_="check")
    op.create_check_constraint(
        "ck_eval_reports_kind", "eval_reports", "kind IN ('phase1_dataset', 'retrieval')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM eval_reports WHERE kind = 'retrieval'")
    op.drop_constraint("ck_eval_reports_kind", "eval_reports", type_="check")
    op.create_check_constraint("ck_eval_reports_kind", "eval_reports", "kind IN ('phase1_dataset')")
