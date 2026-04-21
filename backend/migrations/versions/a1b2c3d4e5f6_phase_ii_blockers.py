"""phase II: structured blocker sidecar table

Revision ID: a1b2c3d4e5f6
Revises: e5f6a7b8c9d0
Create Date: 2026-04-21 16:00:00.000000

Adds the ``blockers`` table — Phase II §I1's first-class routing
object. Tasks flagged as blocked must now carry at least one open
Blocker row; free-text "blocked on ..." comments become a protocol
error in Phase VI once lane quieting is wired up. The table ships
here so API + enforcement PRs can land incrementally.

See docs/plans/2026-04-16-mc-delivery-enforcement-plan.md §I1 and
§"Phase II — Blocker and review sidecars".
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blockers",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "board_id",
            sa.Uuid(),
            sa.ForeignKey("boards.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("owner_role", sa.String(length=64), nullable=False),
        sa.Column("required_artifact", sa.Text(), nullable=True),
        sa.Column("target_env", sa.String(length=64), nullable=True),
        sa.Column("reopen_condition", sa.Text(), nullable=True),
        sa.Column(
            "created_by_agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "acknowledged_by_agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "supersedes_blocker_id",
            sa.Uuid(),
            sa.ForeignKey("blockers.id"),
            nullable=True,
            index=True,
        ),
    )
    # Defense-in-depth: Pydantic Literal will validate API writes, but
    # raw SQL and fixtures bypass that layer.
    op.create_check_constraint(
        "ck_blockers_category_values",
        "blockers",
        "category IN ('source', 'deploy', 'runtime', 'contract', 'operator')",
    )
    # Fast "does this task have any open blocker?" lookup powers
    # is_blocked derivation in the next commit.
    op.create_index(
        "ix_blockers_task_id_open",
        "blockers",
        ["task_id"],
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_blockers_task_id_open", table_name="blockers")
    op.drop_constraint("ck_blockers_category_values", "blockers", type_="check")
    op.drop_table("blockers")
