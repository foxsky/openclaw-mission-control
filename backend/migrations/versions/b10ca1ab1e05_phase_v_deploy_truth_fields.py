"""phase V: packet SHA + build SHA + target capability columns on tasks

Revision ID: b10ca1ab1e05
Revises: b10ca1ab1e04
Create Date: 2026-04-21 21:00:00.000000

Adds the three columns Phase V §I8 needs to implement capability-based
deploy truth:

- ``tasks.packet_commit_sha`` — the source commit SHA the reviewer is
  claiming is (or was) deployed. Null pre-Phase-V.
- ``tasks.packet_build_sha`` — downstream artefact tag where the
  target produces one distinct from the source commit. Optional; null
  when the target produces no build SHA or operators don't track it.
- ``tasks.supports_build_metadata`` — capability flag for the target.
  True: target exposes ``GET /__build``, SHA comparison is mandatory
  when the task reaches ``review``/``done``. False: target is
  deploy-blind, validation runs in degraded mode (operator-visible,
  burn-down required). Null: operators haven't classified the target
  yet — treat as degraded but keep the null so dashboards can count
  un-classified targets separately from explicitly-blind ones.

Nullable by design so the migration lands without a data-migration
pass; the enforcement gate (next commit) only trips on
``supports_build_metadata=True`` tasks anyway.

See docs/plans/2026-04-16-mc-delivery-enforcement-plan.md §I8.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "b10ca1ab1e05"
down_revision = "b10ca1ab1e04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("packet_commit_sha", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("packet_build_sha", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("supports_build_metadata", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "supports_build_metadata")
    op.drop_column("tasks", "packet_build_sha")
    op.drop_column("tasks", "packet_commit_sha")
