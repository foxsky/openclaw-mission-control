# ruff: noqa: INP001
"""Integration test for GET /api/v1/gateways/{id}/observability/error-rates."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import require_org_admin
from app.api.gateway import router as gateway_router
from app.core.auth import AuthContext, get_auth_context
from app.core.time import utcnow
from app.db.session import get_session
from app.models.gateway_observability_samples import GatewayObservabilitySample
from app.models.gateways import Gateway
from app.models.organization_members import OrganizationMember
from app.models.organizations import Organization
from app.models.users import User
from app.services.organizations import OrganizationContext


@pytest_asyncio.fixture()
async def session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _build_app(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    org_id,
) -> FastAPI:
    app = FastAPI()
    app.include_router(gateway_router, prefix="/api/v1")

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    async def _override_auth() -> AuthContext:
        async with session_maker() as session:
            existing_user = (await session.exec(select(User).limit(1))).first()
        return AuthContext(actor_type="user", user=existing_user)

    async def _override_org_admin() -> OrganizationContext:
        async with session_maker() as session:
            org = await session.get(Organization, org_id)
            assert org is not None
            return OrganizationContext(
                organization=org,
                member=OrganizationMember(
                    organization_id=org_id,
                    user_id=uuid4(),
                    role="owner",
                    all_boards_read=True,
                    all_boards_write=True,
                ),
            )

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_auth_context] = _override_auth
    app.dependency_overrides[require_org_admin] = _override_org_admin
    return app


@pytest.mark.asyncio
async def test_endpoint_returns_samples_within_window(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    org_id = uuid4()
    gateway_id = uuid4()
    async with session_maker() as session:
        org = Organization(id=org_id, name="Org", slug="org")
        user = User(
            id=uuid4(),
            clerk_user_id=f"clerk_{uuid4()}",
            email="test@example.com",
        )
        session.add(org)
        session.add(user)
        await session.flush()
        session.add(
            OrganizationMember(
                organization_id=org_id, user_id=user.id, role="admin"
            )
        )
        gateway = Gateway(
            id=gateway_id,
            organization_id=org_id,
            name="GW",
            url="ws://gw:18789",
            workspace_root="/tmp",
            token="t",
        )
        session.add(gateway)
        now = utcnow()
        session.add(
            GatewayObservabilitySample(
                gateway_id=gateway_id,
                scraped_at=now,
                metric_name="openclaw_model_call_total",
                labels={"outcome": "error", "model": "gpt-5.5"},
                counter_value=3.0,
                rate_per_second=0.05,
                elapsed_seconds=60.0,
            )
        )
        session.add(
            GatewayObservabilitySample(
                gateway_id=gateway_id,
                scraped_at=now - timedelta(hours=2),
                metric_name="openclaw_model_call_total",
                labels={"outcome": "error", "model": "gpt-5.5"},
                counter_value=2.0,
                rate_per_second=None,
                elapsed_seconds=None,
            )
        )
        await session.commit()

    app = _build_app(session_maker, org_id=org_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway_id}/observability/error-rates",
            params={"window": "1h"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["windowSeconds"] == 3600
    assert len(body["samples"]) == 1  # 2h-old row falls outside window
    sample = body["samples"][0]
    assert sample["metric_name"] == "openclaw_model_call_total"
    assert sample["counter_value"] == 3.0
    assert sample["ratePerSecond"] == 0.05


@pytest.mark.asyncio
async def test_endpoint_400s_on_invalid_window(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    org_id = uuid4()
    gateway_id = uuid4()
    async with session_maker() as session:
        org = Organization(id=org_id, name="Org", slug="org")
        user = User(
            id=uuid4(),
            clerk_user_id=f"clerk_{uuid4()}",
            email="test@example.com",
        )
        session.add(org)
        session.add(user)
        await session.flush()
        session.add(
            OrganizationMember(
                organization_id=org_id, user_id=user.id, role="admin"
            )
        )
        session.add(
            Gateway(
                id=gateway_id,
                organization_id=org_id,
                name="GW",
                url="ws://gw:18789",
                workspace_root="/tmp",
                token="t",
            )
        )
        await session.commit()

    app = _build_app(session_maker, org_id=org_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway_id}/observability/error-rates",
            params={"window": "2 hours"},
        )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_window"
