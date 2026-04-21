"""Phase III OperatorDecision endpoints (plan §I3).

Board-scoped CRUD for first-class operator decisions. Unlike blockers
(per-task) or reviews (per-task), a decision is board-scoped and can
link to multiple tasks. The compatibility bridge that ORs pending
decisions into ``is_blocked`` lands in a follow-up commit.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import col, select

from app.api.deps import (
    ACTOR_DEP,
    SESSION_DEP,
    ActorContext,
    get_board_for_actor_read,
    get_board_for_actor_write,
)
from app.core.time import utcnow
from app.db.pagination import paginate
from app.models.operator_decisions import (
    OperatorDecision,
    OperatorDecisionTaskLink,
)
from app.schemas.operator_decisions import (
    OperatorDecisionCreate,
    OperatorDecisionRead,
    OperatorDecisionStatus,
    OperatorDecisionUpdate,
)
from app.schemas.pagination import DefaultLimitOffsetPage

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from fastapi_pagination.limit_offset import LimitOffsetPage
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.models.boards import Board

router = APIRouter(
    prefix="/boards/{board_id}/operator_decisions", tags=["operator_decisions"]
)

BOARD_READ_DEP = Depends(get_board_for_actor_read)
BOARD_WRITE_DEP = Depends(get_board_for_actor_write)

STATUS_FILTER_QUERY: OperatorDecisionStatus | None = Query(
    default=None, alias="status"
)


async def _task_ids_by_decision(
    session: "AsyncSession", decision_ids: "Iterable[UUID]"
) -> dict[UUID, list[UUID]]:
    """Batch-preload the task id list per decision — the hot query
    for list-endpoint hydration. One SELECT instead of N."""

    ids = list(decision_ids)
    if not ids:
        return {}
    stmt = (
        select(
            col(OperatorDecisionTaskLink.decision_id),
            col(OperatorDecisionTaskLink.task_id),
        )
        .where(col(OperatorDecisionTaskLink.decision_id).in_(ids))
        .order_by(col(OperatorDecisionTaskLink.created_at).asc())
    )
    grouped: dict[UUID, list[UUID]] = defaultdict(list)
    for decision_id, task_id in (await session.exec(stmt)).all():
        grouped[decision_id].append(task_id)
    return grouped


def _decision_read(
    decision: OperatorDecision, task_ids: list[UUID]
) -> OperatorDecisionRead:
    return OperatorDecisionRead(
        id=decision.id,
        board_id=decision.board_id,
        question=decision.question,
        owner_user_id=decision.owner_user_id,
        unblock_rule=decision.unblock_rule,
        status=decision.status,  # type: ignore[arg-type]
        resolved_value=decision.resolved_value,
        created_by_agent_id=decision.created_by_agent_id,
        created_at=decision.created_at,
        resolved_at=decision.resolved_at,
        dependent_task_ids=task_ids,
    )


async def _load_decision(
    session: "AsyncSession", *, board_id: UUID, decision_id: UUID
) -> OperatorDecision:
    """Load a decision scoped to the board, or raise 404."""

    decision = await OperatorDecision.objects.filter_by(
        id=decision_id, board_id=board_id
    ).first(session)
    if decision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return decision


@router.get("", response_model=DefaultLimitOffsetPage[OperatorDecisionRead])
async def list_board_operator_decisions(
    board: "Board" = BOARD_READ_DEP,
    session: "AsyncSession" = SESSION_DEP,
    _actor: ActorContext = ACTOR_DEP,
    status_filter: OperatorDecisionStatus | None = STATUS_FILTER_QUERY,
) -> "LimitOffsetPage[OperatorDecisionRead]":
    """List operator decisions on the board, newest first.

    Optional ``status=pending|resolved|cancelled`` filter narrows to
    the inbox lane the operator cares about.
    """

    async def _transform(
        rows: "Sequence[object]",
    ) -> list[OperatorDecisionRead]:
        decisions: list[OperatorDecision] = []
        for row in rows:
            if not isinstance(row, OperatorDecision):
                msg = "Expected OperatorDecision rows from pagination query."
                raise TypeError(msg)
            decisions.append(row)
        tasks_by_decision = await _task_ids_by_decision(
            session, (d.id for d in decisions)
        )
        return [
            _decision_read(decision, tasks_by_decision.get(decision.id, []))
            for decision in decisions
        ]

    statement = OperatorDecision.objects.filter_by(board_id=board.id)
    if status_filter is not None:
        statement = statement.filter(
            col(OperatorDecision.status) == status_filter
        )
    statement = statement.order_by(col(OperatorDecision.created_at).desc())
    return await paginate(session, statement.statement, transformer=_transform)


@router.post(
    "",
    response_model=OperatorDecisionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_operator_decision(
    payload: OperatorDecisionCreate,
    board: "Board" = BOARD_WRITE_DEP,
    session: "AsyncSession" = SESSION_DEP,
    actor: ActorContext = ACTOR_DEP,
) -> OperatorDecisionRead:
    """Escalate a new operator decision for the board.

    ``dependent_task_ids`` may be empty — the decision can exist in
    the inbox before the operator links it to specific tasks. The
    link management endpoint covers post-creation updates.
    """

    decision = OperatorDecision(
        board_id=board.id,
        question=payload.question,
        owner_user_id=payload.owner_user_id,
        unblock_rule=payload.unblock_rule,
        created_by_agent_id=actor.agent.id if actor.agent is not None else None,
    )
    session.add(decision)
    for task_id in payload.dependent_task_ids:
        session.add(
            OperatorDecisionTaskLink(
                decision_id=decision.id, task_id=task_id
            ),
        )
    await session.commit()
    return _decision_read(decision, list(payload.dependent_task_ids))


@router.patch(
    "/{decision_id}", response_model=OperatorDecisionRead
)
async def update_operator_decision(
    decision_id: UUID,
    payload: OperatorDecisionUpdate,
    board: "Board" = BOARD_WRITE_DEP,
    session: "AsyncSession" = SESSION_DEP,
    _actor: ActorContext = ACTOR_DEP,
) -> OperatorDecisionRead:
    """Resolve, cancel, or sharpen a pending operator decision."""

    decision = await _load_decision(
        session, board_id=board.id, decision_id=decision_id
    )

    if decision.status != "pending" and payload.status_transition is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot transition a {decision.status} decision",
        )
    if (
        decision.status != "pending"
        and payload.model_fields_set - {"status_transition"}
    ):
        # A closed row's content is audit material; rewriting
        # ``unblock_rule`` or ``resolved_value`` post-close would
        # silently alter history.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot update a {decision.status} decision",
        )

    mutated = False
    if payload.status_transition == "resolve":
        decision.status = "resolved"
        decision.resolved_at = utcnow()
        decision.resolved_value = payload.resolved_value
        mutated = True
    elif payload.status_transition == "cancel":
        decision.status = "cancelled"
        decision.resolved_at = utcnow()
        mutated = True
    elif "resolved_value" in payload.model_fields_set:
        # Allow sharpening the pre-resolve value while still pending —
        # e.g. the operator is drafting their answer.
        decision.resolved_value = payload.resolved_value
        mutated = True

    for field in ("owner_user_id", "unblock_rule"):
        if field in payload.model_fields_set:
            setattr(decision, field, getattr(payload, field))
            mutated = True

    if mutated:
        session.add(decision)
        await session.commit()

    task_ids_by_decision = await _task_ids_by_decision(session, [decision.id])
    return _decision_read(decision, task_ids_by_decision.get(decision.id, []))
