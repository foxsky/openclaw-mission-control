# ruff: noqa: INP001
"""Gateway RPCs and template-sync lifecycles must not wait forever.

On 2026-09-14 a superseded OpenClaw config reload never answered MC's ``config.patch``. MC waited
22 minutes inside a lifecycle that held the agent's row lock, and every check-in for that agent
timed out behind it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.openclaw.gateway_rpc as gateway_rpc
import app.services.openclaw.provisioning_db as provisioning_db
from app.schemas.gateways import GatewayTemplatesSyncResult
from app.services.openclaw.gateway_rpc import GatewayConfig, OpenClawGatewayError, openclaw_call
from app.services.openclaw.internal.retry import GatewayBackoff, _is_transient_gateway_error

# Control-UI mode needs no device identity file, so the real connect path runs offline.
_CONFIG = GatewayConfig(url="ws://gateway.example/ws", disable_device_pairing=True)


def _slow_call_once(delay_seconds: float) -> Any:
    async def _fake_call_once(
        method: str,
        params: dict[str, object] | None,
        *,
        config: GatewayConfig,
        gateway_url: str,
    ) -> object:
        del method, params, config, gateway_url
        await asyncio.sleep(delay_seconds)
        return {"ok": True}

    return _fake_call_once


@pytest.mark.asyncio
async def test_openclaw_call_times_out_when_gateway_never_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_rpc, "_openclaw_call_once", _slow_call_once(10))
    monkeypatch.setattr(gateway_rpc, "GATEWAY_RPC_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(
        OpenClawGatewayError, match="gateway rpc status timed out after 0.05s"
    ) as info:
        await openclaw_call("status", config=_CONFIG)

    # Timeouts stay retryable by GatewayBackoff, like other transport failures.
    assert _is_transient_gateway_error(info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["config.patch", "sessions.reset"])
async def test_slow_mutations_get_longer_deadlines_than_other_rpcs(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    monkeypatch.setattr(gateway_rpc, "_openclaw_call_once", _slow_call_once(0.1))
    monkeypatch.setattr(gateway_rpc, "GATEWAY_RPC_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setitem(gateway_rpc.GATEWAY_RPC_TIMEOUT_OVERRIDES_SECONDS, method, 1.0)

    assert await openclaw_call(method, {}, config=_CONFIG) == {"ok": True}
    with pytest.raises(OpenClawGatewayError, match="timed out"):
        await openclaw_call("config.get", config=_CONFIG)


def test_deadlines_cover_supported_slow_gateway_operations() -> None:
    # Above the websocket open timeout, so slow connects keep their own error.
    assert gateway_rpc.GATEWAY_RPC_TIMEOUT_SECONDS > gateway_rpc.GATEWAY_WS_OPEN_TIMEOUT_SECONDS
    overrides = gateway_rpc.GATEWAY_RPC_TIMEOUT_OVERRIDES_SECONDS
    # OpenClaw 2026.9.4: ACP parent+child session cleanup ~4 x 15 s steps; secret providers may
    # take up to 120 s before config.patch applies.
    assert overrides["sessions.reset"] >= 60
    assert overrides["config.patch"] > 120


@pytest.mark.asyncio
async def test_openclaw_connect_metadata_times_out_when_gateway_never_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _hanging_connect_metadata_once(*, config: GatewayConfig, gateway_url: str) -> object:
        del config, gateway_url
        await asyncio.sleep(10)
        return {}

    monkeypatch.setattr(
        gateway_rpc, "_openclaw_connect_metadata_once", _hanging_connect_metadata_once
    )
    monkeypatch.setattr(gateway_rpc, "GATEWAY_RPC_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(OpenClawGatewayError, match="timed out after 0.05s"):
        await gateway_rpc.openclaw_connect_metadata(config=_CONFIG)


class _FakeTransport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class _FakeWebSocket:
    """Gateway double: challenge first, then one response per request after ``reply_delay``."""

    def __init__(self, *, reply_delay: float, close_delay: float) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._reply_delay = reply_delay
        self._close_delay = close_delay
        self._tasks: set[asyncio.Task[None]] = set()
        self.transport = _FakeTransport()
        self.closed = False
        challenge = {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n"}}
        self._queue.put_nowait(json.dumps(challenge))

    async def send(self, raw: str) -> None:
        request = json.loads(raw)
        delay = 0.0 if request["method"] == "connect" else self._reply_delay

        async def _reply() -> None:
            await asyncio.sleep(delay)
            response = {"type": "res", "id": request["id"], "ok": True, "payload": {"ok": True}}
            await self._queue.put(json.dumps(response))

        task = asyncio.get_running_loop().create_task(_reply())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def recv(self) -> str:
        return await self._queue.get()

    async def close(self) -> None:
        await asyncio.sleep(self._close_delay)
        self.closed = True


def _use_fake_gateway(monkeypatch: pytest.MonkeyPatch, ws: _FakeWebSocket) -> None:
    async def _fake_connect(url: str, **kwargs: object) -> _FakeWebSocket:
        del url, kwargs
        return ws

    monkeypatch.setattr(gateway_rpc.websockets, "connect", _fake_connect)


@pytest.mark.asyncio
async def test_timed_out_rpc_drops_the_connection_without_waiting_for_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _FakeWebSocket(reply_delay=10, close_delay=10)
    _use_fake_gateway(monkeypatch, ws)
    monkeypatch.setattr(gateway_rpc, "GATEWAY_RPC_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(OpenClawGatewayError, match="timed out"):
        await asyncio.wait_for(openclaw_call("status", config=_CONFIG), timeout=1)

    assert ws.transport.aborted


@pytest.mark.asyncio
async def test_answered_rpc_returns_even_when_close_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _FakeWebSocket(reply_delay=0, close_delay=10)
    _use_fake_gateway(monkeypatch, ws)
    monkeypatch.setattr(gateway_rpc, "GATEWAY_WS_CLOSE_TIMEOUT_SECONDS", 0.05)

    payload = await asyncio.wait_for(openclaw_call("status", config=_CONFIG), timeout=0.5)

    assert payload == {"ok": True}
    await asyncio.sleep(0.2)
    assert ws.transport.aborted  # background close gave up and dropped the transport


@pytest.mark.asyncio
async def test_answered_rpc_closes_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _FakeWebSocket(reply_delay=0, close_delay=0)
    _use_fake_gateway(monkeypatch, ws)

    assert await openclaw_call("status", config=_CONFIG) == {"ok": True}
    await asyncio.sleep(0.05)
    assert ws.closed
    assert not ws.transport.aborted


class _SessionStub:
    def __init__(self, *, rollback_delay: float = 0.0) -> None:
        self.rolled_back = False
        self.invalidated = False
        self._rollback_delay = rollback_delay

    async def rollback(self) -> None:
        await asyncio.sleep(self._rollback_delay)
        self.rolled_back = True

    async def invalidate(self) -> None:
        self.invalidated = True


@pytest.mark.asyncio
async def test_discard_cancelled_transaction_rolls_back() -> None:
    session = _SessionStub()

    await provisioning_db._discard_cancelled_transaction(session)  # type: ignore[arg-type]

    assert session.rolled_back
    assert not session.invalidated


@pytest.mark.asyncio
async def test_discard_cancelled_transaction_invalidates_when_rollback_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provisioning_db, "_CANCELLED_TRANSACTION_CLEANUP_SECONDS", 0.05)
    session = _SessionStub(rollback_delay=10)

    await asyncio.wait_for(
        provisioning_db._discard_cancelled_transaction(session),  # type: ignore[arg-type]
        timeout=1,
    )

    assert session.invalidated


class _ExpiringAgent:
    """Stands in for an ORM Agent whose attributes expire on rollback (MissingGreenlet)."""

    def __init__(self, session: _SessionStub, name: str) -> None:
        self._session = session
        self._name = name
        self._id = "00000000-0000-4000-8000-000000000001"

    def _check(self) -> None:
        if self._session.rolled_back:
            raise RuntimeError("attribute read after rollback")

    @property
    def id(self) -> str:
        self._check()
        return self._id

    @property
    def name(self) -> str:
        self._check()
        return self._name


@pytest.mark.asyncio
async def test_sync_one_agent_reports_lifecycle_timeout_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStub()
    agent = _ExpiringAgent(session, "Supervisor")
    board = SimpleNamespace(id="00000000-0000-4000-8000-000000000002")

    async def _token(*args: object, **kwargs: object) -> tuple[str, bool]:
        return "token", False

    class _HangingOrchestrator:
        last_lifecycle_warnings: tuple[str, ...] = ()

        def __init__(self, session: object) -> None:
            del session

        async def run_lifecycle(self, **kwargs: object) -> None:
            await asyncio.sleep(10)

    monkeypatch.setattr(provisioning_db, "_resolve_agent_auth_token", _token)
    monkeypatch.setattr(provisioning_db, "AgentLifecycleOrchestrator", _HangingOrchestrator)
    monkeypatch.setattr(provisioning_db, "_agent_key", lambda agent: "lead-x")
    monkeypatch.setattr(provisioning_db, "_SYNC_LIFECYCLE_TIMEOUT_SECONDS", 0.05)
    ctx = SimpleNamespace(
        session=session,
        gateway=SimpleNamespace(),
        backoff=GatewayBackoff(timeout_s=60, base_delay_s=0.01, max_delay_s=0.01),
        options=SimpleNamespace(
            user=None,
            force_bootstrap=False,
            overwrite=False,
            reset_sessions=False,
        ),
    )
    result = GatewayTemplatesSyncResult(
        gateway_id="00000000-0000-4000-8000-000000000003",
        include_main=True,
        reset_sessions=False,
        agents_updated=0,
        agents_skipped=0,
        main_updated=False,
    )

    stop = await provisioning_db._sync_one_agent(ctx, result, agent, board)  # type: ignore[arg-type]

    assert stop is True
    assert session.rolled_back
    assert [error.agent_name for error in result.errors] == ["Supervisor"]
    assert "did not finish within 0.05s" in result.errors[0].message


def test_sync_lifecycle_deadline_covers_rpc_deadlines() -> None:
    # One lifecycle makes several RPCs; its cap must allow the slowest single RPC.
    assert provisioning_db._SYNC_LIFECYCLE_TIMEOUT_SECONDS > max(
        gateway_rpc.GATEWAY_RPC_TIMEOUT_OVERRIDES_SECONDS.values()
    )
