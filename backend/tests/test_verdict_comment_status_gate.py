# ruff: noqa
"""Companion to the review-event gate: a validator's *verdict-shaped* COMMENT
must not land on a task outside the review flow either.

PR #16 gates the structured `/review-events` verdict, but a validator can also
express a verdict as a free-text comment (the QA/Architect skills post a
`VERDICT: …` / `**Verdict: …**` comment alongside the structured event). Without
this gate, that free-text verdict could still land on an `inbox` task — the same
"working the inbox column" the structured gate blocks. Both the POST /comments
and the PATCH/update_task inline-comment paths are gated.

Scoped narrowly: only a verdict-shaped message from a verdict-posting reviewer
on a non-`review`/`rework` task is rejected. A non-verdict routing note, a
non-reviewer's comment, and the board lead quoting a verdict while routing are
all still allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.tasks import (
    _allowed_reviewer_roles_for_agent,
    _require_verdict_comment_in_review_flow,
)
from app.models.agents import Agent


@dataclass
class _ActorStub:
    agent: Agent | None
    actor_type: str = "agent"
    user: object | None = None


def _agent(profile: dict, *, is_board_lead: bool = False) -> Agent:
    return Agent(
        id=uuid4(),
        board_id=uuid4(),
        gateway_id=uuid4(),
        name="agent",
        is_board_lead=is_board_lead,
        openclaw_session_id="agent:x:main",
        identity_profile=profile,
    )


def _qa() -> _ActorStub:
    return _ActorStub(agent=_agent({"validation_flow": "qa_validation"}))


def _architect() -> _ActorStub:
    return _ActorStub(agent=_agent({"dev_acp_flow": "review_only"}))


def _named_architect() -> _ActorStub:
    # No flow flag — recognised as a reviewer by role/name only (Codex finding #2).
    return _ActorStub(agent=_agent({"role": "System Architect and Code Reviewer"}))


def _implementer() -> _ActorStub:
    return _ActorStub(agent=_agent({"role": "Programmer-Frontend"}))


def _lead() -> _ActorStub:
    return _ActorStub(agent=_agent({"role": "Supervisor"}, is_board_lead=True))


QA_VERDICT = "VERDICT: INCONCLUSIVE\n\nTarget unreachable; cannot validate."
ARCH_VERDICT = "**Architect review** — `abc` · **Verdict: FAIL**\n\n- Blocking: x"
ROUTING_NOTE = "@lead this task is not in `review`, please route it to review first."


@pytest.mark.parametrize("status", ["inbox", "in_progress", "done", "cancelled"])
def test_qa_verdict_comment_rejected_off_review_flow(status: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_verdict_comment_in_review_flow(task_status=status, message=QA_VERDICT, actor=_qa())
    assert exc.value.status_code == 409
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("code") == "verdict_comment_task_not_in_review"


def test_architect_verdict_comment_rejected_on_inbox() -> None:
    with pytest.raises(HTTPException) as exc:
        _require_verdict_comment_in_review_flow(
            task_status="inbox", message=ARCH_VERDICT, actor=_architect()
        )
    assert exc.value.status_code == 409


def test_name_based_reviewer_verdict_rejected_on_inbox() -> None:
    # A reviewer identified by role/name (no flow flag) must also be gated.
    with pytest.raises(HTTPException) as exc:
        _require_verdict_comment_in_review_flow(
            task_status="inbox", message=ARCH_VERDICT, actor=_named_architect()
        )
    assert exc.value.status_code == 409


@pytest.mark.parametrize(
    "message",
    [
        "**Architect review** — `abc` · **Verdict: FAIL**",  # bold wraps label+value
        "**Architect review** — `abc` · **Verdict:** FAIL",  # bold label, plain value
        "**Architect review** — `abc` · Verdict: **FAIL**",  # plain label, bold value
        "VERDICT:  `INCONCLUSIVE`  — target unreachable",  # backtick-wrapped value
    ],
)
def test_markdown_verdict_variants_rejected_on_inbox(message: str) -> None:
    # An Architect/review-only reviewer has no VERDICT-prefix format gate, so the
    # declaration regex must tolerate markdown emphasis around the verdict token.
    with pytest.raises(HTTPException) as exc:
        _require_verdict_comment_in_review_flow(
            task_status="inbox", message=message, actor=_architect()
        )
    assert exc.value.status_code == 409


@pytest.mark.parametrize("status", ["review", "rework"])
def test_verdict_comment_allowed_in_review_flow(status: str) -> None:
    # No raise on review/rework — the legitimate verdict path.
    _require_verdict_comment_in_review_flow(task_status=status, message=QA_VERDICT, actor=_qa())
    _require_verdict_comment_in_review_flow(
        task_status=status, message=ARCH_VERDICT, actor=_architect()
    )


def test_non_verdict_routing_note_allowed_on_inbox() -> None:
    # A validator's non-verdict routing note must still be postable off-review.
    _require_verdict_comment_in_review_flow(
        task_status="inbox", message=ROUTING_NOTE, actor=_architect()
    )


def test_lead_verdict_quote_allowed_off_review() -> None:
    # The lead legitimately quotes a verdict while routing — never gated.
    _require_verdict_comment_in_review_flow(task_status="inbox", message=QA_VERDICT, actor=_lead())


def test_non_validator_not_gated() -> None:
    # The gate is scoped to reviewers; an implementer comment is never a verdict.
    _require_verdict_comment_in_review_flow(
        task_status="inbox", message=QA_VERDICT, actor=_implementer()
    )


def test_bare_named_qa_validation_agent_is_gated() -> None:
    """A `validation_flow=qa_validation` agent whose name carries no `e2e`/`unit`
    keyword gets NO reviewer role from `_allowed_reviewer_roles_for_agent`, so a
    role-set-only reviewer check would miss it. The explicit flow-field disjunct
    in `_actor_is_verdict_posting_reviewer` is what keeps it gated — this guards
    against a future collapse of that union into the role set alone.
    """
    actor = _qa()  # name="agent" — carries no e2e/unit keyword
    assert _allowed_reviewer_roles_for_agent(actor.agent) == set()
    with pytest.raises(HTTPException) as exc:
        _require_verdict_comment_in_review_flow(
            task_status="inbox", message=QA_VERDICT, actor=actor
        )
    assert exc.value.status_code == 409
