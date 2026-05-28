"""Thin gateway session-inspection API wrappers."""

from __future__ import annotations

import asyncio
import hashlib
import socket
from typing import TYPE_CHECKING, Any, TypedDict
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlmodel import col, select

from app.api.deps import require_org_admin
from app.core.auth import AuthContext, get_auth_context
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.agents import Agent
from app.models.gateways import Gateway
from app.schemas.common import OkResponse
from app.schemas.gateway_api import (
    ConfigSchemaLookupResponse,
    GatewayCommandsResponse,
    GatewayDevice,
    GatewayDeviceListResponse,
    GatewayEvalApprovalResolveRequest,
    GatewayEvalSessionEnsureRequest,
    GatewayObservabilityErrorRatesResponse,
    GatewayObservabilitySamplePoint,
    GatewayResolveQuery,
    GatewaySessionHistoryResponse,
    GatewaySessionMessageRequest,
    GatewaySessionResponse,
    GatewaySessionsResponse,
    GatewaysStatusResponse,
    OpenClawRuntimeStatusResponse,
    ProjectedGatewaySession,
    ProjectedGatewaySessionsResponse,
    RemoveGatewayDeviceResponse,
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
    GatewayConfig,
    OpenClawGatewayError,
    openclaw_call,
)
from app.services.openclaw.internal.agent_key import projection_lookup_id
from app.services.openclaw.runtime_status import collect_openclaw_status
from app.services.openclaw.session_service import GatewaySessionService
from app.services.organizations import OrganizationContext

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

logger = get_logger(__name__)

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


def _project_gateway_device(
    raw: dict[str, Any],
    *,
    self_device_ids: set[str],
) -> GatewayDevice:
    """Flatten the gateway's per-device dict into the wire shape MC exposes.

    Combines all token-level ``scopes`` into a single union, computes the
    most-recent ``lastUsedAtMs`` across tokens, and marks ``is_self`` when
    ``raw.deviceId`` is in ``self_device_ids`` (the precomputed set of
    MC-owned devices identified by IP+clientId+clientMode heuristic).
    """

    tokens = raw.get("tokens") or []
    if not isinstance(tokens, list):
        tokens = []

    scopes_union: set[str] = set()
    for s in raw.get("scopes") or []:
        if isinstance(s, str):
            scopes_union.add(s)
    last_used: int | None = None
    for t in tokens:
        if not isinstance(t, dict):
            continue
        for s in t.get("scopes") or []:
            if isinstance(s, str):
                scopes_union.add(s)
        ts = t.get("lastUsedAtMs")
        if isinstance(ts, int) and (last_used is None or ts > last_used):
            last_used = ts

    device_id = str(raw.get("deviceId") or "")
    return GatewayDevice.model_validate(
        {
            **raw,
            "scopes": sorted(scopes_union),
            "tokenCount": len(tokens),
            "lastUsedAtMs": last_used,
            "isSelf": device_id in self_device_ids,
            # tokens[] is not declared on GatewayDevice; Pydantic ignores extras.
        }
    )


def _gateway_connection_fingerprint(cfg: GatewayConfig) -> str:
    """Stable per-connection fingerprint so the cache invalidates when the
    gateway target changes within the TTL window.

    The Gateway row's ``updated_at`` is not bumped on PATCH (no SQL
    ``onupdate=`` and ``apply_updates`` does not touch it), so we cannot
    rely on it. Hash the connection-identifying fields of the resolved
    ``GatewayConfig`` instead; generic tunables are intentionally
    excluded so they do not perturb the cache.
    """
    h = hashlib.sha256()
    h.update(cfg.url.encode("utf-8"))
    h.update(b"\x00")
    h.update(str(cfg.allow_insecure_tls).encode("utf-8"))
    h.update(b"\x00")
    h.update(str(cfg.disable_device_pairing).encode("utf-8"))
    h.update(b"\x00")
    h.update((cfg.token or "").encode("utf-8"))
    return h.hexdigest()[:16]


_CONFIG_LOOKUP_CACHE = ConfigLookupCache(ttl_seconds=30.0)
_CONFIG_LOOKUP_RPC_TIMEOUT_SECONDS = 5.0
_PAIRING_RPC_TIMEOUT_SECONDS = 5.0

# Shared by the two DELETE error-mapping paths (self-protect list + remove)
# so a translation added in one stays consistent with the other.
_PAIRING_OUTCOME_MAP = {
    "device_not_found": "device_not_found",
    "gateway_pairing_scope_denied": "scope_denied",
    "gateway_unavailable": "gateway_unavailable",
    "gateway_unreachable": "gateway_unreachable",
    "gateway_rejected_request": "gateway_rejected_request",
    "method_unsupported": "method_unsupported",
}


def _detect_outbound_ip_to(host: str, port: int) -> str | None:
    """Detect MC's kernel-chosen source IP for connections to (host, port).

    Uses a DGRAM socket connect (which sets routing context but sends nothing)
    so getsockname() returns the source IP the kernel would pick. Returns None
    on any OSError (DNS failure, no route, etc.).
    """

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            sock.connect((host, port))
            # getsockname() is typed as Any (depends on family); narrow to str.
            return str(sock.getsockname()[0])
    except OSError:
        return None


def _select_self_device_ids(paired: list[Any], self_match_ip: str | None) -> set[str]:
    """Pick the deviceIds whose IP+clientId+clientMode signal MC's own device.

    Returns an empty set if ``self_match_ip`` is None (autodetect failed and
    no override). Multiple matches are intentionally retained — the DELETE
    handler refuses all of them, which is conservative against ambiguity.
    """

    if self_match_ip is None:
        return set()
    matched: set[str] = set()
    for raw_device in paired:
        if not isinstance(raw_device, dict):
            continue
        if (
            raw_device.get("clientId") == "gateway-client"
            and raw_device.get("clientMode") == "backend"
            and raw_device.get("remoteIp") == self_match_ip
        ):
            did = str(raw_device.get("deviceId") or "")
            if did:
                matched.add(did)
    return matched


def _resolve_self_match_ip(cfg: GatewayConfig) -> str | None:
    """Return MC's outbound source IP for the gateway, or None if undetectable.

    Used by the pairings page to mark a paired device as ``isSelf`` when its
    ``remoteIp`` matches and clientId/clientMode are MC-shaped.

    Resolution order:
    1. Explicit override via ``settings.gateway_client_outbound_ip`` (env
       ``GATEWAY_CLIENT_OUTBOUND_IP``).
    2. Autodetect via DGRAM socket connect to the gateway host:port.
    3. None — caller surfaces ``isSelfResolved: false`` and refuses writes.

    This replaces the earlier ``load_or_create_device_identity`` approach,
    which read a stable local Ed25519 keypair that turned out not to match the
    gateway's view of MC's paired device — see
    ``project_mc_pairings_page.md`` memory for the 2026-05-25 incident.
    """

    if settings.gateway_client_outbound_ip:
        return settings.gateway_client_outbound_ip
    parsed = urlparse(cfg.url)
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
    return _detect_outbound_ip_to(host, port)


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


class _GatewayErrorFields(TypedDict):
    """Canonical fields extracted from an ``OpenClawGatewayError`` for HTTP mapping."""

    code: str
    message: str
    lowered: str
    request_id: str | None
    is_method_unsupported: bool


def _map_gateway_error_common(exc: OpenClawGatewayError) -> _GatewayErrorFields:
    """Canonical OpenClawGatewayError -> mapper-fields extraction."""

    details: dict[str, Any] = exc.details if isinstance(exc.details, dict) else {}
    code = str(details.get("code") or "").upper()
    message = str(details.get("message") or str(exc) or "")
    lowered = message.lower()
    is_method_unsupported = (
        code in {"METHOD_NOT_FOUND", "METHOD_NOT_REGISTERED", "NOT_IMPLEMENTED"}
        or "method not found" in lowered
        or "unknown method" in lowered
    )
    return {
        "code": code,
        "message": message,
        "lowered": lowered,
        "request_id": exc.request_id,
        "is_method_unsupported": is_method_unsupported,
    }


def _map_config_lookup_error(
    exc: OpenClawGatewayError,
    path: str,
) -> HTTPException:
    """Translate an OpenClawGatewayError from config.schema.lookup into HTTP."""

    fields = _map_gateway_error_common(exc)
    code = fields["code"]
    message = fields["message"]

    logger.warning(
        "gateway.config_lookup.failed path=%r code=%s request_id=%s message=%s",
        path,
        code or "<none>",
        fields["request_id"] or "<none>",
        message or "<none>",
    )

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
    if fields["is_method_unsupported"]:
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


def _map_pairing_error(
    exc: OpenClawGatewayError,
    *,
    device_id: str,
) -> HTTPException:
    """Translate an OpenClawGatewayError from a device.pair.* RPC into HTTP."""

    fields = _map_gateway_error_common(exc)
    code = fields["code"]
    message = fields["message"]
    lowered = fields["lowered"]

    logger.warning(
        "gateway.pairing.failed device_id=%s code=%s request_id=%s message=%s",
        device_id,
        code or "<none>",
        fields["request_id"] or "<none>",
        message or "<none>",
    )

    if code == "INVALID_REQUEST":
        if "device not found" in lowered or "unknown device" in lowered:
            return HTTPException(
                status_code=404,
                detail={"error": "device_not_found", "device_id": device_id},
            )
        if (
            "insufficient scope" in lowered
            or "missing scope" in lowered
            or "removal denied" in lowered
        ):
            return HTTPException(
                status_code=403,
                detail={"error": "gateway_pairing_scope_denied"},
            )
        return HTTPException(
            status_code=422,
            detail={"error": "gateway_rejected_request", "detail": message},
        )
    if fields["is_method_unsupported"]:
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
    fingerprint = _gateway_connection_fingerprint(cfg)

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
            (gateway_id, trimmed_path, fingerprint),
            _load,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail={"error": "gateway_timeout"},
        ) from exc
    except OpenClawGatewayError as exc:
        raise _map_config_lookup_error(exc, trimmed_path) from exc
    if not isinstance(payload, dict):
        logger.error(
            "gateway.config_lookup.invalid_payload gateway_id=%s path=%r type=%s",
            gateway_id,
            trimmed_path,
            type(payload).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "gateway_invalid_payload"},
        )
    try:
        return ConfigSchemaLookupResponse.model_validate(
            {**payload, "gateway_id": gateway_id},
        )
    except ValidationError as exc:
        logger.error(
            "gateway.config_lookup.invalid_payload_shape gateway_id=%s path=%r errors=%s",
            gateway_id,
            trimmed_path,
            exc.errors(include_url=False, include_input=False),
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "gateway_invalid_payload"},
        ) from exc


@router.get(
    "/{gateway_id}/devices",
    response_model=GatewayDeviceListResponse,
    response_model_by_alias=True,
    operation_id="list_gateway_devices",
)
async def list_gateway_devices(
    gateway_id: UUID,
    session: AsyncSession = SESSION_DEP,
    _auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewayDeviceListResponse:
    """List devices paired with the gateway, with self-protect marking."""

    gateway = await GatewayAdminLifecycleService(session).require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    cfg = gateway_client_config(gateway)
    self_match_ip = _resolve_self_match_ip(cfg)

    try:
        payload = await asyncio.wait_for(
            openclaw_call("device.pair.list", config=cfg),
            timeout=_PAIRING_RPC_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail={"error": "gateway_timeout"},
        ) from exc
    except OpenClawGatewayError as exc:
        fields = _map_gateway_error_common(exc)
        logger.warning(
            "gateway.pairing.list.failed code=%s request_id=%s message=%s",
            fields["code"] or "<none>",
            fields["request_id"] or "<none>",
            fields["message"] or "<none>",
        )
        if fields["is_method_unsupported"]:
            raise HTTPException(
                status_code=501,
                detail={
                    "error": "method_unsupported",
                    "requires_gateway_version": _GATEWAY_METHOD_REQUIRED_VERSION,
                },
            ) from exc
        if fields["code"] == "UNAVAILABLE":
            raise HTTPException(
                status_code=503,
                detail={"error": "gateway_unavailable", "detail": fields["message"]},
            ) from exc
        raise HTTPException(
            status_code=503,
            detail={"error": "gateway_unreachable", "detail": fields["message"]},
        ) from exc
    if not isinstance(payload, dict):
        logger.error(
            "gateway.pairing.list.invalid_payload gateway_id=%s type=%s",
            gateway_id,
            type(payload).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "gateway_invalid_payload"},
        )
    paired = payload.get("paired")
    if not isinstance(paired, list):
        logger.error(
            "gateway.pairing.list.invalid_payload gateway_id=%s paired_type=%s",
            gateway_id,
            type(paired).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "gateway_invalid_payload"},
        )

    self_device_ids = _select_self_device_ids(paired, self_match_ip)

    try:
        devices = [
            _project_gateway_device(raw, self_device_ids=self_device_ids)
            for raw in paired
            if isinstance(raw, dict)
        ]
    except ValidationError as exc:
        logger.error(
            "gateway.pairing.list.projection_failed gateway_id=%s errors=%s",
            gateway_id,
            exc.errors(include_url=False, include_input=False),
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "gateway_invalid_payload"},
        ) from exc

    return GatewayDeviceListResponse(
        gateway_id=gateway_id,
        devices=devices,
        is_self_resolved=self_match_ip is not None,
    )


@router.delete(
    "/{gateway_id}/devices/{device_id}",
    response_model=RemoveGatewayDeviceResponse,
    response_model_by_alias=True,
    operation_id="remove_gateway_device",
)
async def remove_gateway_device(
    gateway_id: UUID,
    device_id: str,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> RemoveGatewayDeviceResponse:
    """Revoke a paired device from the gateway, with self-protect."""

    user_id = auth.user.id if auth.user else None

    logger.info(
        "gateway.pairing.remove.attempt user_id=%s org_id=%s gateway_id=%s device_id=%s",
        user_id,
        ctx.organization.id,
        gateway_id,
        device_id,
    )

    def _audit(outcome: str, gateway_request_id: str | None = None) -> None:
        logger.info(
            "gateway.pairing.remove.outcome user_id=%s org_id=%s gateway_id=%s "
            "device_id=%s outcome=%s gateway_request_id=%s",
            user_id,
            ctx.organization.id,
            gateway_id,
            device_id,
            outcome,
            gateway_request_id or "<none>",
        )

    gateway = await GatewayAdminLifecycleService(session).require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    cfg = gateway_client_config(gateway)

    # Self-protect: identify MC's own paired devices by the heuristic
    # IP+clientId+clientMode match. We must enumerate the gateway's view first
    # (two RPC round-trips for one DELETE) because no whoami RPC exposes the
    # current connection's deviceId — see project_mc_pairings_page memory.
    self_match_ip = _resolve_self_match_ip(cfg)
    if self_match_ip is None:
        _audit("self_identity_unavailable")
        raise HTTPException(
            status_code=503,
            detail={"error": "self_identity_unavailable"},
        )

    try:
        list_payload = await asyncio.wait_for(
            openclaw_call("device.pair.list", config=cfg),
            timeout=_PAIRING_RPC_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        _audit("gateway_timeout")
        raise HTTPException(
            status_code=504,
            detail={"error": "gateway_timeout"},
        ) from exc
    except OpenClawGatewayError as exc:
        http_exc = _map_pairing_error(exc, device_id=device_id)
        detail_error = http_exc.detail.get("error") if isinstance(http_exc.detail, dict) else None
        outcome = _PAIRING_OUTCOME_MAP.get(detail_error, "other") if detail_error else "other"
        _audit(outcome, gateway_request_id=exc.request_id)
        raise http_exc from exc

    if not isinstance(list_payload, dict):
        _audit("gateway_invalid_payload")
        raise HTTPException(
            status_code=502,
            detail={"error": "gateway_invalid_payload"},
        )
    paired = list_payload.get("paired") or []
    if not isinstance(paired, list):
        _audit("gateway_invalid_payload")
        raise HTTPException(
            status_code=502,
            detail={"error": "gateway_invalid_payload"},
        )

    self_device_ids = _select_self_device_ids(paired, self_match_ip)
    if not self_device_ids:
        # Heuristic resolved an outbound IP but no paired device matched
        # clientId=gateway-client + clientMode=backend + remoteIp=<our IP>.
        # Could be NAT, remoteIp=null on MC's row, or MC not currently a paired
        # backend client. Refuse writes — operator must opt-in via
        # GATEWAY_CLIENT_OUTBOUND_IP override or accept the risk explicitly.
        _audit("self_identity_unavailable")
        raise HTTPException(
            status_code=503,
            detail={"error": "self_identity_unavailable"},
        )
    if device_id in self_device_ids:
        _audit("cannot_remove_self")
        raise HTTPException(
            status_code=409,
            detail={"error": "cannot_remove_self", "device_id": device_id},
        )

    try:
        # Param shape verified by Task 1 probe (2026-05-23): {"deviceId": ...};
        # {"id": ...} and {"device": ...} both return INVALID_REQUEST.
        await asyncio.wait_for(
            openclaw_call("device.pair.remove", {"deviceId": device_id}, config=cfg),
            timeout=_PAIRING_RPC_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        _audit("gateway_timeout")
        raise HTTPException(
            status_code=504,
            detail={"error": "gateway_timeout"},
        ) from exc
    except OpenClawGatewayError as exc:
        http_exc = _map_pairing_error(exc, device_id=device_id)
        detail_error = http_exc.detail.get("error") if isinstance(http_exc.detail, dict) else None
        outcome = _PAIRING_OUTCOME_MAP.get(detail_error, "other") if detail_error else "other"
        _audit(outcome, gateway_request_id=exc.request_id)
        raise http_exc from exc

    _audit("success")
    return RemoveGatewayDeviceResponse(device_id=device_id)


_OBSERVABILITY_WINDOW_PATTERN = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
}


def _parse_observability_window(raw: str) -> int:
    """Map a window string (``1h``/``24h``/etc) to seconds; raise 400 otherwise."""

    seconds = _OBSERVABILITY_WINDOW_PATTERN.get(raw)
    if seconds is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_window", "allowed": sorted(_OBSERVABILITY_WINDOW_PATTERN)},
        )
    return seconds


@router.get(
    "/{gateway_id}/observability/error-rates",
    response_model=GatewayObservabilityErrorRatesResponse,
    response_model_by_alias=True,
    operation_id="get_gateway_observability_error_rates",
)
async def get_gateway_observability_error_rates(
    gateway_id: UUID,
    window: str = Query(default="1h"),
    session: AsyncSession = SESSION_DEP,
    _auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewayObservabilityErrorRatesResponse:
    """Return persisted error-rate samples written by the gateway
    observability poller, filtered to a recent time window."""

    from datetime import UTC, timedelta

    from app.core.time import as_naive_utc, utcnow
    from app.models.gateway_observability_samples import GatewayObservabilitySample

    window_seconds = _parse_observability_window(window)

    # Validate gateway scope (404 if not in the caller's org).
    await GatewayAdminLifecycleService(session).require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )

    cutoff = utcnow() - timedelta(seconds=window_seconds)
    statement = (
        select(GatewayObservabilitySample)
        .where(GatewayObservabilitySample.gateway_id == gateway_id)
        .where(GatewayObservabilitySample.scraped_at >= cutoff)
        .order_by(GatewayObservabilitySample.scraped_at.desc())  # type: ignore[arg-type]
    )
    rows = (await session.exec(statement)).all()
    points = [
        GatewayObservabilitySamplePoint(
            metric_name=row.metric_name,
            labels=row.labels,
            counter_value=row.counter_value,
            rate_per_second=row.rate_per_second,
            elapsed_seconds=row.elapsed_seconds,
            # ``scraped_at`` is naive UTC by project convention
            # (see app/core/time.utcnow). Plain ``.timestamp()`` on a
            # naive datetime assumes local timezone, which would shift
            # epoch ms by the host's UTC offset. Re-tag as UTC first.
            scraped_at_ms=int(as_naive_utc(row.scraped_at).replace(tzinfo=UTC).timestamp() * 1000),
        )
        for row in rows
    ]
    return GatewayObservabilityErrorRatesResponse(
        gateway_id=gateway_id,
        window_seconds=window_seconds,
        samples=points,
    )
