"""Deterministic next-action selection for board lead heartbeats."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.models.tasks import Task
from app.schemas.lead_actions import LeadNextActionName, LeadNextActionRead

ApprovalState = Literal["none", "pending", "approved", "rejected"]
ApprovalStateRow = tuple[UUID | None, str, datetime | None, datetime]


def latest_approval_state_by_task_id(
    *,
    task_ids: Sequence[UUID],
    rows: Sequence[ApprovalStateRow],
) -> dict[UUID, ApprovalState]:
    """Return the latest relevant approval state for each task."""

    state_by_task_id: dict[UUID, ApprovalState] = {task_id: "none" for task_id in task_ids}
    latest_by_task_id: dict[UUID, tuple[datetime, ApprovalState]] = {}
    for task_id, raw_status, resolved_at, created_at in rows:
        if task_id is None:
            continue
        timestamp = resolved_at or created_at
        current = latest_by_task_id.get(task_id)
        if current is not None and timestamp <= current[0]:
            continue
        if raw_status in {"pending", "approved", "rejected"}:
            latest_by_task_id[task_id] = (timestamp, raw_status)
    for task_id, (_timestamp, approval_status) in latest_by_task_id.items():
        state_by_task_id[task_id] = approval_status
    return state_by_task_id


def _is_waiting(task: Task, blocked_by_task_id: Mapping[UUID, Sequence[UUID]]) -> bool:
    if task.status in {"done", "cancelled"}:
        return True
    return bool(blocked_by_task_id.get(task.id)) or bool(task.operator_decision_required)


def _task_sort_key(task: Task) -> tuple[str, str]:
    timestamp = task.updated_at or task.created_at
    return (timestamp.isoformat(), str(task.id))


def _action(
    *,
    task: Task | None,
    action_required: bool,
    action: LeadNextActionName,
    reason_code: str,
    details: dict[str, object] | None = None,
) -> LeadNextActionRead:
    return LeadNextActionRead(
        action_required=action_required,
        action=action,
        reason_code=reason_code,
        task_id=task.id if task is not None else None,
        task_status=task.status if task is not None else None,
        task_title=task.title if task is not None else None,
        assigned_agent_id=task.assigned_agent_id if task is not None else None,
        details=details or {},
    )


def select_lead_next_action(
    *,
    tasks: Sequence[Task],
    blocked_by_task_id: Mapping[UUID, Sequence[UUID]],
    approval_state_by_task_id: Mapping[UUID, ApprovalState],
    pipeline_missing_by_task_id: Mapping[UUID, Sequence[str]],
    review_readiness_by_task_id: Mapping[UUID, object] | None = None,
) -> LeadNextActionRead:
    """Return the single closest-to-done lead action from structured state."""

    active_tasks = [
        task
        for task in tasks
        if task.status in {"inbox", "in_progress", "review", "rework"}
        and not _is_waiting(task, blocked_by_task_id)
    ]
    ordered = sorted(active_tasks, key=_task_sort_key)
    review_readiness_by_task_id = review_readiness_by_task_id or {}

    for task in ordered:
        if task.status != "review":
            continue
        if approval_state_by_task_id.get(task.id, "none") == "approved":
            return _action(
                task=task,
                action_required=True,
                action="inspect_review_gates",
                reason_code="approved_review_needs_done_gate",
                details={
                    "approval_state": "approved",
                    "review_readiness": review_readiness_by_task_id.get(task.id),
                },
            )

    for task in ordered:
        if task.status != "review":
            continue
        approval_state = approval_state_by_task_id.get(task.id, "none")
        missing_pipeline = list(pipeline_missing_by_task_id.get(task.id, []))
        return _action(
            task=task,
            action_required=True,
            action="inspect_review_gates",
            reason_code="review_task_missing_gates",
            details={
                "approval_state": approval_state,
                "missing_pipeline_states": missing_pipeline,
                "review_packet_type": task.review_packet_type,
                "validation_target": task.validation_target,
                "review_readiness": review_readiness_by_task_id.get(task.id),
            },
        )

    for task in ordered:
        if task.status == "rework" and task.assigned_agent_id is not None:
            return _action(
                task=task,
                action_required=True,
                action="route_rework",
                reason_code="assigned_rework_needs_owner_followup",
            )

    for task in ordered:
        if task.status == "in_progress" and task.assigned_agent_id is not None:
            return _action(
                task=task,
                action_required=True,
                action="inspect_stale_in_progress",
                reason_code="in_progress_work_needs_health_check",
            )

    for task in ordered:
        if task.status == "inbox" and task.assigned_agent_id is None:
            return _action(
                task=task,
                action_required=True,
                action="route_inbox",
                reason_code="unassigned_inbox_needs_routing",
            )

    return _action(
        task=None,
        action_required=False,
        action="clear",
        reason_code="only_waiting_or_no_active_work",
    )
