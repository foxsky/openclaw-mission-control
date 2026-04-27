"""Add task source memory linkage.

Revision ID: ea1b2c3d4f5a
Revises: c9d0e1f2a3b4
Create Date: 2026-04-26 22:25:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ea1b2c3d4f5a"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("source_memory_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_tasks_source_memory_id_board_memory",
        "tasks",
        "board_memory",
        ["source_memory_id"],
        ["id"],
    )
    op.create_index("ix_tasks_source_memory_id", "tasks", ["source_memory_id"])
    op.create_unique_constraint(
        "uq_tasks_source_memory_id",
        "tasks",
        ["source_memory_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_tasks_source_memory_id", "tasks", type_="unique")
    op.drop_index("ix_tasks_source_memory_id", table_name="tasks")
    op.drop_constraint("fk_tasks_source_memory_id_board_memory", "tasks", type_="foreignkey")
    op.drop_column("tasks", "source_memory_id")
