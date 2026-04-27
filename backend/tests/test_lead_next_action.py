# ruff: noqa: INP001
"""Regression tests for deterministic lead next-action selection."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from app.core.time import utcnow
from app.models.tasks import Task
from app.services.lead_next_action import latest_approval_state_by_task_id, select_lead_next_action


def _task(
    *,
    status: str,
    title: str = "Task",
    assigned: bool = False,
    review_packet_type: str | None = None,
) -> Task:
    return Task(
        id=uuid4(),
        board_id=uuid4(),
        title=title,
        status=status,
        assigned_agent_id=uuid4() if assigned else None,
        review_packet_type=review_packet_type,
    )


def test_selects_approved_review_task_for_gate_inspection_first() -> None:
    task = _task(status="review", title="Ready review")

    action = select_lead_next_action(
        tasks=[task],
        blocked_by_task_id={},
        approval_state_by_task_id={task.id: "approved"},
        pipeline_missing_by_task_id={},
        review_readiness_by_task_id={task.id: {"ready": True}},
    )

    assert action.action_required is True
    assert action.action == "inspect_review_gates"
    assert action.reason_code == "approved_review_needs_done_gate"
    assert action.task_id == task.id
    assert action.details["approval_state"] == "approved"
    assert action.details["review_readiness"] == {"ready": True}


def test_frontend_review_with_missing_pipeline_requires_gate_inspection() -> None:
    task = _task(status="review", title="Frontend review", review_packet_type="frontend_ui")

    action = select_lead_next_action(
        tasks=[task],
        blocked_by_task_id={},
        approval_state_by_task_id={task.id: "none"},
        pipeline_missing_by_task_id={task.id: ["deployed", "runtime_verified"]},
    )

    assert action.action_required is True
    assert action.action == "inspect_review_gates"
    assert action.reason_code == "review_task_missing_gates"
    assert action.task_id == task.id
    assert action.details["approval_state"] == "none"
    assert action.details["missing_pipeline_states"] == ["deployed", "runtime_verified"]


def test_routes_unassigned_inbox_when_no_review_or_active_work_exists() -> None:
    task = _task(status="inbox", title="New work")

    action = select_lead_next_action(
        tasks=[task],
        blocked_by_task_id={},
        approval_state_by_task_id={},
        pipeline_missing_by_task_id={},
    )

    assert action.action_required is True
    assert action.action == "route_inbox"
    assert action.reason_code == "unassigned_inbox_needs_routing"
    assert action.task_id == task.id


def test_returns_clear_when_only_known_blocked_work_remains() -> None:
    task = _task(status="in_progress", title="Blocked work", assigned=True)

    action = select_lead_next_action(
        tasks=[task],
        blocked_by_task_id={task.id: [uuid4()]},
        approval_state_by_task_id={},
        pipeline_missing_by_task_id={},
    )

    assert action.action_required is False
    assert action.action == "clear"
    assert action.reason_code == "only_waiting_or_no_active_work"


def test_latest_approval_state_uses_newest_move_to_done_row() -> None:
    task_id = uuid4()
    old_created = utcnow()
    old_resolved = old_created
    new_created = old_created + timedelta(minutes=1)
    new_resolved = new_created

    state = latest_approval_state_by_task_id(
        task_ids=[task_id],
        rows=[
            (task_id, "approved", old_resolved, old_created),
            (task_id, "rejected", new_resolved, new_created),
        ],
    )

    assert state[task_id] == "rejected"
