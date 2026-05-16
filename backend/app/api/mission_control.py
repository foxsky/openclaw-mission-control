from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import require_org_admin
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.session import async_session_maker
from app.models.agents import Agent
from app.models.boards import Board
from app.services.openclaw.gateway_rpc import GatewayConfig as GatewayClientConfig
from app.services.openclaw.gateway_rpc import openclaw_call
from app.services.openclaw.heartbeat_sweep import (
    _fetch_disabled_agent_ids,
    _fetch_paused_board_ids,
)
from app.services.openclaw.provisioning import _any_board_active_on_gateway

logger = get_logger(__name__)


async def _get_gateway_config_for_board(
    session: AsyncSession, board_id: UUID
) -> tuple[GatewayClientConfig | None, UUID | None]:
    """Return (gateway config, gateway_id) for a board.

    Gateway_id is returned alongside the config so the caller can pass
    it to ``_any_board_active_on_gateway`` to compute the correct
    multi-board ``set-heartbeats`` value without a second board lookup.
    Falls back to the known gateway URL when the gateway row has no
    URL configured.
    """
    from app.models.gateways import Gateway
    from app.services.openclaw.gateway_dispatch import GatewayDispatchService

    board = (await session.exec(select(Board).where(col(Board.id) == board_id))).first()
    logger.info(
        "pause/resume: board=%s gateway_id=%s", board_id, board.gateway_id if board else None
    )
    if not board or not board.gateway_id:
        logger.warning("pause/resume: no board or gateway_id")
        return None, None

    dispatch = GatewayDispatchService(session)
    config = await dispatch.optional_gateway_config_for_board(board)
    logger.info("pause/resume: dispatch.config=%s", config)

    # Fallback: if gateway has no URL configured, use the known gateway URL
    if config is None:
        gateway = (
            await session.exec(select(Gateway).where(col(Gateway.id) == board.gateway_id))
        ).first()
        logger.info(
            "pause/resume: gateway=%s url=%s token=%s",
            gateway.id if gateway else None,
            gateway.url if gateway else None,
            gateway.token[:10] if gateway and gateway.token else None,
        )
        if gateway:
            cfg = GatewayClientConfig(
                url="ws://192.168.2.60:18789",
                token=gateway.token or None,
                allow_insecure_tls=True,
            )
            logger.info("pause/resume: using fallback config")
            return cfg, board.gateway_id

    return config, board.gateway_id


# Router-level admin guard: every endpoint in this module mutates or
# enumerates production gateway state (pause/resume the heartbeat
# kill-switch, enumerate live agents). Pre-fix this router was mounted
# at app.main:625 without ANY auth dependency — any LAN-adjacent
# attacker could silence every agent. Admin scope is the right gate;
# member scope would be too permissive for the heartbeat kill-switch.
router = APIRouter(
    prefix="/api/mission-control",
    tags=["metrics"],
    dependencies=[Depends(require_org_admin)],
)


def _heartbeat_enabled(agent: Agent) -> bool:
    cfg = agent.heartbeat_config or {}
    every = str(cfg.get("every") or "").strip().lower()
    return bool(every and every != "0m")


@router.get("/heartbeats")
async def mission_control_heartbeats() -> dict[str, Any]:
    now = utcnow()
    async with async_session_maker() as session:
        agents = (await session.exec(select(Agent).order_by(col(Agent.name).asc()))).all()
        board_ids = {a.board_id for a in agents if a.board_id is not None}
        boards: dict[UUID, Board] = {}
        if board_ids:
            board_rows = (
                await session.exec(select(Board).where(col(Board.id).in_(board_ids)))
            ).all()
            boards = {b.id: b for b in board_rows}
        paused_boards = await _fetch_paused_board_ids(session)
        disabled_agents = await _fetch_disabled_agent_ids(session)

    monitored = []
    for agent in agents:
        if not _heartbeat_enabled(agent):
            continue
        is_paused = (
            agent.board_id is not None and agent.board_id in paused_boards
        ) or agent.id in disabled_agents
        deadline = agent.checkin_deadline_at
        monitored.append(
            {
                "agent_id": str(agent.id),
                "name": agent.name,
                "board_id": str(agent.board_id) if agent.board_id else None,
                "board_name": (boards[agent.board_id].name if agent.board_id in boards else None),
                "status": agent.status,
                "enabled": not is_paused,
                "last_seen_at": (
                    agent.last_seen_at.isoformat() + "Z" if agent.last_seen_at else None
                ),
                "checkin_deadline_at": deadline.isoformat() + "Z" if deadline else None,
                "wake_attempts": agent.wake_attempts,
                "last_wake_sent_at": (
                    agent.last_wake_sent_at.isoformat() + "Z" if agent.last_wake_sent_at else None
                ),
                "seconds_until_deadline": (
                    int((deadline - now).total_seconds()) if deadline else None
                ),
                # Paused agents are intentionally not heartbeating — don't surface
                # them as overdue even if their stale deadline is in the past.
                "overdue": bool(deadline and deadline < now and not is_paused),
                "is_board_lead": bool(agent.is_board_lead),
            }
        )

    monitored.sort(key=lambda item: ((not item["overdue"]), item["name"]))
    return {
        "ok": True,
        "generated_at": now.isoformat() + "Z",
        "agents_monitored": len(monitored),
        "agents": monitored,
    }


# ---------------------------------------------------------------------------
# Board pause/resume endpoints
# ---------------------------------------------------------------------------


@router.post("/boards/{board_id}/pause", status_code=200)
async def api_pause_board(board_id: str) -> dict[str, Any]:
    """Mark a board as paused — heartbeat monitor will skip nudge/wake for its agents.

    Also disables agent heartbeats in the gateway by calling set-heartbeats RPC
    for all agents assigned to this board.
    """
    if not board_id or len(board_id) > 256:
        raise HTTPException(status_code=422, detail="Invalid board_id")
    try:
        bid = uuid.UUID(board_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid board_id format")

    now = utcnow()
    async with async_session_maker() as session:
        await session.execute(text("""
            INSERT INTO board_pause_states (board_id, is_paused, paused_at, paused_by)
            VALUES (:board_id, TRUE, :paused_at, 'human')
            ON CONFLICT(board_id) DO UPDATE SET
                is_paused = TRUE, paused_at = EXCLUDED.paused_at, paused_by = 'human'
        """).bindparams(board_id=bid, paused_at=now))

        # Upsert heartbeat rows for all board agents and disable them (PostgreSQL state)
        await session.execute(text("""
            INSERT INTO agent_heartbeats (agent_id, enabled, last_status)
            SELECT id, FALSE, 'idle'
            FROM agents WHERE board_id = :board_id
            ON CONFLICT(agent_id) DO UPDATE SET enabled = FALSE, last_status = 'idle'
        """).bindparams(board_id=bid))

        # Get gateway config + id while session is still open
        config, gateway_id = await _get_gateway_config_for_board(session, bid)

        await session.commit()

    # Gateway `set-heartbeats` is global per gateway. Compute the value
    # from the post-commit pause state: keep the flag enabled if any
    # OTHER board on the same gateway is still active. This makes pause
    # safe in a multi-board-per-gateway setup — pausing Board A on a
    # shared gateway no longer silences Board B's agents.
    if config is not None and gateway_id is not None:
        should_enable = await _any_board_active_on_gateway(gateway_id)
        logger.info(
            "pause: calling gateway RPC any_board_active=%s gateway_id=%s",
            should_enable,
            gateway_id,
        )
        try:
            result = await openclaw_call(
                "set-heartbeats",
                {"enabled": should_enable},
                config=config,
            )
            logger.info("pause: set-heartbeats success enabled=%s result=%s", should_enable, result)
        except Exception as exc:
            logger.warning(
                "pause_board.set_heartbeats_failed board_id=%s enabled=%s error=%s",
                board_id,
                should_enable,
                str(exc),
                exc_info=True,
            )

    logger.info("board %s paused", board_id)
    return {"ok": True, "board_id": board_id, "is_paused": True, "paused_at": now}


@router.post("/boards/{board_id}/resume", status_code=200)
async def api_resume_board(board_id: str) -> dict[str, Any]:
    """Mark a board as resumed — heartbeat monitor resumes normal nudge/wake behaviour.

    Also re-enables agent heartbeats in the gateway by calling set-heartbeats RPC
    for all agents assigned to this board.
    """
    if not board_id or len(board_id) > 256:
        raise HTTPException(status_code=422, detail="Invalid board_id")
    try:
        bid = uuid.UUID(board_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid board_id format")

    async with async_session_maker() as session:
        await session.execute(text("""
            INSERT INTO board_pause_states (board_id, is_paused, paused_at, paused_by)
            VALUES (:board_id, FALSE, NULL, NULL)
            ON CONFLICT(board_id) DO UPDATE SET
                is_paused = FALSE, paused_at = NULL, paused_by = NULL
        """).bindparams(board_id=bid))

        # Upsert heartbeat rows for all board agents and enable them (PostgreSQL state)
        await session.execute(text("""
            INSERT INTO agent_heartbeats (agent_id, enabled, last_status)
            SELECT id, TRUE, 'idle'
            FROM agents WHERE board_id = :board_id
            ON CONFLICT(agent_id) DO UPDATE SET enabled = TRUE, last_status = 'idle'
        """).bindparams(board_id=bid))

        # Fetch gateway config + id while session is still open
        config, gateway_id = await _get_gateway_config_for_board(session, bid)

        await session.commit()

    # Gateway `set-heartbeats` is global per gateway. After this resume
    # commit there is at least one active board (the one just resumed),
    # so the flag should be enabled. We still query
    # ``_any_board_active_on_gateway`` for symmetry with pause and to
    # stay correct against any concurrent pause activity.
    if config is not None and gateway_id is not None:
        should_enable = await _any_board_active_on_gateway(gateway_id)
        try:
            result = await openclaw_call(
                "set-heartbeats",
                {"enabled": should_enable},
                config=config,
            )
            logger.info(
                "resume: set-heartbeats success enabled=%s result=%s", should_enable, result
            )
        except Exception as exc:
            logger.warning(
                "resume_board.set_heartbeats_failed board_id=%s enabled=%s error=%s",
                board_id,
                should_enable,
                str(exc),
                exc_info=True,
            )

    logger.info("board %s resumed", board_id)
    return {"ok": True, "board_id": board_id, "is_paused": False}


@router.get("/boards/{board_id}/pause", status_code=200)
async def api_get_board_pause_state(board_id: str) -> dict[str, Any]:
    """Return the current pause state for a board."""
    if not board_id or len(board_id) > 256:
        raise HTTPException(status_code=422, detail="Invalid board_id")
    try:
        bid = uuid.UUID(board_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid board_id format")

    async with async_session_maker() as session:
        result = await session.execute(text("""
            SELECT is_paused, paused_at, paused_by FROM board_pause_states WHERE board_id = :board_id
        """).bindparams(board_id=bid))
        row = result.first()

    if row is None:
        return {"board_id": board_id, "is_paused": False, "paused_at": None, "paused_by": None}
    return {
        "board_id": board_id,
        "is_paused": bool(row[0]),
        "paused_at": row[1],
        "paused_by": row[2],
    }
