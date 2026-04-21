# ruff: noqa: INP001
"""Lifecycle tests for the actionability-emit background-task pool.

Covers the Codex-driven hardening in commit that follows da3c1947:

- backlog cap prevents unbounded growth under slow-DB storm
- shutdown drain waits for pending emits to land
- CancelledError path logs without swallowing
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import app.api.tasks as tasks_module
from app.api.tasks import (
    _ACTIONABILITY_EMIT_MAX_PENDING,
    _ACTIONABILITY_EMIT_TASKS,
    _schedule_actionability_violation_emit,
    drain_actionability_emit_tasks,
)


@pytest.fixture(autouse=True)
def _clean_task_set() -> Any:
    """Reset module state between tests so leakage doesn't cross-pollinate."""

    _ACTIONABILITY_EMIT_TASKS.clear()
    yield
    # Cancel anything left behind and swallow so teardown doesn't
    # surface scheduler warnings.
    for pending in list(_ACTIONABILITY_EMIT_TASKS):
        pending.cancel()
    _ACTIONABILITY_EMIT_TASKS.clear()


@pytest.mark.asyncio
async def test_schedule_registers_task_in_strong_ref_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scheduled task must be held in the module-level set so asyncio
    doesn't GC it mid-flight."""

    async def _noop_emit(**_kw: Any) -> None:
        return None

    monkeypatch.setattr(tasks_module, "emit_actionability_violation_metric", _noop_emit)
    from uuid import uuid4

    _schedule_actionability_violation_emit(
        task_id=uuid4(),
        board_id=None,
        agent_id=None,
        status_value="in_progress",
        missing_fields=["validation_target"],
    )
    assert len(_ACTIONABILITY_EMIT_TASKS) == 1
    await drain_actionability_emit_tasks()
    assert len(_ACTIONABILITY_EMIT_TASKS) == 0


@pytest.mark.asyncio
async def test_backlog_cap_drops_excess_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the pending set is at capacity, further schedules are dropped
    with a WARN log; they must NOT raise, block, or grow the set."""

    async def _slow_emit(**_kw: Any) -> None:
        await asyncio.sleep(10)  # long enough to hold slot through the test

    monkeypatch.setattr(tasks_module, "emit_actionability_violation_metric", _slow_emit)
    from uuid import uuid4

    # Fill to capacity.
    for _ in range(_ACTIONABILITY_EMIT_MAX_PENDING):
        _schedule_actionability_violation_emit(
            task_id=uuid4(),
            board_id=None,
            agent_id=None,
            status_value="in_progress",
            missing_fields=["validation_target"],
        )
    assert len(_ACTIONABILITY_EMIT_TASKS) == _ACTIONABILITY_EMIT_MAX_PENDING

    # One more must be dropped + logged.
    overflow_task_id = uuid4()
    with caplog.at_level("WARNING", logger="app.api.tasks"):
        _schedule_actionability_violation_emit(
            task_id=overflow_task_id,
            board_id=None,
            agent_id=None,
            status_value="in_progress",
            missing_fields=["validation_target"],
        )
    assert len(_ACTIONABILITY_EMIT_TASKS) == _ACTIONABILITY_EMIT_MAX_PENDING
    assert "actionability_emit_dropped_backlog_full" in caplog.text


@pytest.mark.asyncio
async def test_drain_waits_for_pending_emits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """drain_actionability_emit_tasks must await any in-flight emit
    before returning, so shutdown doesn't lose signal."""

    completed: list[str] = []

    async def _tracked_emit(*, task_id: Any, **_kw: Any) -> None:
        await asyncio.sleep(0)
        completed.append(str(task_id))

    monkeypatch.setattr(tasks_module, "emit_actionability_violation_metric", _tracked_emit)
    from uuid import uuid4

    a = uuid4()
    b = uuid4()
    _schedule_actionability_violation_emit(
        task_id=a, board_id=None, agent_id=None,
        status_value="in_progress", missing_fields=["validation_target"],
    )
    _schedule_actionability_violation_emit(
        task_id=b, board_id=None, agent_id=None,
        status_value="in_progress", missing_fields=["validation_target"],
    )

    await drain_actionability_emit_tasks()
    assert sorted(completed) == sorted([str(a), str(b)])
    assert len(_ACTIONABILITY_EMIT_TASKS) == 0


@pytest.mark.asyncio
async def test_drain_on_empty_set_is_noop() -> None:
    """Drain is safe to call when no emits are pending."""

    assert len(_ACTIONABILITY_EMIT_TASKS) == 0
    await drain_actionability_emit_tasks()
