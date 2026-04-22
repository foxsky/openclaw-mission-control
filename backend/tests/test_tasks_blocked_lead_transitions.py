# ruff: noqa: INP001

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import ActorContext
from app.api.tasks import _apply_lead_task_update, _TaskUpdateInput
from app.models.agents import Agent
from app.models.boards import Board
from app.models.organizations import Organization
from app.models.task_dependencies import TaskDependency
from app.models.tasks import Task


@pytest.mark.asyncio
async def test_lead_update_rejects_assignment_change_when_task_blocked(
    sqlite_session: AsyncSession,
) -> None:
    org_id = uuid4()
    board_id = uuid4()
    lead_id = uuid4()
    worker_id = uuid4()
    dep_id = uuid4()
    task_id = uuid4()

    sqlite_session.add(Organization(id=org_id, name="org"))
    sqlite_session.add(Board(id=board_id, organization_id=org_id, name="b", slug="b"))
    sqlite_session.add(
        Agent(
            id=lead_id,
            name="Lead",
            board_id=board_id,
            gateway_id=uuid4(),
            is_board_lead=True,
            openclaw_session_id="agent:lead:session",
        ),
    )
    sqlite_session.add(
        Agent(
            id=worker_id,
            name="Worker",
            board_id=board_id,
            gateway_id=uuid4(),
            is_board_lead=False,
            openclaw_session_id="agent:worker:session",
        ),
    )
    sqlite_session.add(Task(id=dep_id, board_id=board_id, title="dep", description=None))
    sqlite_session.add(
        Task(
            id=task_id,
            board_id=board_id,
            title="t",
            description=None,
            status="review",
            assigned_agent_id=None,
        ),
    )
    sqlite_session.add(
        TaskDependency(
            board_id=board_id,
            task_id=task_id,
            depends_on_task_id=dep_id,
        ),
    )
    await sqlite_session.commit()

    lead = (await sqlite_session.exec(select(Agent).where(col(Agent.id) == lead_id))).first()
    task = (await sqlite_session.exec(select(Task).where(col(Task.id) == task_id))).first()
    assert lead is not None
    assert task is not None

    update = _TaskUpdateInput(
        task=task,
        actor=ActorContext(actor_type="agent", agent=lead),
        board_id=board_id,
        previous_status=task.status,
        previous_assigned=task.assigned_agent_id,
        status_requested=False,
        updates={"assigned_agent_id": worker_id},
        comment=None,
        depends_on_task_ids=None,
        tag_ids=None,
        custom_field_values={},
        custom_field_values_set=False,
    )

    with pytest.raises(HTTPException) as exc:
        await _apply_lead_task_update(sqlite_session, update=update)

    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "task_blocked_cannot_transition"
    assert detail["blocked_by_task_ids"] == [str(dep_id)]

    # DB unchanged
    reloaded = (await sqlite_session.exec(select(Task).where(col(Task.id) == task_id))).first()
    assert reloaded is not None
    assert reloaded.status == "review"
    assert reloaded.assigned_agent_id is None


@pytest.mark.asyncio
async def test_lead_update_rejects_status_change_when_task_blocked(
    sqlite_session: AsyncSession,
) -> None:
    org_id = uuid4()
    board_id = uuid4()
    lead_id = uuid4()
    dep_id = uuid4()
    task_id = uuid4()

    sqlite_session.add(Organization(id=org_id, name="org"))
    sqlite_session.add(Board(id=board_id, organization_id=org_id, name="b", slug="b"))
    sqlite_session.add(
        Agent(
            id=lead_id,
            name="Lead",
            board_id=board_id,
            gateway_id=uuid4(),
            is_board_lead=True,
            openclaw_session_id="agent:lead:session",
        ),
    )
    sqlite_session.add(Task(id=dep_id, board_id=board_id, title="dep", description=None))
    sqlite_session.add(
        Task(
            id=task_id,
            board_id=board_id,
            title="t",
            description=None,
            status="review",
        ),
    )
    sqlite_session.add(
        TaskDependency(
            board_id=board_id,
            task_id=task_id,
            depends_on_task_id=dep_id,
        ),
    )
    await sqlite_session.commit()

    lead = (await sqlite_session.exec(select(Agent).where(col(Agent.id) == lead_id))).first()
    task = (await sqlite_session.exec(select(Task).where(col(Task.id) == task_id))).first()
    assert lead is not None
    assert task is not None

    update = _TaskUpdateInput(
        task=task,
        actor=ActorContext(actor_type="agent", agent=lead),
        board_id=board_id,
        previous_status=task.status,
        previous_assigned=task.assigned_agent_id,
        status_requested=True,
        updates={"status": "done"},
        comment=None,
        depends_on_task_ids=None,
        tag_ids=None,
        custom_field_values={},
        custom_field_values_set=False,
    )

    with pytest.raises(HTTPException) as exc:
        await _apply_lead_task_update(sqlite_session, update=update)

    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "task_blocked_cannot_transition"
    assert detail["blocked_by_task_ids"] == [str(dep_id)]

    reloaded = (await sqlite_session.exec(select(Task).where(col(Task.id) == task_id))).first()
    assert reloaded is not None
    assert reloaded.status == "review"
