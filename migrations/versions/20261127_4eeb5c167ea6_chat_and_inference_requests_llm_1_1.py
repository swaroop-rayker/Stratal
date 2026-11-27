"""Chat sessions, their messages, and every LLM request with its timings (LLM-1.1, NFR-6)

ADR-0021 rule 9: ``chat_sessions`` and ``chat_messages`` hold SCR-12's conversations;
``inference_requests`` one row per chat, RAG or image-search request, kept when its
conversation is deleted, since its timings are the NFR-6 record.

Revision ID: 4eeb5c167ea6
Revises: e3b6f1a9c204
Create Date: 2026-11-27 05:57:22.121999
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4eeb5c167ea6"
down_revision: str | None = "e3b6f1a9c204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_sessions_updated",
        "chat_sessions",
        [sa.literal_column("updated_at DESC")],
        unique=False,
    )
    op.create_table(
        "inference_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("revision", sa.Text(), nullable=True),
        sa.Column("device", sa.Text(), nullable=True),
        sa.Column("index_version_id", sa.Text(), nullable=True),
        sa.Column("cold_start", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("load_ms", sa.Float(), nullable=True),
        sa.Column("ttft_ms", sa.Float(), nullable=True),
        sa.Column("total_ms", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("tokens_per_s", sa.Float(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('llm', 'rag', 'images')", name="ck_inference_requests_kind"),
        sa.CheckConstraint(
            "status IN ('ok', 'refused', 'failed', 'stopped')", name="ck_inference_requests_status"
        ),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inference_requests_created",
        "inference_requests",
        [sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("mode IN ('llm', 'rag', 'images')", name="ck_chat_messages_mode"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
        sa.ForeignKeyConstraint(["request_id"], ["inference_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_session", "chat_messages", ["session_id", "id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_inference_requests_created", table_name="inference_requests")
    op.drop_table("inference_requests")
    op.drop_index("ix_chat_sessions_updated", table_name="chat_sessions")
    op.drop_table("chat_sessions")
