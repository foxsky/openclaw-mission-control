"""Append-only history of approval lifecycle events.

Each row records a single transition in an approval's lifecycle. The
``Approval`` table itself stores only the current state (one mutable row
per approval) and so cannot answer questions like "how many times was
this task rejected" — reopening a rejected approval overwrites the row.
``ApprovalHistory`` is the append-only event log the rejection-loop
guard relies on.

Event types:
- ``submitted``  — a worker created or re-opened an approval request
- ``rejected``   — reviewer rejected the approval
- ``approved``   — reviewer or operator approved
- ``unblocked``  — operator/board-lead explicitly cleared the rejection
                   loop via ``POST .../approvals/{id}/unblock``

Actor identity is split between ``actor_user_id`` (humans, via the
LOCAL_AUTH_TOKEN or org membership) and ``actor_agent_id`` (board agents
authenticated by agent token). Exactly one of them is non-null; the
``actor_type`` column makes that explicit so a reader does not have to
check both columns.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

RUNTIME_ANNOTATION_TYPES = (datetime,)


class ApprovalHistory(QueryModel, table=True):
    """Append-only lifecycle event for an approval."""

    __tablename__ = "approval_history"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    approval_id: UUID = Field(foreign_key="approvals.id", index=True)
    board_id: UUID = Field(foreign_key="boards.id", index=True)
    task_id: UUID | None = Field(default=None, foreign_key="tasks.id", index=True)
    event_type: str = Field(index=True)
    actor_type: str  # "user" | "agent" | "system"
    actor_user_id: UUID | None = Field(default=None, foreign_key="users.id", index=True)
    actor_agent_id: UUID | None = Field(default=None, foreign_key="agents.id", index=True)
    message: str | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
