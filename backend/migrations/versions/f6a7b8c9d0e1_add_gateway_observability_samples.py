"""add gateway_observability_samples table

Slice 1 of the gateway observability poller. Stores raw Prometheus
counter values + computed rate-per-second deltas so MC can surface
model_call/harness_run/run_completed failure rates without running
a full Prometheus stack.

One row per ``(gateway_id, scraped_at, metric_name, labels)`` —
``labels`` is JSON, sortable when needed by querying. We intentionally
filter at write time to the three error metrics that actually fire
under our Codex-stdio fleet (model_call_total{outcome=error},
harness_run_total{outcome=error}, run_completed_total{state=failed});
``model_failover_total`` is silent on Codex harness 404 aborts and is
not stored.

Revision ID: f6a7b8c9d0e1
Revises: f5e7a8b9c0d1
Create Date: 2026-05-28 17:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "f5e7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_observability_samples",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gateway_id", sa.UUID(), nullable=False),
        # Naive UTC to match project convention (``app.core.time.utcnow``
        # returns naive). ``server_default=now()`` is naive on SQLite and
        # ``timestamp without time zone`` on Postgres, so the wire shape
        # is consistent across both backends used by tests vs prod.
        sa.Column(
            "scraped_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("metric_name", sa.String(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("counter_value", sa.Float(), nullable=False),
        sa.Column("rate_per_second", sa.Float(), nullable=True),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_gateway_observability_samples"),
        sa.ForeignKeyConstraint(
            ["gateway_id"],
            ["gateways.id"],
            name="fk_gateway_observability_samples_gateway_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_gateway_observability_samples_gateway_id",
        "gateway_observability_samples",
        ["gateway_id"],
        unique=False,
    )
    op.create_index(
        "ix_gateway_observability_samples_scraped_at",
        "gateway_observability_samples",
        ["scraped_at"],
        unique=False,
    )
    op.create_index(
        "ix_gateway_observability_samples_metric_name",
        "gateway_observability_samples",
        ["metric_name"],
        unique=False,
    )
    # Composite index for window queries:
    # ``WHERE gateway_id=? AND metric_name=? AND scraped_at > ?``
    op.create_index(
        "ix_gateway_observability_samples_window",
        "gateway_observability_samples",
        ["gateway_id", "metric_name", "scraped_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gateway_observability_samples_window",
        table_name="gateway_observability_samples",
    )
    op.drop_index(
        "ix_gateway_observability_samples_metric_name",
        table_name="gateway_observability_samples",
    )
    op.drop_index(
        "ix_gateway_observability_samples_scraped_at",
        table_name="gateway_observability_samples",
    )
    op.drop_index(
        "ix_gateway_observability_samples_gateway_id",
        table_name="gateway_observability_samples",
    )
    op.drop_table("gateway_observability_samples")
