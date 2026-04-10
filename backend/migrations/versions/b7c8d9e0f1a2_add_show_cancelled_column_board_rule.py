"""add show-cancelled-column board rule

Revision ID: b7c8d9e0f1a2
Revises: a9b1c2d3e4f7
Create Date: 2026-04-09 22:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b7c8d9e0f1a2"
down_revision = "a9b1c2d3e4f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    board_columns = {column["name"] for column in inspector.get_columns("boards")}
    if "show_cancelled_column" not in board_columns:
        op.add_column(
            "boards",
            sa.Column(
                "show_cancelled_column",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    board_columns = {column["name"] for column in inspector.get_columns("boards")}
    if "show_cancelled_column" in board_columns:
        op.drop_column("boards", "show_cancelled_column")
