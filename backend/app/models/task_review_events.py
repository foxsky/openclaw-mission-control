"""Structured review verdict events for task readiness gates."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

RUNTIME_ANNOTATION_TYPES = (datetime,)


class TaskReviewEvent(QueryModel, table=True):
    """Append-only reviewer verdict for one task review cycle."""

    __tablename__ = "task_review_events"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    board_id: UUID = Field(foreign_key="boards.id", index=True)
    task_id: UUID = Field(foreign_key="tasks.id", index=True)
    agent_id: UUID | None = Field(default=None, foreign_key="agents.id", index=True)
    reviewer_role: str = Field(index=True)
    verdict: str = Field(index=True)
    evidence_type: str | None = Field(default=None, index=True)
    target: str | None = None
    build_hash: str | None = Field(default=None, index=True)
    source_commit: str | None = Field(default=None, index=True)
    blocking_owner: str | None = Field(default=None, index=True)
    suggested_routing: str | None = None
    evidence: dict[str, object] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)
