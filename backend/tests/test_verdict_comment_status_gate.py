# ruff: noqa
"""Companion to the review-event gate: a validator's *verdict-shaped* COMMENT
must not land on a task outside the review flow either.

PR #16 gates the structured `/review-events` verdict, but a validator can also
express a verdict as a free-text task comment (the QA/Architect skills post a
`VERDICT: …` / `**Verdict: …**` comment alongside the structured event). Without
this gate, that free-text verdict could still land on an `inbox` task — the same
"working the inbox column" the structured gate blocks.

Scoped narrowly so it does NOT block a validator's legitimate *non-verdict*
routing note (e.g. "@lead this task is not in review, route it") — only a
verdict-shaped message on a non-`review`/`rework` task is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.tasks import _require_verdict_comment_in_review_flow
from app.models.agents import Agent
from app.models.tasks import Task


@dataclass
class _ActorStub:
    agent: Agent | None
    actor_type: str = "agent"
    user: object | None = None


def _agent(profile: dict) -> Agent:
    return Agent(
        id=uuid4(),
        board_id=uuid4(),
        gateway_id=uuid4(),
        name="agent",
        openclaw_session_id="agent:x:main",
        identity_profile=profile,
    )


def _qa() -> _ActorStub:
    return _ActorStub(agent=_agent({"validation_flow": "qa_validation"}))


def _architect() -> _ActorStub:
    return _ActorStub(agent=_agent({"dev_acp_flow": "review_only"}))


def _implementer() -> _ActorStub:
    return _ActorStub(agent=_agent({"role": "Programmer-Frontend"}))


def _task(status: str) -> Task:
    return Task(id=uuid4(), board_id=uuid4(), title="t", status=status)


QA_VERDICT = "VERDICT: INCONCLUSIVE\n\nTarget unreachable; cannot validate."
ARCH_VERDICT = "**Architect review** — `abc` · **Verdict: FAIL**\n\n- Blocking: x"
ROUTING_NOTE = "@lead this task is not in `review`, please route it to review first."


@pytest.mark.parametrize("status", ["inbox", "in_progress", "done", "cancelled"])
def test_qa_verdict_comment_rejected_off_review_flow(status: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_verdict_comment_in_review_flow(task=_task(status), message=QA_VERDICT, actor=_qa())
    assert exc.value.status_code == 409
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("code") == "verdict_comment_task_not_in_review"


def test_architect_verdict_comment_rejected_on_inbox() -> None:
    with pytest.raises(HTTPException) as exc:
        _require_verdict_comment_in_review_flow(
            task=_task("inbox"), message=ARCH_VERDICT, actor=_architect()
        )
    assert exc.value.status_code == 409


@pytest.mark.parametrize("status", ["review", "rework"])
def test_verdict_comment_allowed_in_review_flow(status: str) -> None:
    # No raise on review/rework — the legitimate verdict path.
    _require_verdict_comment_in_review_flow(task=_task(status), message=QA_VERDICT, actor=_qa())
    _require_verdict_comment_in_review_flow(
        task=_task(status), message=ARCH_VERDICT, actor=_architect()
    )


def test_non_verdict_routing_note_allowed_on_inbox() -> None:
    # A validator's non-verdict routing note must still be postable off-review.
    _require_verdict_comment_in_review_flow(
        task=_task("inbox"), message=ROUTING_NOTE, actor=_architect()
    )


def test_non_validator_not_gated() -> None:
    # The gate is scoped to validators; an implementer comment is never a verdict.
    _require_verdict_comment_in_review_flow(
        task=_task("inbox"), message=QA_VERDICT, actor=_implementer()
    )
