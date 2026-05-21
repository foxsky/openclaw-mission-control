"""Thin gateway session-inspection API wrappers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import col, select

from app.api.deps import require_org_admin
from app.core.auth import AuthContext, get_auth_context
from app.db.session import get_session
from app.models.agents import Agent
from app.models.gateways import Gateway
from app.schemas.common import OkResponse
from app.schemas.gateway_api import (
    ConfigSchemaLookupResponse,
    GatewayCommandsResponse,
    GatewayEvalApprovalResolveRequest,
    GatewayEvalSessionEnsureRequest,
    GatewayResolveQuery,
    GatewaySessionHistoryResponse,
    GatewaySessionMessageRequest,
    GatewaySessionResponse,
    GatewaySessionsResponse,
    GatewaysStatusResponse,
    OpenClawRuntimeStatusResponse,
    ProjectedGatewaySession,
    ProjectedGatewaySessionsResponse,
)
from app.services.mc_gateway_subscriber.session_state_repo import (
    list_session_states_for_agent_ids,
)
from app.services.openclaw.admin_service import GatewayAdminLifecycleService
from app.services.openclaw.config_lookup_cache import ConfigLookupCache
from app.services.openclaw.gateway_resolver import gateway_client_config
from app.services.openclaw.gateway_rpc import (
    GATEWAY_EVENTS,
    GATEWAY_METHODS,
    PROTOCOL_VERSION,
    OpenClawGatewayError,
    openclaw_call,
)
from app.services.openclaw.internal.agent_key import projection_lookup_id
from app.services.openclaw.runtime_status import collect_openclaw_status
from app.services.openclaw.session_service import GatewaySessionService
from app.services.organizations import OrganizationContext

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(prefix="/gateways", tags=["gateways"])
SESSION_DEP = Depends(get_session)
AUTH_DEP = Depends(get_auth_context)
ORG_ADMIN_DEP = Depends(require_org_admin)
BOARD_ID_QUERY = Query(default=None)

_MAX_CONFIG_LOOKUP_PATH_LEN = 512


def _validate_config_lookup_path(raw: str) -> str:
    """Cheap pre-validation; lets the gateway parser be authoritative on grammar.

    Rejects only empty/oversize/control-char input so the WS RPC never sees
    obviously-bad payloads. Bracket-quoted keys, dotted paths, and the root
    sentinel `.` all pass through unchanged.
    """
    trimmed = raw.strip()
    if not trimmed or len(trimmed) > _MAX_CONFIG_LOOKUP_PATH_LEN:
        raise HTTPException(status_code=400, detail={"error": "invalid_path"})
    if any(ord(ch) < 0x20 for ch in trimmed):
        raise HTTPException(status_code=400, detail={"error": "invalid_path"})
    return trimmed


_CONFIG_LOOKUP_CACHE = ConfigLookupCache(ttl_seconds=30.0)
_CONFIG_LOOKUP_RPC_TIMEOUT_SECONDS = 5.0


def _query_to_resolve_input(
    board_id: str | None = Query(default=None),
    gateway_url: str | None = Query(default=None),
    gateway_token: str | None = Query(default=None),
    gateway_disable_device_pairing: bool | None = Query(default=None),
    gateway_allow_insecure_tls: bool | None = Query(default=None),
) -> GatewayResolveQuery:
    return GatewaySessionService.to_resolve_query(
        board_id=board_id,
        gateway_url=gateway_url,
        gateway_token=gateway_token,
        gateway_disable_device_pairing=gateway_disable_device_pairing,
        gateway_allow_insecure_tls=gateway_allow_insecure_tls,
    )


RESOLVE_INPUT_DEP = Depends(_query_to_resolve_input)


@router.get("/status", response_model=GatewaysStatusResponse)
async def gateways_status(
    params: GatewayResolveQuery = RESOLVE_INPUT_DEP,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewaysStatusResponse:
    """Return gateway connectivity and session status."""
    service = GatewaySessionService(session)
    return await service.get_status(
        params=params,
        organization_id=ctx.organization.id,
        user=auth.user,
    )


@router.get("/sessions", response_model=GatewaySessionsResponse)
async def list_gateway_sessions(
    board_id: str | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewaySessionsResponse:
    """List sessions for a gateway associated with a board."""
    service = GatewaySessionService(session)
    return await service.get_sessions(
        board_id=board_id,
        organization_id=ctx.organization.id,
        user=auth.user,
    )


@router.get("/sessions/{session_id}", response_model=GatewaySessionResponse)
async def get_gateway_session(
    session_id: str,
    board_id: str | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewaySessionResponse:
    """Get a specific gateway session by key."""
    service = GatewaySessionService(session)
    return await service.get_session(
        session_id=session_id,
        board_id=board_id,
        organization_id=ctx.organization.id,
        user=auth.user,
    )


@router.get("/sessions/{session_id}/history", response_model=GatewaySessionHistoryResponse)
async def get_session_history(
    session_id: str,
    board_id: str | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewaySessionHistoryResponse:
    """Fetch chat history for a gateway session."""
    service = GatewaySessionService(session)
    return await service.get_session_history(
        session_id=session_id,
        board_id=board_id,
        organization_id=ctx.organization.id,
        user=auth.user,
    )


@router.post("/sessions/{session_id}/message", response_model=OkResponse)
async def send_gateway_session_message(
    session_id: str,
    payload: GatewaySessionMessageRequest,
    board_id: str | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OkResponse:
    """Send a message into a specific gateway session."""
    service = GatewaySessionService(session)
    await service.send_session_message(
        session_id=session_id,
        payload=payload,
        board_id=board_id,
        organization_id=ctx.organization.id,
        user=auth.user,
    )
    return OkResponse()


@router.post("/evals/sessions/{session_id}", response_model=GatewaySessionResponse)
async def ensure_eval_gateway_session(
    session_id: str,
    payload: GatewayEvalSessionEnsureRequest,
    board_id: str | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewaySessionResponse:
    """Create or reset an isolated eval session for a board gateway."""
    service = GatewaySessionService(session)
    return await service.ensure_eval_session(
        session_id=session_id,
        payload=payload,
        board_id=board_id,
        organization_id=ctx.organization.id,
        user=auth.user,
    )


@router.get("/evals/sessions/{session_id}/history", response_model=GatewaySessionHistoryResponse)
async def get_eval_gateway_session_history(
    session_id: str,
    board_id: str | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewaySessionHistoryResponse:
    """Fetch chat history for an isolated eval session."""
    service = GatewaySessionService(session)
    return await service.get_eval_session_history(
        session_id=session_id,
        board_id=board_id,
        organization_id=ctx.organization.id,
        user=auth.user,
    )


@router.post("/evals/sessions/{session_id}/message", response_model=OkResponse)
async def send_eval_gateway_session_message(
    session_id: str,
    payload: GatewaySessionMessageRequest,
    board_id: str | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OkResponse:
    """Send a prompt into an isolated eval session."""
    service = GatewaySessionService(session)
    await service.send_eval_session_message(
        session_id=session_id,
        payload=payload,
        board_id=board_id,
        organization_id=ctx.organization.id,
        user=auth.user,
    )
    return OkResponse()


@router.post("/evals/sessions/{session_id}/approvals/resolve", response_model=OkResponse)
async def resolve_eval_gateway_session_approval(
    session_id: str,
    payload: GatewayEvalApprovalResolveRequest,
    board_id: str | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OkResponse:
    """Resolve a pending exec approval inside an isolated eval session."""
    service = GatewaySessionService(session)
    await service.resolve_eval_session_exec_approval(
        session_id=session_id,
        payload=payload,
        board_id=board_id,
        organization_id=ctx.organization.id,
        user=auth.user,
    )
    return OkResponse()


@router.delete("/evals/sessions/{session_id}", response_model=OkResponse)
async def delete_eval_gateway_session(
    session_id: str,
    board_id: str | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OkResponse:
    """Delete an isolated eval session."""
    service = GatewaySessionService(session)
    await service.delete_eval_session(
        session_id=session_id,
        board_id=board_id,
        organization_id=ctx.organization.id,
        user=auth.user,
    )
    return OkResponse()


@router.get("/commands", response_model=GatewayCommandsResponse)
async def gateway_commands(
    _auth: AuthContext = AUTH_DEP,
    _ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewayCommandsResponse:
    """Return supported gateway protocol methods and events."""
    return GatewayCommandsResponse(
        protocol_version=PROTOCOL_VERSION,
        methods=GATEWAY_METHODS,
        events=GATEWAY_EVENTS,
    )


@router.get("/runtime/status", response_model=OpenClawRuntimeStatusResponse)
async def openclaw_runtime_status(
    _auth: AuthContext = AUTH_DEP,
    _ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OpenClawRuntimeStatusResponse:
    """Return the local OpenClaw runtime status snapshot."""

    snapshot = await collect_openclaw_status()
    return OpenClawRuntimeStatusResponse(
        ok=snapshot.ok,
        status=snapshot.payload,
        error=snapshot.error,
        return_code=snapshot.return_code,
    )


@router.get(
    "/projected-sessions",
    response_model=ProjectedGatewaySessionsResponse,
)
async def projected_gateway_sessions(
    agent_id: str | None = Query(default=None),
    session: AsyncSession = SESSION_DEP,
    _auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> ProjectedGatewaySessionsResponse:
    """Return projected gateway session state for the caller's
    organization. Scoping rule: a projection row appears only if its
    ``agent_id`` matches the strict-parse of ``openclaw_session_id``
    on an MC ``agents`` row joined to the caller's org via
    ``Agent → Gateway → Organization``. The optional ``agent_id``
    query param is intersected with that set, so callers cannot
    widen across orgs by guessing identifiers.

    Rows for board leads (``lead-<board_id>``) and gateway-internal
    workers (``mc-gateway-<gateway_id>``) ARE returned when the
    operator has registered them as MC agents under the caller's
    gateway — they are org-bound by virtue of the agent registration.
    Projection rows with no matching Agent row in the caller's org
    are dropped, regardless of prefix.
    """
    # Agents have no organization_id directly; scope via the gateway
    # they're paired against (Agent.gateway_id → Gateway.organization_id).
    stmt = (
        select(Agent)
        .join(Gateway, col(Gateway.id) == col(Agent.gateway_id))
        .where(col(Gateway.organization_id) == ctx.organization.id)
    )
    result = await session.exec(stmt)
    org_agents = list(result.all())
    # Strict parser only — never fall back to slugify(agent.name).
    # Codex finding: a slug fallback can collide with a real
    # gateway-emitted agent_id from an UNRELATED org's session and
    # leak the row through the org-scoping check.
    org_gateway_ids = {
        lookup for a in org_agents if (lookup := projection_lookup_id(a)) is not None
    }
    if agent_id is not None:
        org_gateway_ids = org_gateway_ids & {agent_id}
    rows = await list_session_states_for_agent_ids(session, agent_ids=org_gateway_ids)
    return ProjectedGatewaySessionsResponse(
        sessions=[
            ProjectedGatewaySession.model_validate(row, from_attributes=True) for row in rows
        ],
    )


_GATEWAY_METHOD_REQUIRED_VERSION = "2026.5.19"


def _map_gateway_error(exc: OpenClawGatewayError, path: str) -> HTTPException:
    """Translate an OpenClawGatewayError into a structured HTTPException."""

    details: dict[str, Any] = exc.details if isinstance(exc.details, dict) else {}
    code = str(details.get("code") or "").upper()
    message = str(details.get("message") or str(exc) or "")
    lowered = message.lower()

    if code == "INVALID_REQUEST":
        if message == "config schema path not found":
            return HTTPException(
                status_code=404,
                detail={"error": "path_not_found", "path": path},
            )
        return HTTPException(
            status_code=422,
            detail={"error": "gateway_rejected_request", "detail": message},
        )
    if (
        code in {"METHOD_NOT_FOUND", "METHOD_NOT_REGISTERED", "NOT_IMPLEMENTED"}
        or "method not found" in lowered
        or "unknown method" in lowered
    ):
        return HTTPException(
            status_code=501,
            detail={
                "error": "method_unsupported",
                "requires_gateway_version": _GATEWAY_METHOD_REQUIRED_VERSION,
            },
        )
    if code == "UNAVAILABLE":
        return HTTPException(
            status_code=503,
            detail={"error": "gateway_unavailable", "detail": message},
        )
    return HTTPException(
        status_code=503,
        detail={"error": "gateway_unreachable", "detail": message},
    )


@router.get(
    "/{gateway_id}/config/lookup",
    response_model=ConfigSchemaLookupResponse,
    response_model_by_alias=True,
    operation_id="gateway_config_lookup",
)
async def gateway_config_lookup(
    gateway_id: UUID,
    path: str = Query(..., min_length=1, max_length=512),
    session: AsyncSession = SESSION_DEP,
    _auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> ConfigSchemaLookupResponse:
    """Look up gateway config schema + reload metadata for a single path."""

    trimmed_path = _validate_config_lookup_path(path)

    gateway = await GatewayAdminLifecycleService(session).require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    cfg = gateway_client_config(gateway)

    async def _load() -> object:
        return await asyncio.wait_for(
            openclaw_call(
                "config.schema.lookup",
                {"path": trimmed_path},
                config=cfg,
            ),
            timeout=_CONFIG_LOOKUP_RPC_TIMEOUT_SECONDS,
        )

    try:
        payload = await _CONFIG_LOOKUP_CACHE.get_or_load(
            (gateway_id, trimmed_path), _load,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504, detail={"error": "gateway_timeout"},
        ) from exc
    except OpenClawGatewayError as exc:
        raise _map_gateway_error(exc, trimmed_path) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail={"error": "gateway_invalid_payload"},
        )
    return ConfigSchemaLookupResponse.model_validate({**payload, "gateway_id": gateway_id})
