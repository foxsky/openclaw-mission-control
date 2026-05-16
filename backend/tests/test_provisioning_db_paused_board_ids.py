# ruff: noqa: INP001
"""Regression: provisioning_db._paused_board_ids reads from
``board_pause_states`` (the unified source written by both the
mission_control API and the board_memory chat-command handler), NOT
the BoardMemory ``/pause``/``/resume`` chat log.

Pre-fix the function parsed BoardMemory chat entries to find the
latest pause command per board. That silently diverged from the
actual paused state in two cases:

1. An operator paused or resumed via the ``mission_control`` API —
   no corresponding chat row was written, so this function saw a
   stale earlier command as the latest and reported the wrong state.
2. BoardMemory history was pruned but ``board_pause_states`` was
   still authoritative.

After fix, ``heartbeat_sweep._fetch_paused_board_ids`` and
``provisioning_db._paused_board_ids`` both read from the same table,
keeping template-sync and sweep/watchdog skip behavior consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.openclaw.provisioning_db import _paused_board_ids


@dataclass
class _FakeExecResult:
    rows: list[Any]

    def __iter__(self) -> Any:
        return iter(self.rows)


@dataclass
class _FakeSession:
    paused_board_ids: set[UUID] = field(default_factory=set)

    async def execute(self, statement: Any) -> _FakeExecResult:
        sql = str(statement).lower()
        if "board_pause_states" in sql:
            return _FakeExecResult(rows=[(bid,) for bid in self.paused_board_ids])
        return _FakeExecResult(rows=[])


@pytest.mark.asyncio
async def test_paused_board_ids_returns_intersection_of_paused_and_requested() -> None:
    """Only boards in the caller's requested set AND marked paused in
    ``board_pause_states`` are returned. Boards paused on other gateways
    or outside the requested ``board_ids`` are filtered out."""
    requested_paused = uuid4()
    requested_active = uuid4()
    foreign_paused = uuid4()
    session = _FakeSession(paused_board_ids={requested_paused, foreign_paused})

    result = await _paused_board_ids(
        session,  # type: ignore[arg-type]
        [requested_paused, requested_active],
    )

    assert result == {requested_paused}


@pytest.mark.asyncio
async def test_paused_board_ids_empty_when_no_board_ids_requested() -> None:
    """Short-circuit: empty input → empty output without any DB hit."""
    session = _FakeSession(paused_board_ids={uuid4()})

    result = await _paused_board_ids(session, [])  # type: ignore[arg-type]

    assert result == set()


@pytest.mark.asyncio
async def test_paused_board_ids_returns_empty_set_when_nothing_paused() -> None:
    """No paused rows in board_pause_states → empty set even if input
    ids are provided."""
    session = _FakeSession(paused_board_ids=set())

    result = await _paused_board_ids(session, [uuid4(), uuid4()])  # type: ignore[arg-type]

    assert result == set()
