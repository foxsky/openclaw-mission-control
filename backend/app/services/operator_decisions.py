"""Service helpers for Phase III operator-decision-aware task queries (plan §I3).

The ``is_blocked`` derivation ORs a third entity source into the task
state: any pending ``OperatorDecision`` linked to the task blocks it,
even when the legacy ``Task.operator_decision_required`` flag is
False. That preserves the compatibility rule (legacy flag still
works) while letting the first-class entity gradually become source
of truth.

Shape mirrors ``app/services/blockers.py``: a batch preloader for the
list/stream hot paths and a scalar EXISTS for the single-task
response path.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import exists
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.operator_decisions import (
    OperatorDecision,
    OperatorDecisionTaskLink,
)


async def task_ids_with_pending_operator_decision(
    session: AsyncSession,
    *,
    board_id: UUID,
    task_ids: Iterable[UUID],
) -> set[UUID]:
    """Return the subset of task ids linked to a pending decision.

    Joins ``operator_decisions`` with its task-link sidecar under the
    board-id tenant scope, filters on ``status = 'pending'`` so the
    partial ``ix_operator_decisions_board_id_pending`` index drives
    the scan.
    """

    task_id_list = list(task_ids)
    if not task_id_list:
        return set()
    stmt = (
        select(col(OperatorDecisionTaskLink.task_id))
        .join(
            OperatorDecision,
            col(OperatorDecisionTaskLink.decision_id) == col(OperatorDecision.id),
        )
        .where(col(OperatorDecision.board_id) == board_id)
        .where(col(OperatorDecision.status) == "pending")
        .where(col(OperatorDecisionTaskLink.task_id).in_(task_id_list))
    )
    return set((await session.exec(stmt)).all())


async def task_has_pending_operator_decision(
    session: AsyncSession, *, board_id: UUID, task_id: UUID
) -> bool:
    """Single-task EXISTS — for the PATCH response path."""

    stmt = select(
        exists()
        .where(
            col(OperatorDecisionTaskLink.decision_id) == col(OperatorDecision.id)
        )
        .where(col(OperatorDecision.board_id) == board_id)
        .where(col(OperatorDecision.status) == "pending")
        .where(col(OperatorDecisionTaskLink.task_id) == task_id)
    )
    result = await session.exec(stmt)
    return bool(result.first())
