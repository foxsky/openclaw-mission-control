"""Activity event model persisted for audit and feed use-cases."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

RUNTIME_ANNOTATION_TYPES = (datetime,)


class ActivityEvent(QueryModel, table=True):
    """Discrete activity event tied to board/task/agent context."""

    __tablename__ = "activity_events"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    event_type: str = Field(index=True)
    message: str | None = None
    agent_id: UUID | None = Field(default=None, foreign_key="agents.id", index=True)
    actor_user_id: UUID | None = Field(default=None, foreign_key="users.id", index=True)
    task_id: UUID | None = Field(default=None, foreign_key="tasks.id", index=True)
    board_id: UUID | None = Field(default=None, foreign_key="boards.id", index=True)
    # Phase I: denormalized classifier flags from the shadow_metrics
    # emitter. Populated at write time for comment events; None for
    # historical rows and non-comment event types. Lets GET /comments
    # filter flagged rows without joining to shadow_metric_events.
    # none_as_null=True so Python ``None`` persists as SQL NULL rather
    # than the JSON literal ``null``. Without this, ``IS NULL`` never
    # matches skipped/crashed classifier runs and filter predicates
    # would treat them as flagged in default_hidden/hidden_strict modes.
    classifier_flags: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON(none_as_null=True), nullable=True),
    )
    created_at: datetime = Field(default_factory=utcnow)
