# ruff: noqa: INP001
"""Tests for GET /api/mission-control/heartbeats — paused-state surface.

Before this fix the endpoint:
- Filtered out agents whose ``heartbeat_config["every"]`` was ``"0m"``,
  but did NOT consult ``board_pause_states.is_paused`` or
  ``agent_heartbeats.enabled``.
- Always set ``"enabled": True`` for every included agent.

Operator-visible result: paused agents either disappeared from the
list (if their config happened to be 0m) or appeared as
``enabled=True``/overdue/monitored — lying about their state.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

import app.api.mission_control as mission_control
from app.core.time import utcnow
from app.models.agents import Agent
from app.models.boards import Board


@dataclass
class _FakeExecResult:
    rows: list[Any]

    def all(self) -> list[Any]:
        return self.rows

    def first(self) -> Any:
        return self.rows[0] if self.rows else None


@dataclass
class _FakeSession:
    agents: list[Agent] = field(default_factory=list)
    boards: list[Board] = field(default_factory=list)
    paused_board_ids: set[UUID] = field(default_factory=set)
    disabled_agent_ids: set[UUID] = field(default_factory=set)

    async def exec(self, statement: Any) -> _FakeExecResult:
        # Distinguish agent vs board selects by the SQL text shape.
        sql = str(statement).lower()
        if "agents" in sql and "boards" not in sql:
            return _FakeExecResult(rows=list(self.agents))
        if "boards" in sql:
            return _FakeExecResult(rows=list(self.boards))
        return _FakeExecResult(rows=[])

    async def execute(self, statement: Any) -> _FakeExecResult:
        sql = str(statement).lower()
        if "board_pause_states" in sql:
            return _FakeExecResult(rows=[(bid,) for bid in self.paused_board_ids])
        if "agent_heartbeats" in sql:
            return _FakeExecResult(rows=[(aid,) for aid in self.disabled_agent_ids])
        return _FakeExecResult(rows=[])


def _agent(*, board_id: UUID | None, heartbeat_every: str = "5m") -> Agent:
    now = utcnow()
    return Agent(
        id=uuid4(),
        gateway_id=uuid4(),
        board_id=board_id,
        name=f"Agent-{uuid4().hex[:4]}",
        status="online",
        heartbeat_config={"every": heartbeat_every},
        checkin_deadline_at=now + timedelta(minutes=5),
        last_seen_at=now - timedelta(seconds=30),
        wake_attempts=0,
    )


@pytest.mark.asyncio
async def test_heartbeats_status_marks_board_paused_agent_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paused_board = uuid4()
    agent = _agent(board_id=paused_board)
    session = _FakeSession(agents=[agent], paused_board_ids={paused_board})

    @asynccontextmanager
    async def _fake_maker():
        yield session

    monkeypatch.setattr(mission_control, "async_session_maker", _fake_maker)

    response = await mission_control.mission_control_heartbeats()

    assert response["agents_monitored"] == 1
    listed = response["agents"]
    assert len(listed) == 1
    assert listed[0]["agent_id"] == str(agent.id)
    assert listed[0]["enabled"] is False


@pytest.mark.asyncio
async def test_heartbeats_status_marks_disabled_agent_row_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``agent_heartbeats.enabled=FALSE`` should surface in the status feed."""

    agent = _agent(board_id=None)
    session = _FakeSession(
        agents=[agent],
        disabled_agent_ids={agent.id},
    )

    @asynccontextmanager
    async def _fake_maker():
        yield session

    monkeypatch.setattr(mission_control, "async_session_maker", _fake_maker)

    response = await mission_control.mission_control_heartbeats()

    listed = response["agents"]
    assert len(listed) == 1
    assert listed[0]["enabled"] is False


@pytest.mark.asyncio
async def test_heartbeats_status_keeps_enabled_for_unpaused_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: don't mislabel healthy agents."""

    agent = _agent(board_id=uuid4())
    session = _FakeSession(agents=[agent])

    @asynccontextmanager
    async def _fake_maker():
        yield session

    monkeypatch.setattr(mission_control, "async_session_maker", _fake_maker)

    response = await mission_control.mission_control_heartbeats()

    listed = response["agents"]
    assert len(listed) == 1
    assert listed[0]["enabled"] is True


@pytest.mark.asyncio
async def test_heartbeats_status_still_omits_zero_every_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agents with no scheduled heartbeat continue to be filtered out
    of the monitored list — they're not paused, they're not monitored."""

    agent = _agent(board_id=None, heartbeat_every="0m")
    session = _FakeSession(agents=[agent])

    @asynccontextmanager
    async def _fake_maker():
        yield session

    monkeypatch.setattr(mission_control, "async_session_maker", _fake_maker)

    response = await mission_control.mission_control_heartbeats()

    assert response["agents_monitored"] == 0
