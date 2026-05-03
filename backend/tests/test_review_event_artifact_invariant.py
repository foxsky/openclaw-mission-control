# ruff: noqa: INP001
"""POST /review-events must reject Architect PASS for review_only packets
that lack child-task evidence — write-time enforcement of the
``planned_child_task_ids`` / ``no_child_tasks_required:true`` rule from
``architect-review-verdict/SKILL.md``.

Production gap 2026-05-03 on QA gate 5b7abdd2: Architect (still on the
old skill content) posted a structured PASS without child_task evidence.
The structured event committed cleanly; review-readiness reported
``ready=false`` with ``artifact_issues=[review_only_architect_pass_missing_child_task_evidence]``;
Supervisor REACTIVELY filed a Blocker. Reactive blockers in agent prose
are exactly the layer the architectural critique earlier today flagged
as wrong — invariants belong at the write boundary.

The fix: reject the POST itself with 422 + ``code=review_only_pass_requires_child_task_evidence``
when (reviewer_role=architect, verdict=pass, task.review_packet_type=review_only)
and the evidence dict has neither ``planned_child_task_ids`` (non-empty)
nor ``no_child_tasks_required:true``.
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


@dataclass
class _ActorStub:
    agent: Agent | None
    actor_type: str = "agent"
    user: object | None = None


async def _seed_review_only_task(
    session: AsyncSession,
    *,
    board_slug: str,
    review_packet_type: str = "review_only",
) -> tuple[Board, Agent, Task]:
    """Seed an org/gateway/board + Architect agent + a review-status
    task with the given review_packet_type."""
    org_id = uuid4()
    gateway_id = uuid4()
    board_id = uuid4()
    architect_id = uuid4()
    task_id = uuid4()

    session.add(Organization(id=org_id, name=f"org-{board_slug}"))
    session.add(
        Gateway(
            id=gateway_id, organization_id=org_id, name=f"gw-{board_slug}",
            url="ws://gateway.example/ws", workspace_root="/tmp/openclaw",
        ),
    )
    board = Board(
        id=board_id, organization_id=org_id, gateway_id=gateway_id,
        name=board_slug, slug=board_slug,
    )
    session.add(board)
    architect = Agent(
        id=architect_id, board_id=board_id, gateway_id=gateway_id,
        name="Architect", openclaw_session_id=f"agent:{board_slug}:architect",
        identity_profile={"dev_acp_flow": "review_only"},
    )
    session.add(architect)
    task = Task(
        id=task_id, board_id=board_id,
        title=f"task-{board_slug}", status="review",
        review_packet_type=review_packet_type,
        assigned_agent_id=architect_id,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return board, architect, task


@pytest.mark.asyncio
async def test_review_only_architect_pass_without_child_task_evidence_rejects(
    sqlite_session: AsyncSession,
) -> None:
    """Architect PASS on a review_only packet without
    ``planned_child_task_ids`` or ``no_child_tasks_required:true``
    must be rejected at write time with 422."""
    board, architect, task = await _seed_review_only_task(
        sqlite_session, board_slug="review-only-pass-no-evidence",
    )
    payload = TaskReviewEventCreate(
        reviewer_role="architect",
        verdict="pass",
        evidence_type="source_review",
        target="http://192.168.2.63:3002/product",
        evidence={"comment": "PASS without child_task evidence"},
    )
    with pytest.raises(HTTPException) as exc_info:
        await tasks_api.record_task_review_event(
            payload=payload,
            task=task,
            session=sqlite_session,
            actor=_ActorStub(agent=architect),
        )
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "review_only_pass_requires_child_task_evidence"
    assert "planned_child_task_ids" in str(detail.get("message", ""))


@pytest.mark.asyncio
async def test_review_only_architect_pass_with_no_child_tasks_required_succeeds(
    sqlite_session: AsyncSession,
) -> None:
    """Positive control: explicit ``no_child_tasks_required:true`` in the
    evidence dict satisfies the rule (intentionally non-decomposed)."""
    board, architect, task = await _seed_review_only_task(
        sqlite_session, board_slug="review-only-explicit-no-children",
    )
    payload = TaskReviewEventCreate(
        reviewer_role="architect",
        verdict="pass",
        evidence_type="source_review",
        target="http://192.168.2.63:3002/product",
        evidence={
            "comment": "PASS — single-AC scope, no decomposition needed",
            "no_child_tasks_required": True,
        },
    )
    read = await tasks_api.record_task_review_event(
        payload=payload,
        task=task,
        session=sqlite_session,
        actor=_ActorStub(agent=architect),
    )
    assert read.verdict == "pass"
    assert read.reviewer_role == "architect"


@pytest.mark.asyncio
async def test_review_only_architect_pass_with_planned_child_task_ids_succeeds(
    sqlite_session: AsyncSession,
) -> None:
    """Positive control: ``planned_child_task_ids`` (non-empty list)
    satisfies the rule."""
    board, architect, task = await _seed_review_only_task(
        sqlite_session, board_slug="review-only-with-children",
    )
    child_id = uuid4()
    sqlite_session.add(
        Task(
            id=child_id, board_id=board.id,
            title="subtask", status="inbox",
            parent_task_id=task.id,
        ),
    )
    await sqlite_session.commit()

    payload = TaskReviewEventCreate(
        reviewer_role="architect",
        verdict="pass",
        evidence_type="source_review",
        target="http://192.168.2.63:3002/product",
        evidence={
            "comment": "PASS — decomposition into 1 child task",
            "planned_child_task_ids": [str(child_id)],
        },
    )
    read = await tasks_api.record_task_review_event(
        payload=payload,
        task=task,
        session=sqlite_session,
        actor=_ActorStub(agent=architect),
    )
    assert read.verdict == "pass"


@pytest.mark.asyncio
async def test_review_only_architect_fail_does_not_require_child_task_evidence(
    sqlite_session: AsyncSession,
) -> None:
    """FAIL verdicts don't need child_task evidence — the rule is
    PASS-specific (only applies to the ``ready`` decision the
    review-readiness gate makes for done transitions)."""
    board, architect, task = await _seed_review_only_task(
        sqlite_session, board_slug="review-only-fail-no-evidence",
    )
    payload = TaskReviewEventCreate(
        reviewer_role="architect",
        verdict="fail",
        evidence_type="source_review",
        target="http://192.168.2.63:3002/product",
        evidence={"comment": "FAIL: missing field X"},
        blocking_owner="PF",
    )
    read = await tasks_api.record_task_review_event(
        payload=payload,
        task=task,
        session=sqlite_session,
        actor=_ActorStub(agent=architect),
    )
    assert read.verdict == "fail"


@pytest.mark.asyncio
async def test_frontend_ui_packet_pass_does_not_trigger_invariant(
    sqlite_session: AsyncSession,
) -> None:
    """The rule is review_only-specific. ``frontend_ui`` packets keep
    the existing gate logic — Architect PASS without child_task
    evidence is fine because the packet has its own browser/source
    artifacts."""
    board, architect, task = await _seed_review_only_task(
        sqlite_session, board_slug="frontend-ui-pass",
        review_packet_type="frontend_ui",
    )
    payload = TaskReviewEventCreate(
        reviewer_role="architect",
        verdict="pass",
        evidence_type="source_review",
        target="http://192.168.2.63:3002/product",
        evidence={"comment": "PASS on frontend_ui packet"},
    )
    read = await tasks_api.record_task_review_event(
        payload=payload,
        task=task,
        session=sqlite_session,
        actor=_ActorStub(agent=architect),
    )
    assert read.verdict == "pass"


@pytest.mark.asyncio
async def test_review_only_qa_pass_does_not_trigger_invariant(
    sqlite_session: AsyncSession,
) -> None:
    """The rule applies only to ``architect`` reviewer_role for
    review_only packets. QA roles have their own evidence requirements
    that aren't gated by child_task_ids."""
    board, _, task = await _seed_review_only_task(
        sqlite_session, board_slug="review-only-qa-pass",
    )
    qa_id = uuid4()
    qa = Agent(
        id=qa_id, board_id=board.id, gateway_id=board.gateway_id,
        name="QA-E2E", openclaw_session_id="agent:qa:main",
        identity_profile={"validation_flow": "qa_validation"},
    )
    sqlite_session.add(qa)
    await sqlite_session.commit()
    await sqlite_session.refresh(qa)

    payload = TaskReviewEventCreate(
        reviewer_role="qa_e2e",
        verdict="pass",
        evidence_type="browser",
        target="http://192.168.2.63:3002/product",
        evidence={"comment": "QA PASS on review_only packet"},
    )
    read = await tasks_api.record_task_review_event(
        payload=payload,
        task=task,
        session=sqlite_session,
        actor=_ActorStub(agent=qa),
    )
    assert read.verdict == "pass"
