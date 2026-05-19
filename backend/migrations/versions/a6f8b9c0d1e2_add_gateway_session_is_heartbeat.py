"""add is_heartbeat to gateway_session_state for OpenClaw 5.14 #80610

OpenClaw 5.14 (#80610) introduced an optional ``isHeartbeat`` field on
agent event payloads so clients can distinguish scheduled heartbeat
ticks from chat-driven runs. The session-state projector captures it
when present; older gateways simply don't stamp the field and the
column stays NULL.

Indexed because the lead next-action surface uses this signal to
separate "agent silent on chat" (likely wedged — wake) from "agent
silent on heartbeat tick" (probably fine — no-op).

Additive, nullable, no backfill — historical rows lack the data until
the projector observes their next ``sessions.changed`` event with the
field set. Older OpenClaw deployments (pre-5.14) leave the column NULL
indefinitely; the read path treats NULL as "unknown" and falls back to
the 4-segment ``main:heartbeat`` sub-label heuristic.

Revision ID: a6f8b9c0d1e2
Revises: f5e7a8b9c0d1
Create Date: 2026-05-19 13:30:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a6f8b9c0d1e2"
down_revision = "f5e7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gateway_session_state",
        sa.Column("is_heartbeat", sa.Boolean(), nullable=True),
    )
    op.create_index(
        "ix_gateway_session_state_is_heartbeat",
        "gateway_session_state",
        ["is_heartbeat"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gateway_session_state_is_heartbeat",
        table_name="gateway_session_state",
    )
    op.drop_column("gateway_session_state", "is_heartbeat")
