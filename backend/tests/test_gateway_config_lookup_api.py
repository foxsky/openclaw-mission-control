# ruff: noqa: INP001
"""Integration tests for /api/v1/gateways/{id}/config/lookup."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
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
            user=User(id=uuid4(), clerk_user_id="test-user", email="t@e"),
        )

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[require_org_admin] = _override_require_org_admin
    app.dependency_overrides[get_auth_context] = _override_get_auth_context
    return app


@pytest_asyncio.fixture
async def setup() -> AsyncIterator[tuple[FastAPI, Organization, Gateway]]:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    org = Organization(id=uuid4(), name="Org One")
    gateway = Gateway(
        id=uuid4(),
        organization_id=org.id,
        name="Gateway One",
        url="https://gateway.example.local",
        workspace_root="/workspace/openclaw",
    )
    async with session_maker() as session:
        session.add(org)
        session.add(gateway)
        await session.commit()

    app = _build_app(session_maker, organization=org)
    try:
        yield app, org, gateway
    finally:
        await engine.dispose()


def _reset_cache() -> None:
    gateway_api._CONFIG_LOOKUP_CACHE = type(gateway_api._CONFIG_LOOKUP_CACHE)(ttl_seconds=30.0)


@pytest.mark.asyncio
async def test_happy_path_returns_schema_and_badges(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    captured: list[tuple[str, Any]] = []

    async def _fake_openclaw_call(method: str, params: Any = None, *, config: Any) -> object:
        captured.append((method, params))
        return {
            "path": "agents.defaults.models",
            "schema": {"type": "object"},
            "reloadKind": "restart",
            "hint": "Restart required.",
            "hintPath": "agents.defaults.models",
            "children": [
                {"path": "agents.defaults.models.foo", "reloadKind": "hot"},
            ],
        }

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake_openclaw_call)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents.defaults.models"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["gateway_id"] == str(gateway.id)
    assert body["reloadKind"] == "restart"
    assert body["children"][0]["reloadKind"] == "hot"
    assert captured == [("config.schema.lookup", {"path": "agents.defaults.models"})]


@pytest.mark.asyncio
async def test_cache_singleflight_single_invocation_within_ttl(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    calls = 0

    async def _fake_openclaw_call(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal calls
        calls += 1
        return {"path": "p", "schema": {}, "reloadKind": "none", "children": []}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake_openclaw_call)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v1/gateways/{gateway.id}/config/lookup"
        r1 = await client.get(url, params={"path": "agents"})
        r2 = await client.get(url, params={"path": "agents"})

    assert r1.status_code == r2.status_code == 200
    assert calls == 1


@pytest.mark.asyncio
async def test_invalid_path_short_circuits_before_rpc(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    called = False

    async def _fake_openclaw_call(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake_openclaw_call)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "\x00bad"},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_path"
    assert called is False


@pytest.mark.asyncio
async def test_path_not_found_returns_404(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError(
            "config schema path not found",
            details={"code": "INVALID_REQUEST", "message": "config schema path not found"},
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "no.such.path"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "path_not_found"
    assert resp.json()["detail"]["path"] == "no.such.path"


@pytest.mark.asyncio
async def test_other_invalid_request_returns_422(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError(
            "bad shape",
            details={
                "code": "INVALID_REQUEST",
                "message": "config schema lookup returned invalid payload",
            },
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "gateway_rejected_request"


@pytest.mark.asyncio
async def test_method_not_supported_returns_501(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError(
            "method not found",
            details={"code": "METHOD_NOT_FOUND", "message": "unknown method config.schema.lookup"},
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 501
    assert resp.json()["detail"]["error"] == "method_unsupported"
    assert resp.json()["detail"]["requires_gateway_version"] == "2026.5.19"


@pytest.mark.asyncio
async def test_unavailable_returns_503(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError("down", details={"code": "UNAVAILABLE"})

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "gateway_unavailable"


@pytest.mark.asyncio
async def test_timeout_returns_504(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio as _asyncio

    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        await _asyncio.sleep(10)  # exceeds the 5s wait_for in handler

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_CONFIG_LOOKUP_RPC_TIMEOUT_SECONDS", 0.05)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 504
    assert resp.json()["detail"]["error"] == "gateway_timeout"


@pytest.mark.asyncio
async def test_other_gateway_id_returns_404(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _ = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{uuid4()}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_future_reload_kind_passes_through(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: don't tighten reload_kind to a Literal[...] later."""

    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {
            "path": ".",
            "schema": {},
            "reloadKind": "warm-restart-future",
            "children": [],
        }

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "."},
        )
    assert resp.status_code == 200
    assert resp.json()["reloadKind"] == "warm-restart-future"


@pytest.mark.asyncio
async def test_non_dict_payload_returns_502(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return ["not", "a", "dict"]

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "gateway_invalid_payload"


@pytest.mark.asyncio
async def test_malformed_reload_kind_shape_returns_502(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic ValidationError on a non-string reloadKind must surface as 502, not 500."""

    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {
            "path": "agents",
            "schema": {},
            "reloadKind": ["restart"],  # array — not str | None
            "children": [],
        }

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "gateway_invalid_payload"
