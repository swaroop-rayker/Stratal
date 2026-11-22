"""Type the loads and unloads stored before M11 (MON-3.3)

Until ADR-0020 the VRAM manager's result events were ``activity`` events. They carry
the same before-and-after metrics, so they are given the types SCR-13 reads:
``model_loaded`` for "Loaded … to GPU" and ``model_unloaded`` for "Unloaded …".
Only rows with the manager's metrics are touched; nothing is deleted. The card's
figure was not recorded then, so those rows show PyTorch's only.

Revision ID: e3b6f1a9c204
Revises: d20a7e5c3b18
Create Date: 2026-11-22 13:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e3b6f1a9c204"
down_revision: str | None = "d20a7e5c3b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE events SET type = 'model_loaded' WHERE type = 'activity' "
        "AND message LIKE 'Loaded % to GPU%' AND metrics ? 'allocated_mb_after'"
    )
    op.execute(
        "UPDATE events SET type = 'model_unloaded' WHERE type = 'activity' "
        "AND message LIKE 'Unloaded %' AND metrics ? 'allocated_mb_after'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE events SET type = 'activity' WHERE type IN ('model_loaded', 'model_unloaded')"
    )
