"""Add structured task review verdict events.

Revision ID: f1a2b3c4d5e6
Revises: f0a1b2c3d4e5
Create Date: 2026-04-27 09:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("board_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("reviewer_role", sa.String(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("evidence_type", sa.String(), nullable=True),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("build_hash", sa.String(), nullable=True),
        sa.Column("source_commit", sa.String(), nullable=True),
        sa.Column("blocking_owner", sa.String(), nullable=True),
        sa.Column("suggested_routing", sa.String(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_review_events_agent_id", "task_review_events", ["agent_id"])
    op.create_index("ix_task_review_events_blocking_owner", "task_review_events", ["blocking_owner"])
    op.create_index("ix_task_review_events_board_id", "task_review_events", ["board_id"])
    op.create_index("ix_task_review_events_build_hash", "task_review_events", ["build_hash"])
    op.create_index("ix_task_review_events_created_at", "task_review_events", ["created_at"])
    op.create_index("ix_task_review_events_evidence_type", "task_review_events", ["evidence_type"])
    op.create_index("ix_task_review_events_reviewer_role", "task_review_events", ["reviewer_role"])
    op.create_index("ix_task_review_events_source_commit", "task_review_events", ["source_commit"])
    op.create_index("ix_task_review_events_task_id", "task_review_events", ["task_id"])
    op.create_index("ix_task_review_events_verdict", "task_review_events", ["verdict"])


def downgrade() -> None:
    op.drop_index("ix_task_review_events_verdict", table_name="task_review_events")
    op.drop_index("ix_task_review_events_task_id", table_name="task_review_events")
    op.drop_index("ix_task_review_events_source_commit", table_name="task_review_events")
    op.drop_index("ix_task_review_events_reviewer_role", table_name="task_review_events")
    op.drop_index("ix_task_review_events_evidence_type", table_name="task_review_events")
    op.drop_index("ix_task_review_events_created_at", table_name="task_review_events")
    op.drop_index("ix_task_review_events_build_hash", table_name="task_review_events")
    op.drop_index("ix_task_review_events_board_id", table_name="task_review_events")
    op.drop_index("ix_task_review_events_blocking_owner", table_name="task_review_events")
    op.drop_index("ix_task_review_events_agent_id", table_name="task_review_events")
    op.drop_table("task_review_events")
