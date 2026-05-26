# ruff: noqa: INP001
"""Integration tests for /api/v1/gateways/{id}/devices."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import gateway as gateway_api
from app.api.deps import require_org_admin
from app.api.gateway import router as gateway_router
from app.core.auth import AuthContext, get_auth_context
from app.db.session import get_session
from app.models.gateways import Gateway
from app.models.organization_members import OrganizationMember
from app.models.organizations import Organization
from app.models.users import User
from app.services.openclaw.gateway_rpc import OpenClawGatewayError
from app.services.organizations import OrganizationContext

# Stand-in for MC's outbound source IP to the gateway. The self-protect
# heuristic marks a paired device as ``isSelf`` when its ``remoteIp`` equals
# this value AND clientId="gateway-client" AND clientMode="backend".
_LOCAL_BACKEND_IP = "192.168.2.64"
_LOCAL_DEVICE_ID = "mc-self-device-id"


def _build_app(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    organization: Organization,
) -> FastAPI:
    app = FastAPI()
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(gateway_router)
    app.include_router(api_v1)

    async def _override_get_session() -> AsyncSession:
        async with session_maker() as session:
            yield session

    async def _override_require_org_admin() -> OrganizationContext:
        return OrganizationContext(
            organization=organization,
            member=OrganizationMember(
                organization_id=organization.id,
                user_id=uuid4(),
                role="owner",
                all_boards_read=True,
                all_boards_write=True,
            ),
        )

    async def _override_get_auth_context() -> AuthContext:
        return AuthContext(
            actor_type="user",
            user=User(id=uuid4(), clerk_user_id="test-user", email="op@example.local"),
        )

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[require_org_admin] = _override_require_org_admin
    app.dependency_overrides[get_auth_context] = _override_get_auth_context
    return app


@pytest_asyncio.fixture
async def setup() -> AsyncIterator[tuple[FastAPI, Organization, Gateway]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    org = Organization(id=uuid4(), name="Org One")
    gateway = Gateway(
        id=uuid4(),
        organization_id=org.id,
        name="GW",
        url="https://gateway.example.local",
        workspace_root="/workspace/openclaw",
    )
    async with sm() as session:
        session.add(org)
        session.add(gateway)
        await session.commit()
    app = _build_app(sm, organization=org)
    try:
        yield app, org, gateway
    finally:
        await engine.dispose()


def _mock_self_ip_ok(_cfg: object) -> str | None:
    return _LOCAL_BACKEND_IP


def _mock_self_ip_none(_cfg: object) -> str | None:
    return None


def _self_device(device_id: str = _LOCAL_DEVICE_ID) -> dict[str, Any]:
    """Build a paired-device dict that matches the self-protect heuristic."""
    return {
        "deviceId": device_id,
        "publicKey": "K1",
        "clientId": "gateway-client",
        "clientMode": "backend",
        "remoteIp": _LOCAL_BACKEND_IP,
        "tokens": [
            {
                "role": "operator",
                "scopes": ["operator.read"],
                "lastUsedAtMs": 1500,
            }
        ],
    }


def _other_device(device_id: str = "other") -> dict[str, Any]:
    """Build a non-self paired-device dict (cli client)."""
    return {
        "deviceId": device_id,
        "publicKey": "K2",
        "clientId": "cli",
        "clientMode": "cli",
        "remoteIp": "10.0.0.99",
        "tokens": [{"role": "operator", "scopes": ["operator.admin"]}],
    }


def _make_dispatch(
    *,
    paired: list[dict[str, Any]] | None = None,
    remove_result: object | None = None,
    remove_exc: BaseException | None = None,
    list_exc: BaseException | None = None,
) -> tuple[Any, list[tuple[str, Any]]]:
    """Dispatch mock for ``openclaw_call`` covering both list+remove RPCs.

    DELETE now fetches device.pair.list FIRST (for self-protect), then
    device.pair.remove — every DELETE test needs both branches.
    """

    captured: list[tuple[str, Any]] = []
    paired_list = paired if paired is not None else []

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        captured.append((method, params))
        if method == "device.pair.list":
            if list_exc is not None:
                raise list_exc
            return {"pending": [], "paired": paired_list}
        if method == "device.pair.remove":
            if remove_exc is not None:
                raise remove_exc
            return remove_result if remove_result is not None else {"ok": True}
        raise AssertionError(f"unexpected method: {method}")

    return _fake, captured


@pytest.mark.asyncio
async def test_list_happy_marks_one_device_as_self(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        assert method == "device.pair.list"
        return {
            "pending": [],
            "paired": [_self_device(), _other_device()],
        }

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["isSelfResolved"] is True
    assert len(body["devices"]) == 2
    selfs = [d for d in body["devices"] if d["isSelf"]]
    assert len(selfs) == 1
    assert selfs[0]["deviceId"] == _LOCAL_DEVICE_ID


@pytest.mark.asyncio
async def test_list_empty(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"pending": [], "paired": []}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 200
    body = resp.json()
    # Empty paired + valid IP must still report isSelfResolved=True (no anomaly).
    assert body["isSelfResolved"] is True
    assert body["devices"] == []


@pytest.mark.asyncio
async def test_list_self_match_ip_unavailable_returns_isSelfResolved_false(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When autodetect fails AND no override, GET still succeeds but signals
    isSelfResolved=False so the UI disables every Remove button."""

    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"pending": [], "paired": [_self_device()]}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_none)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["isSelfResolved"] is False
    assert all(d["isSelf"] is False for d in body["devices"])


@pytest.mark.asyncio
async def test_list_marks_multiple_matching_backend_devices_as_self(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two paired devices both match the heuristic → both flagged isSelf=True."""

    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {
            "pending": [],
            "paired": [
                _self_device(device_id="mc-self-1"),
                _self_device(device_id="mc-self-2"),
                _other_device(),
            ],
        }

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["isSelfResolved"] is True
    selfs = {d["deviceId"] for d in body["devices"] if d["isSelf"]}
    assert selfs == {"mc-self-1", "mc-self-2"}


@pytest.mark.asyncio
async def test_list_malformed_payload_returns_502(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"paired": "not-a-list"}  # malformed

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "gateway_invalid_payload"


@pytest.mark.asyncio
async def test_list_cross_org_returns_404(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _ = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"pending": [], "paired": []}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{uuid4()}/devices")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_rpc_timeout_returns_504(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio as _asyncio

    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        await _asyncio.sleep(10)  # exceeds the wait_for

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)
    monkeypatch.setattr(gateway_api, "_PAIRING_RPC_TIMEOUT_SECONDS", 0.05)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 504
    assert resp.json()["detail"]["error"] == "gateway_timeout"


@pytest.mark.asyncio
async def test_list_rpc_unavailable_returns_503(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError("down", details={"code": "UNAVAILABLE"})

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "gateway_unavailable"


@pytest.mark.asyncio
async def test_list_does_not_cache_rpc_calls(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: don't accidentally reuse _CONFIG_LOOKUP_CACHE for pairings.

    The plan explicitly forbids caching the device list — operators expect
    post-revoke refresh to reflect reality immediately.
    """

    app, _, gateway = setup
    calls = 0

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal calls
        calls += 1
        return {"pending": [], "paired": []}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v1/gateways/{gateway.id}/devices"
        r1 = await client.get(url)
        r2 = await client.get(url)
    assert r1.status_code == r2.status_code == 200
    assert calls == 2  # one per request — no cache


@pytest.mark.asyncio
async def test_remove_happy(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    # paired list must include a self device so the empty-self-set fail-closed
    # gate (Fix A) doesn't refuse the call — plus a non-matching target.
    fake, captured = _make_dispatch(
        paired=[_self_device(), _other_device(device_id="some-other-device")],
    )

    monkeypatch.setattr(gateway_api, "openclaw_call", fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/some-other-device")
    assert resp.status_code == 200
    # list first, then remove
    assert captured == [
        ("device.pair.list", None),
        ("device.pair.remove", {"deviceId": "some-other-device"}),
    ]


@pytest.mark.asyncio
async def test_remove_self_protect(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    fake, captured = _make_dispatch(paired=[_self_device()])

    monkeypatch.setattr(gateway_api, "openclaw_call", fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/{_LOCAL_DEVICE_ID}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "cannot_remove_self"
    # list called, but remove must NOT be called
    assert [m for m, _ in captured] == ["device.pair.list"]


@pytest.mark.asyncio
async def test_remove_self_protect_blocks_any_matching_device(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When two devices both match the heuristic, DELETE on either returns 409."""

    app, _, gateway = setup
    paired = [
        _self_device(device_id="mc-self-1"),
        _self_device(device_id="mc-self-2"),
        _other_device(),
    ]

    for target in ("mc-self-1", "mc-self-2"):
        fake, captured = _make_dispatch(paired=paired)
        monkeypatch.setattr(gateway_api, "openclaw_call", fake)
        monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/{target}")
        assert resp.status_code == 409, target
        assert resp.json()["detail"]["error"] == "cannot_remove_self"
        assert [m for m, _ in captured] == ["device.pair.list"]


@pytest.mark.asyncio
async def test_remove_self_identity_unavailable_refuses(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When self IP can't be resolved, refuse 503 BEFORE any RPC call."""

    app, _, gateway = setup
    called = False

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_none)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/anything")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "self_identity_unavailable"
    assert called is False


@pytest.mark.asyncio
async def test_remove_device_not_found_returns_404(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    # Verified live shape from Task 1 probe — gateway emits "unknown deviceId" (camelCase).
    # Paired list includes a self device so Fix A's empty-self-set gate passes.
    fake, _ = _make_dispatch(
        paired=[_self_device()],
        remove_exc=OpenClawGatewayError(
            "unknown deviceId",
            details={"code": "INVALID_REQUEST", "message": "unknown deviceId"},
        ),
    )

    monkeypatch.setattr(gateway_api, "openclaw_call", fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "device_not_found"


@pytest.mark.asyncio
async def test_remove_pairing_scope_denied_returns_403(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    fake, _ = _make_dispatch(
        paired=[_self_device()],
        remove_exc=OpenClawGatewayError(
            "insufficient scope",
            details={"code": "INVALID_REQUEST", "message": "insufficient scope: operator.pairing"},
        ),
    )

    monkeypatch.setattr(gateway_api, "openclaw_call", fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "gateway_pairing_scope_denied"


@pytest.mark.asyncio
async def test_remove_gateway_unavailable_returns_503(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    fake, _ = _make_dispatch(
        paired=[_self_device()],
        remove_exc=OpenClawGatewayError("down", details={"code": "UNAVAILABLE"}),
    )

    monkeypatch.setattr(gateway_api, "openclaw_call", fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_remove_timeout_returns_504(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio as _asyncio

    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        await _asyncio.sleep(10)

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)
    monkeypatch.setattr(gateway_api, "_PAIRING_RPC_TIMEOUT_SECONDS", 0.05)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 504


@pytest.mark.parametrize(
    "scenario,expected_outcome,expected_status",
    [
        ("happy", "success", 200),
        ("not_found", "device_not_found", 404),
        ("self", "cannot_remove_self", 409),
        ("unavailable", "gateway_unavailable", 503),
    ],
)
@pytest.mark.asyncio
async def test_remove_emits_audit_log_per_outcome(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
    expected_outcome: str,
    expected_status: int,
) -> None:
    app, _, gateway = setup

    # All non-self scenarios must seed a self device so Fix A's
    # empty-self-set fail-closed gate doesn't intercept the call.
    if scenario == "happy":
        fake, _ = _make_dispatch(paired=[_self_device()])
        target_id = "other-id"
    elif scenario == "not_found":
        fake, _ = _make_dispatch(
            paired=[_self_device()],
            remove_exc=OpenClawGatewayError(
                "unknown deviceId",
                details={"code": "INVALID_REQUEST", "message": "unknown deviceId"},
            ),
        )
        target_id = "missing-x"
    elif scenario == "self":
        fake, _ = _make_dispatch(paired=[_self_device()])
        target_id = _LOCAL_DEVICE_ID
    else:  # unavailable
        fake, _ = _make_dispatch(
            paired=[_self_device()],
            remove_exc=OpenClawGatewayError("down", details={"code": "UNAVAILABLE"}),
        )
        target_id = "down-x"

    monkeypatch.setattr(gateway_api, "openclaw_call", fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    caplog.set_level(logging.INFO, logger="app.api.gateway")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/{target_id}")
    assert resp.status_code == expected_status

    audit_lines = [r for r in caplog.records if "gateway.pairing.remove.outcome" in r.getMessage()]
    assert len(audit_lines) == 1
    message = audit_lines[0].getMessage()
    assert f"outcome={expected_outcome}" in message
    assert f"device_id={target_id}" in message
    # Renamed from request_id → gateway_request_id to disambiguate from the
    # HTTP-level request_id that the logging middleware also stamps.
    assert "gateway_request_id=" in message
    assert "request_id=" not in message.replace("gateway_request_id=", "")


@pytest.mark.asyncio
async def test_remove_self_set_empty_refuses_503(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If self_match_ip resolves but no device matches the heuristic
    (NAT / remoteIp=null), DELETE must fail closed."""
    app, _, gateway = setup

    # IP resolves, but the paired list contains no backend rows from that IP.
    fake, captured = _make_dispatch(paired=[_other_device(device_id="stranger")])

    monkeypatch.setattr(gateway_api, "openclaw_call", fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/stranger")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "self_identity_unavailable"

    methods_called = [m for m, _ in captured]
    assert methods_called == [
        "device.pair.list"
    ], f"expected only device.pair.list, got {methods_called}"


@pytest.mark.asyncio
async def test_remove_removal_denied_maps_to_403(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upstream gateway 'device pairing removal denied' maps to 403."""
    app, _, gateway = setup
    fake, _ = _make_dispatch(
        paired=[_self_device()],
        remove_exc=OpenClawGatewayError(
            "device pairing removal denied",
            details={
                "code": "INVALID_REQUEST",
                "message": "device pairing removal denied",
            },
        ),
    )

    monkeypatch.setattr(gateway_api, "openclaw_call", fake)
    monkeypatch.setattr(gateway_api, "_resolve_self_match_ip", _mock_self_ip_ok)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "gateway_pairing_scope_denied"
