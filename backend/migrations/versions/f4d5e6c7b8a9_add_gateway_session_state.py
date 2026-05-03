"""add gateway_session_state projection table

Slice 4 of the gateway-event-subscriber project. Persists the projector
output from ``mc_gateway_subscriber.session_state_projector`` so MC
API endpoints (e.g. /agent/next-action lead signals) can read
real-time session activity without coupling to the worker process.

agent_id is the raw gateway sessionKey identifier (``mc-<uuid>``,
``lead-<uuid>``, ``mc-gateway-<uuid>``) — NOT a foreign key to
``agents.id`` because (a) the gateway emits state for its own internal
agent (mc-gateway-*) which has no MC row, and (b) the projector must
be able to write before the agents row exists during provisioning
races.

Composite PK on (agent_id, session_label) is the natural identity:
one agent has finitely many session buckets ('main', occasional
'debug'), and the gateway emits sessionKey-keyed updates that are
intrinsically per-bucket.

Revision ID: f4d5e6c7b8a9
Revises: f3c4d5e6a7b8
Create Date: 2026-05-03 14:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f4d5e6c7b8a9"
down_revision = "f3c4d5e6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_session_state",
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("session_label", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("last_phase", sa.String(), nullable=True),
        sa.Column("last_message_seq", sa.Integer(), nullable=True),
        sa.Column("last_changed_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column(
            "aborted_last_run",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "agent_id",
            "session_label",
            name="pk_gateway_session_state",
        ),
    )
    op.create_index(
        "ix_gateway_session_state_last_changed_at_ms",
        "gateway_session_state",
        ["last_changed_at_ms"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gateway_session_state_last_changed_at_ms",
        table_name="gateway_session_state",
    )
    op.drop_table("gateway_session_state")
