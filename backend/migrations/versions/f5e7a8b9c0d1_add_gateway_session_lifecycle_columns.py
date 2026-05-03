"""add lifecycle columns to gateway_session_state for slice 6

Slice 6 of the gateway-event-subscriber project. Live capture of
``sessions.changed`` events from .60 surfaced three fields the
slice-4 projector silently dropped:

* ``parentSessionKey`` — set when a session is an ACP child spawned
  by another agent. Indexed because the lead next-action surface
  filters on it.
* ``status`` — per-run state ("running" | "done" | ...).
* ``reason`` — gateway lifecycle vocabulary ("create" | "completed"
  | "abort" | "expiry" | "spawn-failed" | "deleted" | "retry-limit"
  | "subagent-status" | "reset" | "patch").

Together these let MC derive ACP-child completion via SQL
(``parent_session_key=X AND last_lifecycle_reason IN
('completed','abort','expiry','spawn-failed','deleted','retry-limit')``)
instead of inferring from session-jsonl mtimes.

Additive, nullable, no backfill — historical rows simply lack the
data until the projector observes their next ``sessions.changed``
event. The lead next-action gate is gated on slice 5 already, so
NULL fallback is acceptable while the projector catches up.

Revision ID: f5e7a8b9c0d1
Revises: f4d5e6c7b8a9
Create Date: 2026-05-03 23:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f5e7a8b9c0d1"
down_revision = "f4d5e6c7b8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gateway_session_state",
        sa.Column("parent_session_key", sa.String(), nullable=True),
    )
    op.add_column(
        "gateway_session_state",
        sa.Column("last_status", sa.String(), nullable=True),
    )
    op.add_column(
        "gateway_session_state",
        sa.Column("last_lifecycle_reason", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_gateway_session_state_parent_session_key",
        "gateway_session_state",
        ["parent_session_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gateway_session_state_parent_session_key",
        table_name="gateway_session_state",
    )
    op.drop_column("gateway_session_state", "last_lifecycle_reason")
    op.drop_column("gateway_session_state", "last_status")
    op.drop_column("gateway_session_state", "parent_session_key")
