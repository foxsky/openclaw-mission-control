"""Tests for global set-heartbeats reconciliation.

Uses in-memory SQLite per the MC test convention — no production DB access.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

import app.services.openclaw.provisioning as provisioning
from app.api import board_memory
from app.models.agents import Agent
from app.models.boards import Board
from app.models.gateways import Gateway
from app.models.organizations import Organization
from app.services.openclaw.gateway_rpc import GatewayConfig as GatewayClientConfig


@pytest_asyncio.fixture
async def pause_tables(sqlite_engine: AsyncEngine) -> None:
    """Create the raw-DDL tables that the pause flow writes to.

    ``board_pause_states`` and ``agent_heartbeats`` live outside the
    SQLModel metadata (they're created via Postgres migrations on prod),
    so ``SQLModel.metadata.create_all`` doesn't conjure them. The chat-
    command pause flow now writes both, so tests that exercise that
    path need them to exist on the sqlite engine.
    """
    async with sqlite_engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS board_pause_states ("
                "  board_id TEXT PRIMARY KEY,"
                "  is_paused BOOLEAN NOT NULL DEFAULT FALSE,"
                "  paused_at TEXT,"
                "  paused_by TEXT"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS agent_heartbeats ("
                "  agent_id TEXT PRIMARY KEY,"
                "  enabled BOOLEAN NOT NULL DEFAULT TRUE,"
                "  last_status TEXT,"
                "  checkin_deadline_at TEXT,"
                "  miss_count INTEGER NOT NULL DEFAULT 0"
                ")"
            )
        )


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
    pause_tables: None,
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

    # The real helper opens its own production-DB session. Route it
    # through the test's sqlite session so it sees the writes this
    # test will commit to ``board_pause_states``.
    async def _fake_any_board_active(gateway_id):  # noqa: ANN001
        rows = await session.execute(text("""
                SELECT b.id FROM boards b
                LEFT JOIN board_pause_states bps ON bps.board_id = b.id
                WHERE b.gateway_id = :gateway_id
                  AND COALESCE(bps.is_paused, FALSE) = FALSE
                LIMIT 1
                """).bindparams(gateway_id=gateway_id))
        return rows.first() is not None

    monkeypatch.setattr(board_memory, "_any_board_active_on_gateway", _fake_any_board_active)

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
    # One global RPC per command, not per-agent. Single-board: pause
    # → no active boards → enabled=False; resume → board active again.
    assert rpc_calls == [
        ("set-heartbeats", {"enabled": False}),
        ("set-heartbeats", {"enabled": True}),
    ]


@pytest.mark.asyncio
async def test_board_memory_pause_keeps_heartbeats_on_when_other_board_active(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_session: AsyncSession,
    pause_tables: None,
) -> None:
    """Multi-board safety: pausing Board A on a shared gateway must NOT
    silence Board B's agents. Before this fix the chat-command path
    unconditionally sent ``set-heartbeats {enabled: False}`` on
    ``/pause``, which would stop scheduling for every agent on the
    gateway — including the still-active second board."""

    rpc_calls: list[tuple[str, dict]] = []
    sent_messages: list[str] = []

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
    board_a = Board(
        id=uuid4(),
        organization_id=org_id,
        name="Board A",
        slug="board-a",
        gateway_id=gateway.id,
    )
    board_b = Board(
        id=uuid4(),
        organization_id=org_id,
        name="Board B",
        slug="board-b",
        gateway_id=gateway.id,
    )
    agent_a = Agent(
        id=uuid4(),
        board_id=board_a.id,
        gateway_id=gateway.id,
        name="Worker A",
        openclaw_session_id="session:a",
        heartbeat_config={"every": "30m"},
    )
    session.add(org)
    session.add(gateway)
    session.add(board_a)
    session.add(board_b)
    session.add(agent_a)
    await session.commit()

    async def _fake_any_board_active(gateway_id):  # noqa: ANN001
        rows = await session.execute(text("""
                SELECT b.id FROM boards b
                LEFT JOIN board_pause_states bps ON bps.board_id = b.id
                WHERE b.gateway_id = :gateway_id
                  AND COALESCE(bps.is_paused, FALSE) = FALSE
                LIMIT 1
                """).bindparams(gateway_id=gateway_id))
        return rows.first() is not None

    monkeypatch.setattr(board_memory, "_any_board_active_on_gateway", _fake_any_board_active)

    actor = board_memory.ActorContext(actor_type="user", user=None, agent=None)
    dispatch = board_memory.GatewayDispatchService(session)
    config = GatewayClientConfig(
        url=gateway.url,
        token=gateway.token,
        allow_insecure_tls=True,
        disable_device_pairing=True,
    )

    # Pause only Board A; Board B stays active.
    await board_memory._send_control_command(
        session=session,
        board=board_a,
        actor=actor,
        dispatch=dispatch,
        config=config,
        command="/pause",
    )

    # Board B is still active on the same gateway → gateway flag must
    # stay enabled even though Board A was paused.
    assert sent_messages == ["/pause"]
    assert rpc_calls == [("set-heartbeats", {"enabled": True})]


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
