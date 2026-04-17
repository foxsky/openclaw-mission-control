"""add shadow_metric_events table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-17 15:30:00.000000

Append-only observability table for Phase 0 shadow classifiers and
gate instrumentation. Each row captures a signal (ack-only candidate,
near-duplicate candidate, actionability violation) without changing
caller-visible behavior.

See ``docs/plans/2026-04-16-mc-delivery-enforcement-plan.md`` §Phase 0
and amendments §A.2, §A.4, §A.5.

Retention: 90-day cutoff via downstream purge job (amendment §A.4).
The ``created_at`` index makes that purge query efficient without
needing a dedicated time-range scan plan.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add a composite index on activity_events to make the classifier's
    # "prior comment by same author on same task within window" lookup
    # cheap. Without this the query was the dominant per-comment cost
    # flagged in efficiency review.
    op.create_index(
        op.f("ix_activity_events_task_agent_type_created"),
        "activity_events",
        ["task_id", "agent_id", "event_type", "created_at"],
        unique=False,
    )
    op.create_table(
        "shadow_metric_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "board_id",
            sa.Uuid(),
            sa.ForeignKey("boards.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_event_id", sa.Uuid(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        op.f("ix_shadow_metric_events_event_type"),
        "shadow_metric_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_shadow_metric_events_created_at"),
        "shadow_metric_events",
        ["created_at"],
        unique=False,
    )
    # Composite for "prior comment by same author on same task within window"
    # lookups from the classifier emitter — see services/shadow_metrics.py.
    # Also usable for operator queries filtering a task's metric history.
    op.create_index(
        op.f("ix_shadow_metric_events_task_agent_created"),
        "shadow_metric_events",
        ["task_id", "agent_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_shadow_metric_events_task_agent_created"),
        table_name="shadow_metric_events",
    )
    op.drop_index(
        op.f("ix_shadow_metric_events_created_at"),
        table_name="shadow_metric_events",
    )
    op.drop_index(
        op.f("ix_shadow_metric_events_event_type"),
        table_name="shadow_metric_events",
    )
    op.drop_table("shadow_metric_events")
    op.drop_index(
        op.f("ix_activity_events_task_agent_type_created"),
        table_name="activity_events",
    )
