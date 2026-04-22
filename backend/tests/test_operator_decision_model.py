# ruff: noqa: INP001
"""Unit tests for the Phase III OperatorDecision model (plan §I3).

Scope: the column contract + constraint guards. Compatibility bridge
(is_blocked derivation ORing in open decisions) + endpoints land in
follow-up commits.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operator_decisions import (
    OperatorDecision,
    OperatorDecisionTaskLink,
)


def _decision(**overrides: object) -> OperatorDecision:
    defaults: dict[str, object] = {
        "board_id": uuid4(),
        "question": "Should the rollout continue?",
    }
    defaults.update(overrides)
    return OperatorDecision(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_canonical_statuses_round_trip(sqlite_session: AsyncSession) -> None:
    for status in ("pending", "resolved", "cancelled"):
        sqlite_session.add(_decision(status=status))
    await sqlite_session.commit()


@pytest.mark.asyncio
async def test_unknown_status_rejected(sqlite_session: AsyncSession) -> None:
    sqlite_session.add(_decision(status="approved"))
    with pytest.raises(IntegrityError):
        await sqlite_session.commit()


@pytest.mark.asyncio
async def test_default_status_is_pending(sqlite_session: AsyncSession) -> None:
    decision = _decision()
    sqlite_session.add(decision)
    await sqlite_session.commit()
    assert decision.status == "pending"


@pytest.mark.asyncio
async def test_task_link_unique_per_decision_task_pair(
    sqlite_session: AsyncSession,
) -> None:
    """A decision cannot link the same task twice — duplicates would
    inflate the bridge's 'does any decision block this task?' count."""

    decision = _decision()
    sqlite_session.add(decision)
    await sqlite_session.commit()
    task_id = uuid4()
    sqlite_session.add(
        OperatorDecisionTaskLink(decision_id=decision.id, task_id=task_id),
    )
    await sqlite_session.commit()
    sqlite_session.add(
        OperatorDecisionTaskLink(decision_id=decision.id, task_id=task_id),
    )
    with pytest.raises(IntegrityError):
        await sqlite_session.commit()
