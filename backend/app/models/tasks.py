"""Task model representing board work items and execution metadata."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field

from app.core.time import utcnow
from app.models.tenancy import TenantScoped

RUNTIME_ANNOTATION_TYPES = (datetime,)


class Task(TenantScoped, table=True):
    """Board-scoped task entity with ownership, status, and timing fields."""

    __tablename__ = "tasks"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    board_id: UUID | None = Field(default=None, foreign_key="boards.id", index=True)

    title: str
    description: str | None = None
    status: str = Field(default="inbox", index=True)
    priority: str = Field(default="medium", index=True)
    due_at: datetime | None = None
    in_progress_at: datetime | None = None
    previous_in_progress_at: datetime | None = None
    cancelled_at: datetime | None = None
    review_packet_type: str | None = None
    validation_target: str | None = None
    validation_target_kind: str | None = None
    validation_target_scope: str | None = None
    operator_decision_required: bool = Field(default=False, index=True)
    operator_decision_summary: str | None = None

    created_by_user_id: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        index=True,
    )
    assigned_agent_id: UUID | None = Field(
        default=None,
        foreign_key="agents.id",
        index=True,
    )
    auto_created: bool = Field(default=False)
    auto_reason: str | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
