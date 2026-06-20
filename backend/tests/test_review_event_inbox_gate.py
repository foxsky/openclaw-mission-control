# ruff: noqa
"""Deterministic gate: a reviewer verdict (review-event) is only valid on a task
that is in the review flow — ``review`` or ``rework``.

`inbox` is the lead's unrouted backlog (or an assigned-but-dependency-blocked
task), and `in_progress`/`done`/`cancelled` have no meaningful verdict either —
recording one still commits the event and fires its FAIL/PASS/lead wakes. The
QA/Architect skills say "verify status is review, else stop", but agents do not
reliably obey prompts (a QA-E2E agent looped INCONCLUSIVE on an inbox task), so
the backend enforces the allowlist at write time.

Scope: ``rework`` re-verdicts ARE legitimate (re-fail on a returned task — see
``test_record_task_review_event_fail_does_not_transition_when_status_not_review``),
so the gate allows ``review`` + ``rework`` and rejects everything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import app.api.tasks as tasks_api
from app.models.agents import Agent
from app.models.boards import Board
from app.models.gateways import Gateway
from app.models.organizations import Organization
from app.models.task_review_events import TaskReviewEvent
from app.models.tasks import Task
from app.schemas.task_review_events import TaskReviewEventCreate
from app.services.openclaw.gateway_rpc import GatewayConfig


@dataclass
class _ActorStub:
    agent: Agent | None
    actor_type: str = "agent"
    user: object | None = None


class _NoopDispatch:
    def __init__(self, session) -> None:
        self.session = session

    async def optional_gateway_config_for_board(self, board):
        return GatewayConfig(url="ws://gateway.example/ws")

    async def try_send_agent_message(self, **kwargs):
        return None


async def _setup(sqlite_session: AsyncSession, *, status: str) -> tuple[Task, Agent]:
    org_id, gateway_id, board_id = uuid4(), uuid4(), uuid4()
    sqlite_session.add(Organization(id=org_id, name=f"org-{org_id}"))
    sqlite_session.add(
        Gateway(
            id=gateway_id,
            organization_id=org_id,
            name="gateway",
            url="ws://gateway.example/ws",
            workspace_root="/tmp/openclaw",
        ),
    )
    sqlite_session.add(
        Board(
            id=board_id,
            organization_id=org_id,
            gateway_id=gateway_id,
            name="board",
            slug="board",
        ),
    )
    reviewer = Agent(
        id=uuid4(),
        board_id=board_id,
        gateway_id=gateway_id,
        name="Architect",
        openclaw_session_id="agent:architect:main",
        identity_profile={"role": "System Architect and Code Reviewer"},
    )
    sqlite_session.add(reviewer)
    task = Task(id=uuid4(), board_id=board_id, title="t", status=status)
    sqlite_session.add(task)
    await sqlite_session.commit()
    await sqlite_session.refresh(task)
    return task, reviewer


async def _count_events(sqlite_session: AsyncSession, task_id) -> int:
    rows = (
        await sqlite_session.exec(
            select(TaskReviewEvent).where(TaskReviewEvent.task_id == task_id),
        )
    ).all()
    return len(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["inbox", "in_progress", "done", "cancelled"])
async def test_review_event_rejected_off_review_flow(
    sqlite_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    task, reviewer = await _setup(sqlite_session, status=status)
    monkeypatch.setattr(tasks_api, "GatewayDispatchService", _NoopDispatch)

    with pytest.raises(HTTPException) as exc:
        await tasks_api.record_task_review_event(
            payload=TaskReviewEventCreate(
                reviewer_role="architect",
                verdict="pass",
                evidence_type="source_review",
                evidence={"comment": f"verdict on a {status} task"},
            ),
            task=task,
            session=sqlite_session,
            actor=_ActorStub(agent=reviewer),
        )
    assert exc.value.status_code == 409
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("code") == "review_event_task_not_in_review"
    # No event row may persist when the verdict is rejected.
    assert await _count_events(sqlite_session, task.id) == 0


@pytest.mark.asyncio
async def test_review_event_allowed_on_rework_task(
    sqlite_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the gate must NOT block legitimate rework re-verdicts."""
    task, reviewer = await _setup(sqlite_session, status="rework")
    monkeypatch.setattr(tasks_api, "GatewayDispatchService", _NoopDispatch)

    read = await tasks_api.record_task_review_event(
        payload=TaskReviewEventCreate(
            reviewer_role="architect",
            verdict="fail",
            evidence_type="source_review",
            evidence={"comment": "re-fail on a returned task"},
        ),
        task=task,
        session=sqlite_session,
        actor=_ActorStub(agent=reviewer),
    )
    assert read.verdict == "fail"


@pytest.mark.asyncio
async def test_wrong_role_gets_403_before_status_gate(
    sqlite_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth precedence: a wrong-role agent on an inbox task must get the 403
    role error, not the 409 status gate — the gate runs AFTER role checks."""
    task, reviewer = await _setup(sqlite_session, status="inbox")
    non_reviewer = Agent(
        id=uuid4(),
        board_id=task.board_id,
        gateway_id=reviewer.gateway_id,
        name="Programmer-Frontend",
        openclaw_session_id="agent:pf:main",
        identity_profile={"role": "Programmer-Frontend"},
    )
    sqlite_session.add(non_reviewer)
    await sqlite_session.commit()
    monkeypatch.setattr(tasks_api, "GatewayDispatchService", _NoopDispatch)

    with pytest.raises(HTTPException) as exc:
        await tasks_api.record_task_review_event(
            payload=TaskReviewEventCreate(
                reviewer_role="architect",
                verdict="pass",
                evidence_type="source_review",
                evidence={"comment": "wrong role on inbox"},
            ),
            task=task,
            session=sqlite_session,
            actor=_ActorStub(agent=non_reviewer),
        )
    assert exc.value.status_code == 403
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("code") == "reviewer_role_not_allowed"
