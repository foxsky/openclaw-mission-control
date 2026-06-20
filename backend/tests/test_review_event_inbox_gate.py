# ruff: noqa
"""Deterministic gate: a reviewer verdict (review-event) may not be recorded on
an ``inbox`` task.

`inbox` is the lead's unrouted backlog, not the review queue — there is nothing
to review on a task that has not been worked/submitted. The QA/Architect skills
say "verify status is review, else stop", but agents do not reliably obey
prompts (a QA-E2E agent looped INCONCLUSIVE on an inbox task). So the backend
rejects the verdict at the data layer.

Scope check: review-events on ``rework`` ARE legitimate (re-fail on a returned
task — see ``test_record_task_review_event_fail_does_not_transition_when_status_not_review``),
so the gate targets ``inbox`` specifically, not "non-review".
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

import app.api.tasks as tasks_api
from app.models.agents import Agent
from app.models.boards import Board
from app.models.gateways import Gateway
from app.models.organizations import Organization
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


@pytest.mark.asyncio
async def test_review_event_rejected_on_inbox_task(
    sqlite_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, reviewer = await _setup(sqlite_session, status="inbox")
    monkeypatch.setattr(tasks_api, "GatewayDispatchService", _NoopDispatch)

    with pytest.raises(HTTPException) as exc:
        await tasks_api.record_task_review_event(
            payload=TaskReviewEventCreate(
                reviewer_role="architect",
                verdict="pass",
                evidence_type="source_review",
                evidence={"comment": "verdict on an inbox task"},
            ),
            task=task,
            session=sqlite_session,
            actor=_ActorStub(agent=reviewer),
        )
    assert exc.value.status_code == 409
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("code") == "review_event_task_in_inbox"


@pytest.mark.asyncio
async def test_review_event_allowed_on_rework_task(
    sqlite_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the inbox gate must NOT block legitimate rework verdicts."""
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
