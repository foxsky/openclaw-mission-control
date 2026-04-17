# ruff: noqa: INP001
"""Unit tests for the Phase 0 shadow-metric emitter.

Covers amendment section A.2 (classifier wired into the comment write
path) and A.4 (append-only events, 90-day retention is a downstream
job — not tested here).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.time import utcnow
from app.models.activity_events import ActivityEvent
from app.models.shadow_metric_events import ShadowMetricEvent
from app.services.shadow_metrics import (
    EVENT_COMMENT_ACK_ONLY,
    EVENT_COMMENT_NEAR_DUPLICATE,
    build_shadow_events_for_comment,
)


@dataclass
class _FakeExecResult:
    rows: list[Any] = field(default_factory=list)

    def first(self) -> Any | None:
        return self.rows[0] if self.rows else None


@dataclass
class _FakeSession:
    """Minimal AsyncSession stand-in used for the prior-comment fetch."""

    prior: ActivityEvent | None = None

    async def exec(self, _statement: Any) -> _FakeExecResult:
        return _FakeExecResult(rows=[self.prior] if self.prior else [])


@pytest.mark.asyncio
async def test_clean_comment_emits_no_events() -> None:
    """Substantive, non-ack comment yields zero shadow events."""

    session = _FakeSession()
    events = await build_shadow_events_for_comment(
        session,  # type: ignore[arg-type]
        task_id=uuid4(),
        board_id=uuid4(),
        agent_id=uuid4(),
        source_event_id=uuid4(),
        message="Filed PR #1 against master, tests passing. Running lighthouse now.",
        packet_type="frontend_ui",
    )
    assert events == []


@pytest.mark.asyncio
async def test_ack_only_comment_emits_one_event() -> None:
    """An ack-theater comment emits a single ack_only_candidate event."""

    session = _FakeSession()
    source_id = uuid4()
    task_id = uuid4()
    agent_id = uuid4()
    board_id = uuid4()
    events = await build_shadow_events_for_comment(
        session,  # type: ignore[arg-type]
        task_id=task_id,
        board_id=board_id,
        agent_id=agent_id,
        source_event_id=source_id,
        message="Acknowledged. Holding exactly there. No status change. @lead",
        packet_type="frontend_ui",
    )
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, ShadowMetricEvent)
    assert event.event_type == EVENT_COMMENT_ACK_ONLY
    assert event.task_id == task_id
    assert event.agent_id == agent_id
    assert event.board_id == board_id
    assert event.source_event_id == source_id
    assert event.metadata_json is not None
    assert event.metadata_json["packet_type"] == "frontend_ui"


@pytest.mark.asyncio
async def test_near_duplicate_emits_event_when_prior_exists() -> None:
    """With a recent same-author prior, duplicate detection fires."""

    now = utcnow()
    prior_msg = "Acknowledged. Holding there."
    prior = ActivityEvent(
        event_type="task.comment",
        message=prior_msg,
        task_id=uuid4(),
        agent_id=uuid4(),
        created_at=now - timedelta(seconds=60),
    )
    session = _FakeSession(prior=prior)
    events = await build_shadow_events_for_comment(
        session,  # type: ignore[arg-type]
        task_id=prior.task_id or uuid4(),
        board_id=None,
        agent_id=prior.agent_id,
        source_event_id=uuid4(),
        message=prior_msg,  # identical -> jaccard 1.0
        packet_type="frontend_ui",
        now=now,
    )
    event_types = {e.event_type for e in events}
    assert EVENT_COMMENT_ACK_ONLY in event_types
    assert EVENT_COMMENT_NEAR_DUPLICATE in event_types


@pytest.mark.asyncio
async def test_no_prior_lookup_when_agent_id_is_none() -> None:
    """User comments (agent_id=None) skip the dedup path entirely."""

    session = _FakeSession(prior=None)
    events = await build_shadow_events_for_comment(
        session,  # type: ignore[arg-type]
        task_id=uuid4(),
        board_id=uuid4(),
        agent_id=None,
        source_event_id=uuid4(),
        message="Filed PR, tests passing.",
        packet_type="frontend_ui",
    )
    assert events == []


@pytest.mark.asyncio
async def test_prior_outside_window_does_not_trigger_duplicate() -> None:
    """A prior from 6 min ago is outside the 5-min dedup window."""

    now = utcnow()
    prior_msg = "Acknowledged. Holding there."
    prior = ActivityEvent(
        event_type="task.comment",
        message=prior_msg,
        task_id=uuid4(),
        agent_id=uuid4(),
        created_at=now - timedelta(minutes=6),
    )
    session = _FakeSession(prior=prior)
    events = await build_shadow_events_for_comment(
        session,  # type: ignore[arg-type]
        task_id=prior.task_id or uuid4(),
        board_id=None,
        agent_id=prior.agent_id,
        source_event_id=uuid4(),
        message=prior_msg,
        packet_type="frontend_ui",
        now=now,
    )
    # Ack-only still fires; near-duplicate does not because the prior
    # is outside the window. The _fake_session returns it anyway, but
    # the classifier's own gap check rejects it.
    event_types = {e.event_type for e in events}
    assert EVENT_COMMENT_ACK_ONLY in event_types
    assert EVENT_COMMENT_NEAR_DUPLICATE not in event_types


@pytest.mark.asyncio
async def test_classifier_failure_returns_empty_list() -> None:
    """Exception inside classify() must not break the caller's commit."""

    class _ExplodingSession:
        async def exec(self, _statement: Any) -> _FakeExecResult:
            raise RuntimeError("simulated DB failure")

    events = await build_shadow_events_for_comment(
        _ExplodingSession(),  # type: ignore[arg-type]
        task_id=uuid4(),
        board_id=uuid4(),
        agent_id=uuid4(),
        source_event_id=uuid4(),
        message="Acknowledged. Holding there.",
        packet_type="frontend_ui",
    )
    assert events == []


@pytest.mark.asyncio
async def test_lax_packet_type_suppresses_ack_only() -> None:
    """A short ack on a review_only task is legit, not theater."""

    session = _FakeSession()
    events = await build_shadow_events_for_comment(
        session,  # type: ignore[arg-type]
        task_id=uuid4(),
        board_id=uuid4(),
        agent_id=uuid4(),
        source_event_id=uuid4(),
        message="Acknowledged. Reassigning to Architect.",
        packet_type="review_only",
    )
    assert events == []
