# ruff: noqa: INP001
"""Graceful-drain tests for the queue worker.

The worker uses Redis ``brpop`` (atomic pop, no requeue) and previously
had no SIGTERM handler — a restart mid-handler silently dropped the
popped job. ``stop_event`` plumbing lets ``_run_worker_loop`` install
SIGTERM/SIGINT handlers that ask ``flush_queue`` to exit between
dequeues, so the in-flight task finishes before the worker stops.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services.queue_worker import flush_queue


@pytest.mark.asyncio
async def test_flush_queue_returns_immediately_when_stop_event_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-set stop_event must short-circuit before any dequeue call —
    no Redis round-trip when the worker is already shutting down."""
    dequeue_calls = 0

    def _fail_if_called(*_args: object, **_kwargs: object) -> object:
        nonlocal dequeue_calls
        dequeue_calls += 1
        return None

    monkeypatch.setattr(
        "app.services.queue_worker.dequeue_task",
        _fail_if_called,
    )

    stop_event = asyncio.Event()
    stop_event.set()

    processed = await flush_queue(
        block=True,
        block_timeout=5,
        stop_event=stop_event,
    )

    assert processed == 0
    assert dequeue_calls == 0, "stop_event must short-circuit before any dequeue"


@pytest.mark.asyncio
async def test_flush_queue_ignores_stop_event_mid_handler_then_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If stop_event is set while a handler is running, the in-flight
    task MUST complete before the loop exits. The atomic-pop semantics
    of brpop mean an interrupted handler == silent job loss."""
    from app.services.queue import QueuedTask

    handler_finished = False
    task = QueuedTask(
        task_type="test.drain",
        payload={},
        attempts=0,
        created_at=datetime.now(timezone.utc),
    )
    deliveries = iter([task, None])

    def _fake_dequeue(*_args: object, **_kwargs: object) -> object:
        return next(deliveries, None)

    stop_event = asyncio.Event()

    async def _fake_handler(_task: QueuedTask) -> None:
        nonlocal handler_finished
        # Simulate SIGTERM arriving mid-handler — stop_event is set
        # while we're still inside the await chain.
        stop_event.set()
        await asyncio.sleep(0)
        handler_finished = True

    monkeypatch.setattr(
        "app.services.queue_worker.dequeue_task",
        _fake_dequeue,
    )
    monkeypatch.setitem(
        __import__("app.services.queue_worker", fromlist=["_TASK_HANDLERS"]).__dict__[
            "_TASK_HANDLERS"
        ],
        "test.drain",
        type(
            "FakeHandler",
            (),
            {
                "handler": staticmethod(_fake_handler),
                "attempts_to_delay": staticmethod(lambda _attempts: 0.0),
                "requeue": staticmethod(lambda _task, _delay: False),
            },
        )(),
    )

    processed = await flush_queue(
        block=True,
        block_timeout=5,
        stop_event=stop_event,
    )

    assert handler_finished, "in-flight handler must complete despite stop_event"
    assert processed == 1
