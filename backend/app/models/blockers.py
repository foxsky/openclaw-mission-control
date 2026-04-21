"""Blocker model — structured routing object for stuck work.

Phase II §I1: a task cannot be marked or treated as blocked purely by
free-text comment. Blockers are first-class records with category,
ownership, required artifact, and lifecycle columns so the Supervisor
can route from structured state rather than parsing prose.

Review-emitted blockers attach to a Review via ``review_blockers``
(Phase II §I4); ad-hoc blockers posted by the task owner or operator
stand alone. Both forms share this table.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint
from sqlmodel import Field

from app.core.time import utcnow
from app.models.tenancy import TenantScoped

RUNTIME_ANNOTATION_TYPES = (datetime,)

BLOCKER_CATEGORIES = ("source", "deploy", "runtime", "contract", "operator")


class Blocker(TenantScoped, table=True):
    """Structured blocker record attached to a task."""

    __tablename__ = "blockers"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        CheckConstraint(
            "category IN ('source', 'deploy', 'runtime', 'contract', 'operator')",
            name="ck_blockers_category_values",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    board_id: UUID = Field(foreign_key="boards.id", index=True)
    task_id: UUID = Field(foreign_key="tasks.id", index=True)
    category: str
    owner_role: str
    required_artifact: str | None = None
    target_env: str | None = None
    reopen_condition: str | None = None
    # Provenance — who filed the blocker. Null when the system fills
    # a retroactive row (e.g. migration of legacy free-text blockers).
    created_by_agent_id: UUID | None = Field(
        default=None, foreign_key="agents.id", index=True
    )
    created_at: datetime = Field(default_factory=utcnow)
    # Acknowledgement signals the receiving owner has seen and accepted
    # the blocker. Lane quieting (Phase VI §I6) keys off this.
    acknowledged_at: datetime | None = None
    acknowledged_by_agent_id: UUID | None = Field(
        default=None, foreign_key="agents.id", index=True
    )
    # Resolution closes the blocker. While open, the task's is_blocked
    # derivation treats this row as active.
    resolved_at: datetime | None = None
    # Allows filing a sharper restatement of a prior blocker without
    # losing the audit trail. The newer row supersedes the prior row;
    # the prior row should be closed in the same transaction.
    supersedes_blocker_id: UUID | None = Field(
        default=None, foreign_key="blockers.id", index=True
    )
