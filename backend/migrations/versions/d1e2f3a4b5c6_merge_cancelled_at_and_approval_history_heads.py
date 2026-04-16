"""merge cancelled_at and approval_history heads

Revision ID: d1e2f3a4b5c6
Revises: 0321a4760036, c8d9e0f1a2b3
Create Date: 2026-04-16 00:15:00.000000

Alembic merge of two sibling heads that both branched off
``a9b1c2d3e4f7_add_board_id_to_activity_events``:

- ``0321a4760036_add_cancelled_at_to_tasks`` (arrived via .64 snapshot
  during Phase B Step 3 sync)
- ``b7c8d9e0f1a2_add_show_cancelled_column_board_rule`` ->
  ``c8d9e0f1a2b3_add_approval_history`` (local-only chain committed
  before the .64 Phase B reconciliation)

Both branches make independent, non-overlapping schema changes
(tasks.cancelled_at column vs. approvals show_cancelled / approval_history
table), so a plain merge with no DDL is safe.
"""

from __future__ import annotations

from typing import Sequence, Union


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = ("0321a4760036", "c8d9e0f1a2b3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
