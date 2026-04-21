"""phase III: drop redundant ix_operator_decisions_status

Revision ID: b10ca1ab1e04
Revises: b10ca1ab1e03
Create Date: 2026-04-21 20:30:00.000000

Every query in services/api filters by ``board_id AND status``, so the
board-scoped partial index ``ix_operator_decisions_board_id_pending``
covers the hot path. The plain ``ix_operator_decisions_status`` full
index is redundant write-amplification with no read benefit.

Lives in a separate revision (not an in-place edit of
``b10ca1ab1e03``) so any environment that stamped the original Phase
III revision picks up the drop on the next ``alembic upgrade head``.
"""

from __future__ import annotations

from alembic import op


revision = "b10ca1ab1e04"
down_revision = "b10ca1ab1e03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_operator_decisions_status", table_name="operator_decisions"
    )


def downgrade() -> None:
    op.create_index(
        "ix_operator_decisions_status",
        "operator_decisions",
        ["status"],
    )
