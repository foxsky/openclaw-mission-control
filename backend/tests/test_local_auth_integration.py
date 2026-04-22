# ruff: noqa: INP001
"""Integration tests for local auth mode on protected API routes."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.users import router as users_router
from app.core import auth as auth_module
from app.core.auth_mode import AuthMode
from app.core.config import settings
from app.db.session import get_session


def _build_test_app(
    session_maker: async_sessionmaker[AsyncSession],
) -> FastAPI:
    app = FastAPI()
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(users_router)
    app.include_router(api_v1)

    async def _override_get_session() -> AsyncSession:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[auth_module.get_session] = _override_get_session
    return app


@pytest.mark.asyncio
async def test_local_auth_users_me_requires_and_accepts_valid_token(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_engine: AsyncEngine,
) -> None:
    unique_suffix = uuid4().hex
    expected_user_id = f"local-auth-integration-{unique_suffix}"
    expected_email = f"local-{unique_suffix}@localhost"
    expected_name = "Local Integration User"

    monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)
    monkeypatch.setattr(settings, "local_auth_token", "integration-token")
    monkeypatch.setattr(auth_module, "LOCAL_AUTH_USER_ID", expected_user_id)
    monkeypatch.setattr(auth_module, "LOCAL_AUTH_EMAIL", expected_email)
    monkeypatch.setattr(auth_module, "LOCAL_AUTH_NAME", expected_name)

    session_maker = async_sessionmaker(
        sqlite_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    app = _build_test_app(session_maker)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        missing = await client.get("/api/v1/users/me")
        assert missing.status_code == 401

        invalid = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert invalid.status_code == 401

        authorized = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": "Bearer integration-token"},
        )
        assert authorized.status_code == 200
        payload = authorized.json()
        assert payload["clerk_user_id"] == expected_user_id
        assert payload["email"] == expected_email
        assert payload["name"] == expected_name

        repeat = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": "Bearer integration-token"},
        )
        assert repeat.status_code == 200
        assert repeat.json()["id"] == payload["id"]
