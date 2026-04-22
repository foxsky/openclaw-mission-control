# ruff: noqa: INP001

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import ActorContext
from app.api.tasks import _apply_lead_task_update, _TaskUpdateInput
from app.models.agents import Agent
from app.models.boards import Board
from app.models.organizations import Organization
from app.models.task_dependencies import TaskDependency
from app.models.tasks import Task
from app.services.task_dependencies import blocked_by_for_task


@pytest.mark.asyncio
async def test_lead_dependency_only_update_allowed_when_task_blocked(
    sqlite_session: AsyncSession,
) -> None:
    """Leads may update dependencies even if the task is currently blocked.

    This supports unblocking work by adjusting dependency graphs, while still
    rejecting status/assignee transitions.
    """

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
    sqlite_session.add(
        Task(
            id=dep_id,
            board_id=board_id,
            title="dep",
            description=None,
            status="inbox",
        ),
    )
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
    blocked_by_before = await blocked_by_for_task(
        sqlite_session,
        board_id=board_id,
        task_id=task_id,
    )
    assert blocked_by_before == [dep_id]

    # Re-assert the same deps list; this should be a no-op and should not
    # be rejected solely because the task is blocked.
    update = _TaskUpdateInput(
        task=task,
        actor=ActorContext(actor_type="agent", agent=lead),
        board_id=board_id,
        previous_status=task.status,
        previous_assigned=task.assigned_agent_id,
        status_requested=False,
        updates={},
        comment=None,
        depends_on_task_ids=[dep_id],
        tag_ids=None,
        custom_field_values={},
        custom_field_values_set=False,
    )

    result = await _apply_lead_task_update(sqlite_session, update=update)
    assert result.id == task_id
    assert result.is_blocked is True
    assert result.blocked_by_task_ids == [dep_id]

    reloaded = (await sqlite_session.exec(select(Task).where(col(Task.id) == task_id))).first()
    assert reloaded is not None
    assert reloaded.status == "review"
    assert reloaded.assigned_agent_id is None
    dependency_rows = (
        await sqlite_session.exec(
            select(TaskDependency).where(
                col(TaskDependency.task_id) == task_id,
                col(TaskDependency.depends_on_task_id) == dep_id,
            ),
        )
    ).all()
    assert len(dependency_rows) == 1
