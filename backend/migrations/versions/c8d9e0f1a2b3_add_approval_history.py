"""add approval_history append-only event table

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-04-13 00:30:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "approval_history" in existing_tables:
        return
    op.create_table(
        "approval_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "approval_id",
            sa.Uuid(),
            sa.ForeignKey("approvals.id"),
            nullable=False,
        ),
        sa.Column(
            "board_id",
            sa.Uuid(),
            sa.ForeignKey("boards.id"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "actor_agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id"),
            nullable=True,
        ),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    # Indexes for the rejection-loop guard query:
    # - approval_id (chronological per approval)
    # - (board_id, task_id, created_at) — loop check window scan
    # - event_type (count by type)
    op.create_index(
        "ix_approval_history_approval_id",
        "approval_history",
        ["approval_id"],
    )
    op.create_index(
        "ix_approval_history_board_task_time",
        "approval_history",
        ["board_id", "task_id", "created_at"],
    )
    op.create_index(
        "ix_approval_history_event_type",
        "approval_history",
        ["event_type"],
    )
    op.create_index(
        "ix_approval_history_actor_user_id",
        "approval_history",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_approval_history_actor_agent_id",
        "approval_history",
        ["actor_agent_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "approval_history" not in set(inspector.get_table_names()):
        return
    op.drop_index(
        "ix_approval_history_actor_agent_id",
        table_name="approval_history",
    )
    op.drop_index(
        "ix_approval_history_actor_user_id",
        table_name="approval_history",
    )
    op.drop_index(
        "ix_approval_history_event_type",
        table_name="approval_history",
    )
    op.drop_index(
        "ix_approval_history_board_task_time",
        table_name="approval_history",
    )
    op.drop_index(
        "ix_approval_history_approval_id",
        table_name="approval_history",
    )
    op.drop_table("approval_history")
