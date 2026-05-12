"""Tests for global set-heartbeats reconciliation.

Uses in-memory SQLite per the MC test convention — no production DB access.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

import app.services.openclaw.provisioning as provisioning
from app.api import board_memory
from app.models.agents import Agent
from app.models.boards import Board
from app.models.gateways import Gateway
from app.models.organizations import Organization
from app.services.openclaw.gateway_rpc import GatewayConfig as GatewayClientConfig


def _seed_org_gateway_board(
    *, board_name: str = "Board A", board_slug: str = "board-a"
) -> tuple[Organization, Gateway, Board]:
    org_id = uuid4()
    org = Organization(id=org_id, name="Acme")
    gateway = Gateway(
        id=uuid4(),
        organization_id=org_id,
        name="GW",
        url="ws://gateway.example/ws",
        token="tok",
        workspace_root="/tmp/openclaw",
    )
    board = Board(
        id=uuid4(),
        organization_id=org_id,
        name=board_name,
        slug=board_slug,
        gateway_id=gateway.id,
    )
    return org, gateway, board


@pytest.mark.asyncio
async def test_board_memory_pause_resume_sends_single_global_set_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_session: AsyncSession,
) -> None:
    sent_messages: list[str] = []
    rpc_calls: list[tuple[str, dict]] = []

    async def _fake_try_send(self, *, session_key, config, agent_name, message, deliver=False):
        sent_messages.append(message)
        return None

    async def _fake_openclaw_call(method, params=None, *, config=None, timeout=None):
        rpc_calls.append((method, params or {}))
        return {"ok": True}

    monkeypatch.setattr(
        board_memory.GatewayDispatchService,
        "try_send_agent_message",
        _fake_try_send,
    )
    monkeypatch.setattr(board_memory, "openclaw_call", _fake_openclaw_call, raising=False)

    session = sqlite_session
    org, gateway, board = _seed_org_gateway_board()
    agents = [
        Agent(
            id=uuid4(),
            board_id=board.id,
            gateway_id=gateway.id,
            name="Worker 1",
            openclaw_session_id="session:w1",
            heartbeat_config={"every": "30m"},
        ),
        Agent(
            id=uuid4(),
            board_id=board.id,
            gateway_id=gateway.id,
            name="Worker 2",
            openclaw_session_id="session:w2",
            heartbeat_config={"every": "30m"},
        ),
    ]
    session.add(org)
    session.add(gateway)
    session.add(board)
    for a in agents:
        session.add(a)
    await session.commit()

    actor = board_memory.ActorContext(actor_type="user", user=None, agent=None)
    dispatch = board_memory.GatewayDispatchService(session)
    config = GatewayClientConfig(
        url=gateway.url,
        token=gateway.token,
        allow_insecure_tls=True,
        disable_device_pairing=True,
    )

    await board_memory._send_control_command(
        session=session,
        board=board,
        actor=actor,
        dispatch=dispatch,
        config=config,
        command="/pause",
    )
    await board_memory._send_control_command(
        session=session,
        board=board,
        actor=actor,
        dispatch=dispatch,
        config=config,
        command="/resume",
    )

    # Two agents → two chat messages per command = 4 total
    assert sent_messages == ["/pause", "/pause", "/resume", "/resume"]
    # One global RPC per command, not per-agent
    assert rpc_calls == [
        ("set-heartbeats", {"enabled": False}),
        ("set-heartbeats", {"enabled": True}),
    ]


@pytest.mark.asyncio
async def test_apply_agent_lifecycle_enables_global_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    async def _fake_openclaw_call(method, params=None, *, config=None, timeout=None):
        calls.append((method, params or {}))
        return {"ok": True}

    async def _fake_ensure_session(session_key, *, config=None, label=None):
        return None

    async def _fake_send_message(message, *, session_key, config=None, deliver=False):
        return {"ok": True}

    async def _fake_provision(self, **kwargs):
        return None

    monkeypatch.setattr(provisioning, "openclaw_call", _fake_openclaw_call)
    monkeypatch.setattr(provisioning, "ensure_session", _fake_ensure_session)
    monkeypatch.setattr(provisioning, "send_message", _fake_send_message)
    monkeypatch.setattr(
        provisioning.BoardAgentLifecycleManager,
        "provision",
        _fake_provision,
    )

    org_id = uuid4()
    gateway = Gateway(
        id=uuid4(),
        organization_id=org_id,
        name="GW",
        url="ws://gateway.example/ws",
        token="tok",
        workspace_root="/tmp/openclaw",
    )
    board = Board(
        id=uuid4(),
        organization_id=org_id,
        name="Board A",
        slug="board-a",
        gateway_id=gateway.id,
    )
    agent = Agent(
        id=uuid4(),
        board_id=board.id,
        gateway_id=gateway.id,
        name="Worker",
        openclaw_session_id="session:worker",
        heartbeat_config={"every": "30m"},
    )

    async def _fake_any_board_active(gateway_id):  # noqa: ANN001
        return True

    monkeypatch.setattr(provisioning, "_any_board_active_on_gateway", _fake_any_board_active)

    await provisioning.OpenClawGatewayProvisioner().apply_agent_lifecycle(
        agent=agent,
        gateway=gateway,
        board=board,
        auth_token="secret-token",
        user=None,
        action="update",
        wake=True,
    )

    assert ("set-heartbeats", {"enabled": True}) in calls


@pytest.mark.asyncio
async def test_apply_agent_lifecycle_skips_enable_when_all_boards_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every board on the gateway is paused, lifecycle must NOT
    flip global heartbeats back on. Before this fix the call was
    unconditional and would erase the operator's pause."""

    calls: list[tuple[str, dict]] = []

    async def _fake_openclaw_call(method, params=None, *, config=None, timeout=None):
        calls.append((method, params or {}))
        return {"ok": True}

    async def _fake_ensure_session(session_key, *, config=None, label=None):
        return None

    async def _fake_send_message(message, *, session_key, config=None, deliver=False):
        return {"ok": True}

    async def _fake_provision(self, **kwargs):
        return None

    async def _fake_any_board_active(gateway_id):  # noqa: ANN001
        return False

    monkeypatch.setattr(provisioning, "openclaw_call", _fake_openclaw_call)
    monkeypatch.setattr(provisioning, "ensure_session", _fake_ensure_session)
    monkeypatch.setattr(provisioning, "send_message", _fake_send_message)
    monkeypatch.setattr(
        provisioning.BoardAgentLifecycleManager,
        "provision",
        _fake_provision,
    )
    monkeypatch.setattr(provisioning, "_any_board_active_on_gateway", _fake_any_board_active)

    org_id = uuid4()
    gateway = Gateway(
        id=uuid4(),
        organization_id=org_id,
        name="GW",
        url="ws://gateway.example/ws",
        token="tok",
        workspace_root="/tmp/openclaw",
    )
    board = Board(
        id=uuid4(),
        organization_id=org_id,
        name="Paused Board",
        slug="paused-board",
        gateway_id=gateway.id,
    )
    agent = Agent(
        id=uuid4(),
        board_id=board.id,
        gateway_id=gateway.id,
        name="Worker",
        openclaw_session_id="session:worker",
        heartbeat_config={"every": "30m"},
    )

    await provisioning.OpenClawGatewayProvisioner().apply_agent_lifecycle(
        agent=agent,
        gateway=gateway,
        board=board,
        auth_token="secret-token",
        user=None,
        action="update",
        wake=True,
    )

    set_heartbeats_calls = [c for c in calls if c[0] == "set-heartbeats"]
    assert set_heartbeats_calls, "expected at least one set-heartbeats call"
    for _method, params in set_heartbeats_calls:
        assert params.get("enabled") is False, (
            f"all-paused gateway must call set-heartbeats with enabled=False, "
            f"got params={params}"
        )
