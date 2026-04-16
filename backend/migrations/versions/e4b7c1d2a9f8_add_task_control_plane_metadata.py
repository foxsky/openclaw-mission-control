"""add task control-plane metadata

Revision ID: e4b7c1d2a9f8
Revises: d1e2f3a4b5c6
Create Date: 2026-04-16 01:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e4b7c1d2a9f8"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("review_packet_type", sa.String(), nullable=True))
    op.add_column("tasks", sa.Column("validation_target", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("validation_target_kind", sa.String(), nullable=True))
    op.add_column("tasks", sa.Column("validation_target_scope", sa.String(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column(
            "operator_decision_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("tasks", sa.Column("operator_decision_summary", sa.Text(), nullable=True))
    op.create_index(
        op.f("ix_tasks_operator_decision_required"),
        "tasks",
        ["operator_decision_required"],
        unique=False,
    )
    op.alter_column("tasks", "operator_decision_required", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_operator_decision_required"), table_name="tasks")
    op.drop_column("tasks", "operator_decision_summary")
    op.drop_column("tasks", "operator_decision_required")
    op.drop_column("tasks", "validation_target_scope")
    op.drop_column("tasks", "validation_target_kind")
    op.drop_column("tasks", "validation_target")
    op.drop_column("tasks", "review_packet_type")
