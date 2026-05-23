# ruff: noqa: INP001
"""Integration tests for /api/v1/gateways/{id}/devices."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
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


@dataclass
class _FakeIdentity:
    device_id: str
    public_key_pem: str = ""
    private_key_pem: str = ""


_LOCAL_DEVICE_ID = "abc-mc-self-device-id"


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


def _identity_self() -> _FakeIdentity:
    return _FakeIdentity(device_id=_LOCAL_DEVICE_ID)


def _identity_raises() -> _FakeIdentity:
    raise RuntimeError("identity file corrupted")


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
            "paired": [
                {
                    "deviceId": _LOCAL_DEVICE_ID,
                    "publicKey": "K1",
                    "clientId": "gateway-client",
                    "clientMode": "backend",
                    "tokens": [
                        {
                            "role": "operator",
                            "scopes": ["operator.read"],
                            "lastUsedAtMs": 1500,
                        }
                    ],
                },
                {
                    "deviceId": "other",
                    "publicKey": "K2",
                    "clientId": "cli",
                    "clientMode": "cli",
                    "tokens": [{"role": "operator", "scopes": ["operator.admin"]}],
                },
            ],
        }

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

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
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 200
    assert resp.json()["devices"] == []


@pytest.mark.asyncio
async def test_list_self_identity_unavailable(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"pending": [], "paired": [{"deviceId": "x", "publicKey": "K", "tokens": []}]}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_raises)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["isSelfResolved"] is False
    assert all(d["isSelf"] is False for d in body["devices"])


@pytest.mark.asyncio
async def test_list_malformed_payload_returns_502(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"paired": "not-a-list"}  # malformed

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

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
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

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
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)
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
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

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
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

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
    captured: list[tuple[str, Any]] = []

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        captured.append((method, params))
        return {"ok": True}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/some-other-device")
    assert resp.status_code == 200
    assert captured == [("device.pair.remove", {"deviceId": "some-other-device"})]


@pytest.mark.asyncio
async def test_remove_self_protect(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    called = False

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/{_LOCAL_DEVICE_ID}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "cannot_remove_self"
    assert called is False


@pytest.mark.asyncio
async def test_remove_self_identity_unavailable_refuses(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    called = False

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_raises)

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

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        # Verified live shape from Task 1 probe — gateway emits "unknown deviceId" (camelCase).
        raise OpenClawGatewayError(
            "unknown deviceId",
            details={"code": "INVALID_REQUEST", "message": "unknown deviceId"},
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

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

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError(
            "insufficient scope",
            details={"code": "INVALID_REQUEST", "message": "insufficient scope: operator.pairing"},
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

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

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError("down", details={"code": "UNAVAILABLE"})

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

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
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)
    monkeypatch.setattr(gateway_api, "_PAIRING_RPC_TIMEOUT_SECONDS", 0.05)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 504
