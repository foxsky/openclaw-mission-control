# ruff: noqa: INP001
"""Gateway RPCs and template-sync lifecycles must not wait forever.

On 2026-09-14 a superseded OpenClaw config reload never answered MC's ``config.patch``. MC waited
22 minutes inside a lifecycle that held the agent's row lock, and every check-in for that agent
timed out behind it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.openclaw.gateway_rpc as gateway_rpc
import app.services.openclaw.provisioning_db as provisioning_db
from app.services.openclaw.gateway_rpc import GatewayConfig, OpenClawGatewayError, openclaw_call
from app.services.openclaw.internal.retry import GatewayBackoff, _is_transient_gateway_error


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
        await openclaw_call("status", config=GatewayConfig(url="ws://gateway.example/ws"))

    # Timeouts stay retryable by GatewayBackoff, like other transport failures.
    assert _is_transient_gateway_error(info.value)


@pytest.mark.asyncio
async def test_config_patch_gets_a_longer_deadline_than_other_rpcs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_rpc, "_openclaw_call_once", _slow_call_once(0.1))
    monkeypatch.setattr(gateway_rpc, "GATEWAY_RPC_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setitem(gateway_rpc.GATEWAY_RPC_TIMEOUT_OVERRIDES_SECONDS, "config.patch", 1.0)
    config = GatewayConfig(url="ws://gateway.example/ws")

    assert await openclaw_call("config.patch", {"raw": "{}"}, config=config) == {"ok": True}
    with pytest.raises(OpenClawGatewayError, match="timed out"):
        await openclaw_call("config.get", config=config)


def test_default_deadlines_leave_room_for_the_websocket_open_timeout() -> None:
    assert gateway_rpc.GATEWAY_RPC_TIMEOUT_SECONDS > gateway_rpc.GATEWAY_WS_OPEN_TIMEOUT_SECONDS
    assert gateway_rpc.GATEWAY_RPC_TIMEOUT_OVERRIDES_SECONDS["config.patch"] >= 90.0


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
        await gateway_rpc.openclaw_connect_metadata(
            config=GatewayConfig(url="ws://gateway.example/ws"),
        )


class _SessionStub:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


def _sync_context(session: _SessionStub) -> Any:
    return SimpleNamespace(
        session=session,
        backoff=GatewayBackoff(timeout_s=60, base_delay_s=0.01, max_delay_s=0.01),
    )


@pytest.mark.asyncio
async def test_sync_lifecycle_deadline_rolls_back_the_shared_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provisioning_db, "_SYNC_LIFECYCLE_TIMEOUT_SECONDS", 0.05)
    session = _SessionStub()

    async def _hanging_lifecycle() -> bool:
        await asyncio.sleep(10)
        return True

    with pytest.raises(TimeoutError, match="Supervisor.*exceeded 0.05s"):
        await provisioning_db._run_sync_lifecycle(
            _sync_context(session),  # type: ignore[arg-type]
            _hanging_lifecycle,
            agent_name="Supervisor",
        )

    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_sync_lifecycle_deadline_passes_through_completed_lifecycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provisioning_db, "_SYNC_LIFECYCLE_TIMEOUT_SECONDS", 1.0)
    session = _SessionStub()

    async def _quick_lifecycle() -> bool:
        return True

    await provisioning_db._run_sync_lifecycle(
        _sync_context(session),  # type: ignore[arg-type]
        _quick_lifecycle,
        agent_name="Supervisor",
    )

    assert session.rollbacks == 0


def test_sync_lifecycle_deadline_covers_rpc_deadlines() -> None:
    # One lifecycle makes several RPCs; its cap must allow at least one slow config.patch.
    assert (
        provisioning_db._SYNC_LIFECYCLE_TIMEOUT_SECONDS
        > gateway_rpc.GATEWAY_RPC_TIMEOUT_OVERRIDES_SECONDS["config.patch"]
    )
