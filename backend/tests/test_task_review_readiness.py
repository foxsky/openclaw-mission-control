# ruff: noqa: INP001
"""Regression tests for structured review verdict readiness."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from app.core.time import utcnow
from app.models.task_review_events import TaskReviewEvent
from app.models.tasks import Task
from app.services.task_review_events import build_review_readiness


def _task(*, review_packet_type: str = "frontend_ui") -> Task:
    return Task(
        id=uuid4(),
        board_id=uuid4(),
        title="Task under review",
        status="review",
        review_packet_type=review_packet_type,
        in_progress_at=utcnow(),
    )


def _event(
    task: Task,
    *,
    reviewer_role: str,
    verdict: str,
    minutes_after_cycle: int = 1,
    evidence: dict[str, object] | None = None,
) -> TaskReviewEvent:
    assert task.board_id is not None
    assert task.in_progress_at is not None
    return TaskReviewEvent(
        board_id=task.board_id,
        task_id=task.id,
        agent_id=uuid4(),
        reviewer_role=reviewer_role,
        verdict=verdict,
        evidence_type="browser" if reviewer_role == "qa_e2e" else "review",
        evidence=evidence,
        created_at=task.in_progress_at + timedelta(minutes=minutes_after_cycle),
    )


def test_frontend_review_readiness_requires_architect_and_qa_e2e_pass() -> None:
    task = _task(review_packet_type="frontend_ui")
    readiness = build_review_readiness(
        task=task,
        events=[
            _event(task, reviewer_role="architect", verdict="pass"),
            _event(task, reviewer_role="qa_e2e", verdict="pass"),
        ],
    )

    assert readiness.ready is True
    assert readiness.required_roles == ["architect", "qa_e2e"]
    assert readiness.missing_roles == []
    assert readiness.blocking_roles == []


def test_latest_fail_blocks_even_when_required_roles_exist() -> None:
    task = _task(review_packet_type="backend_api")
    readiness = build_review_readiness(
        task=task,
        events=[
            _event(task, reviewer_role="architect", verdict="pass"),
            _event(task, reviewer_role="qa_unit", verdict="pass"),
            _event(task, reviewer_role="qa_unit", verdict="fail", minutes_after_cycle=2),
        ],
    )

    assert readiness.ready is False
    assert readiness.missing_roles == []
    assert readiness.blocking_roles == ["qa_unit"]


def test_stale_verdict_before_current_cycle_does_not_count() -> None:
    task = _task(review_packet_type="infra_ops")
    readiness = build_review_readiness(
        task=task,
        events=[
            _event(task, reviewer_role="devops", verdict="pass", minutes_after_cycle=-1),
        ],
    )

    assert readiness.ready is False
    assert readiness.required_roles == ["devops"]
    assert readiness.missing_roles == ["devops"]


def test_review_only_architect_pass_requires_child_task_evidence() -> None:
    task = _task(review_packet_type="review_only")
    readiness = build_review_readiness(
        task=task,
        events=[
            _event(task, reviewer_role="architect", verdict="pass"),
        ],
    )

    assert readiness.ready is False
    assert readiness.artifact_issues == [
        "review_only_architect_pass_missing_child_task_evidence"
    ]


def test_review_only_architect_pass_accepts_declared_child_task_ids() -> None:
    task = _task(review_packet_type="review_only")
    child_task_id = uuid4()
    readiness = build_review_readiness(
        task=task,
        events=[
            _event(
                task,
                reviewer_role="architect",
                verdict="pass",
                evidence={"planned_child_task_ids": [str(child_task_id)]},
            ),
        ],
        board_task_ids={task.id, child_task_id},
    )

    assert readiness.ready is True
    assert readiness.declared_child_task_ids == [child_task_id]
    assert readiness.artifact_issues == []


def test_review_only_architect_pass_blocks_missing_declared_child_task_ids() -> None:
    task = _task(review_packet_type="review_only")
    child_task_id = uuid4()
    readiness = build_review_readiness(
        task=task,
        events=[
            _event(
                task,
                reviewer_role="architect",
                verdict="pass",
                evidence={"planned_child_task_ids": [str(child_task_id)]},
            ),
        ],
        board_task_ids={task.id},
    )

    assert readiness.ready is False
    assert readiness.declared_child_task_ids == [child_task_id]
    assert readiness.missing_child_task_ids == [child_task_id]
    assert readiness.artifact_issues == [
        "review_only_architect_pass_child_tasks_not_found"
    ]


def test_review_only_architect_pass_accepts_explicit_no_child_tasks_required() -> None:
    task = _task(review_packet_type="review_only")
    readiness = build_review_readiness(
        task=task,
        events=[
            _event(
                task,
                reviewer_role="architect",
                verdict="pass",
                evidence={"no_child_tasks_required": True},
            ),
        ],
    )

    assert readiness.ready is True
    assert readiness.artifact_issues == []
