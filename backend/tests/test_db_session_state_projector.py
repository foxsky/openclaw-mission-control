"""Tests for ``DbSessionStateProjector`` — wires sessions.changed
event frames to ``GatewaySessionState`` rows in Postgres (SQLite in
tests). Co-tests the parser-via-build_state_from_frame so the contract
between parsing and persistence is locked end-to-end.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.mc_gateway_subscriber.db_session_state_projector import (
    DbSessionStateProjector,
)
from app.services.mc_gateway_subscriber.session_state_repo import (
    SessionStateRepo,
)


# ---------------------------------------------------------------------------
# Fixture: a session_factory that yields the test sqlite session every call.
#
# Production gives the projector a real `async_sessionmaker[AsyncSession]`
# from `app.db.session`. In tests we hand it the SAME session the
# assertion code reads from, so the projector's commits are visible
# without a separate connection.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(
    sqlite_session: AsyncSession,
) -> AsyncIterator:
    """Async-context-manager factory returning the shared test session."""

    class _Factory:
        def __call__(self):
            return _Ctx(sqlite_session)

    class _Ctx:
        def __init__(self, sess: AsyncSession) -> None:
            self._sess = sess

        async def __aenter__(self) -> AsyncSession:
            return self._sess

        async def __aexit__(self, *exc) -> None:
            # Test owns the session lifecycle; do not close it here.
            return None

    yield _Factory()


def _frame(
    *,
    session_key: str = "agent:mc-aaaaaaaa-1111-2222-3333-444444444444:main",
    phase: str = "message",
    ts: int = 1_777_823_446_849,
    message_seq: int | None = 158,
    session_id: str = "062b709b-540e-430b-b451-d48f4acff7b9",
    input_tokens: int | None = 49_931,
    output_tokens: int | None = 14_736,
    total_tokens: int | None = 64_667,
    channel: str = "webchat",
    aborted_last_run: bool = False,
) -> dict:
    inner: dict = {
        "sessionKey": session_key,
        "phase": phase,
        "ts": ts,
        "session": {
            "key": session_key,
            "kind": "direct",
            "label": "QA-E2E",
            "displayName": "webchat:g-agent",
            "channel": channel,
            "sessionId": session_id,
            "abortedLastRun": aborted_last_run,
            "updatedAt": ts,
        },
    }
    if message_seq is not None:
        inner["messageSeq"] = message_seq
    if input_tokens is not None:
        inner["session"]["inputTokens"] = input_tokens
    if output_tokens is not None:
        inner["session"]["outputTokens"] = output_tokens
    if total_tokens is not None:
        inner["session"]["totalTokens"] = total_tokens
    return {"type": "event", "event": "sessions.changed", "payload": inner, "seq": 1}


@pytest.mark.asyncio
async def test_db_projector_writes_first_event(
    session_factory,
    sqlite_session: AsyncSession,
) -> None:
    p = DbSessionStateProjector(session_factory=session_factory)
    await p(_frame())

    rows = await SessionStateRepo.list_all(sqlite_session)
    assert len(rows) == 1
    assert rows[0].agent_id == "mc-aaaaaaaa-1111-2222-3333-444444444444"
    assert rows[0].session_label == "main"
    assert rows[0].last_changed_at_ms == 1_777_823_446_849
    assert rows[0].total_tokens == 64_667


@pytest.mark.asyncio
async def test_db_projector_overwrites_on_newer_event(
    session_factory,
    sqlite_session: AsyncSession,
) -> None:
    p = DbSessionStateProjector(session_factory=session_factory)
    await p(_frame(ts=1, total_tokens=100, phase="created"))
    await p(_frame(ts=2, total_tokens=200, phase="message"))

    rows = await SessionStateRepo.list_all(sqlite_session)
    assert len(rows) == 1
    assert rows[0].last_changed_at_ms == 2
    assert rows[0].total_tokens == 200
    assert rows[0].last_phase == "message"


@pytest.mark.asyncio
async def test_db_projector_drops_older_or_equal_timestamp(
    session_factory,
    sqlite_session: AsyncSession,
) -> None:
    """Reconnect replays can deliver an older snapshot — must not
    regress the persisted row."""
    p = DbSessionStateProjector(session_factory=session_factory)
    await p(_frame(ts=200, total_tokens=2000))
    await p(_frame(ts=100, total_tokens=1000))
    await p(_frame(ts=200, total_tokens=999))  # equal-ts is also a drop

    rows = await SessionStateRepo.list_all(sqlite_session)
    assert len(rows) == 1
    assert rows[0].last_changed_at_ms == 200
    assert rows[0].total_tokens == 2000


@pytest.mark.asyncio
async def test_db_projector_skips_redundant_no_op_writes(
    session_factory,
    sqlite_session: AsyncSession,
) -> None:
    """Heartbeat ticks emit sessions.changed every ~10s with identical
    field values. The projector must NOT issue a DB write when the
    incoming state is field-equal to the persisted row — otherwise the
    Postgres write rate scales with heartbeat tick count, not with
    actual session activity."""
    p = DbSessionStateProjector(session_factory=session_factory)
    await p(_frame(ts=1, total_tokens=100))
    first_updated_at = (await SessionStateRepo.list_all(sqlite_session))[0].updated_at

    # Same fields except a strictly-newer ts (the only field that must
    # advance to make the projector consider the event "new"). With
    # strict diff, this still counts as no-op because the new last-known
    # gateway-side fields are unchanged. Behaviour we want: skip.
    # ALTERNATIVELY: ts itself is a meaningful field, so a newer ts +
    # all-other-fields-equal might still be considered worth recording.
    # Decision: ts is the gateway's clock, not a meaningful state field,
    # so skip if every other field equals existing.
    await asyncio.sleep(0.01)
    await p(_frame(ts=2, total_tokens=100))
    second_updated_at = (await SessionStateRepo.list_all(sqlite_session))[0].updated_at
    assert first_updated_at == second_updated_at, (
        "no-op event (only ts changed) must not bump updated_at"
    )


@pytest.mark.asyncio
async def test_db_projector_writes_when_a_real_field_changed(
    session_factory,
    sqlite_session: AsyncSession,
) -> None:
    """Skipping no-ops must NOT also skip real field changes — verify
    the diff guard fires only when payload-fields are equal."""
    p = DbSessionStateProjector(session_factory=session_factory)
    await p(_frame(ts=1, total_tokens=100, message_seq=None))
    # message_seq advancing from None -> 5 IS a real change
    await p(_frame(ts=2, total_tokens=100, message_seq=5))

    rows = await SessionStateRepo.list_all(sqlite_session)
    assert rows[0].last_message_seq == 5
    assert rows[0].last_changed_at_ms == 2


@pytest.mark.asyncio
async def test_db_projector_drops_unparseable_session_key(
    session_factory,
    sqlite_session: AsyncSession,
) -> None:
    p = DbSessionStateProjector(session_factory=session_factory)
    await p(_frame(session_key="not-an-agent-key"))
    await p(_frame(session_key="agent:x:cron:y:run:z"))  # 5-segment cron run

    rows = await SessionStateRepo.list_all(sqlite_session)
    assert rows == []


@pytest.mark.asyncio
async def test_db_projector_tracks_multiple_sessions_per_agent(
    session_factory,
    sqlite_session: AsyncSession,
) -> None:
    p = DbSessionStateProjector(session_factory=session_factory)
    aid = "mc-aaaaaaaa-1111-2222-3333-444444444444"
    await p(_frame(session_key=f"agent:{aid}:main"))
    await p(_frame(session_key=f"agent:{aid}:debug"))

    rows = await SessionStateRepo.list_for_agent(sqlite_session, agent_id=aid)
    assert {r.session_label for r in rows} == {"main", "debug"}


@pytest.mark.asyncio
async def test_db_projector_handler_does_not_raise_on_malformed_frame(
    session_factory,
    sqlite_session: AsyncSession,
) -> None:
    """The Subscriber dispatcher swallows handler exceptions but a
    quiet drop is preferable — verify the projector returns cleanly
    on every malformed shape we've seen in production."""
    p = DbSessionStateProjector(session_factory=session_factory)
    await p({})  # no event/payload
    await p({"type": "event", "event": "sessions.changed"})  # no payload
    await p({"type": "event", "event": "sessions.changed", "payload": None})
    await p({"type": "event", "event": "sessions.changed", "payload": "oops"})
    await p({"type": "event", "event": "sessions.changed",
             "payload": {"phase": "message"}})  # no sessionKey
    rows = await SessionStateRepo.list_all(sqlite_session)
    assert rows == []
