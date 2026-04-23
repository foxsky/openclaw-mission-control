# ruff: noqa: INP001

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi_pagination import add_pagination
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.agent import router as agent_router
from app.api.deps import get_board_for_actor_read, get_board_or_404
from app.core.agent_tokens import hash_agent_token
from app.db.session import get_session
from app.models.activity_events import ActivityEvent
from app.models.agents import Agent
from app.models.boards import Board
from app.models.gateways import Gateway
from app.models.organizations import Organization
from app.models.tasks import Task


async def _make_engine() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


def _build_test_app(session_maker: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(agent_router)
    app.include_router(api_v1)
    add_pagination(app)

    async def _override_get_session() -> AsyncSession:
        async with session_maker() as session:
            yield session

    async def _override_get_board_or_404(
        board_id: str,
        session: AsyncSession = Depends(get_session),
    ) -> Board:
        board = await Board.objects.by_id(UUID(board_id)).first(session)
        if board is None:
            from fastapi import HTTPException, status

            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return board

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_board_or_404] = _override_get_board_or_404
    app.dependency_overrides[get_board_for_actor_read] = _override_get_board_or_404
    return app


async def _seed_agent_task(
    session: AsyncSession,
    *,
    with_comment: bool = False,
) -> tuple[str, Board, Agent, Task]:
    token = "test-agent-token-" + uuid4().hex
    org_id = uuid4()
    gateway_id = uuid4()
    board_id = uuid4()
    agent_id = uuid4()
    task_id = uuid4()

    session.add(Organization(id=org_id, name=f"org-{org_id}"))
    session.add(
        Gateway(
            id=gateway_id,
            organization_id=org_id,
            name="gateway",
            url="https://gateway.example.local",
            workspace_root="/tmp/workspace",
        ),
    )
    board = Board(
        id=board_id,
        organization_id=org_id,
        gateway_id=gateway_id,
        name="Board",
        slug="board",
        comment_signal_filter="off",
    )
    session.add(board)
    agent = Agent(
        id=agent_id,
        board_id=board_id,
        gateway_id=gateway_id,
        name="Worker Agent",
        status="online",
        openclaw_session_id="agent:worker:session",
        agent_token_hash=hash_agent_token(token),
    )
    session.add(agent)
    task = Task(
        id=task_id,
        board_id=board_id,
        title="Live task",
        description="",
        status="in_progress",
        assigned_agent_id=agent_id,
    )
    session.add(task)
    if with_comment:
        session.add(
            ActivityEvent(
                event_type="task.comment",
                message="Existing progress update",
                task_id=task_id,
                board_id=board_id,
                agent_id=agent_id,
            ),
        )
    await session.commit()
    return token, board, agent, task


@pytest.mark.asyncio
async def test_agent_can_fetch_comments_for_live_task() -> None:
    engine = await _make_engine()
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = _build_test_app(session_maker)

    async with session_maker() as session:
        token, board, _agent, task = await _seed_agent_task(session, with_comment=True)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/agent/boards/{board.id}/tasks/{task.id}/comments",
                headers={"X-Agent-Token": token},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["message"] == "Existing progress update"
        assert body["items"][0]["task_id"] == str(task.id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_task_comments_missing_task_stays_404() -> None:
    engine = await _make_engine()
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = _build_test_app(session_maker)

    async with session_maker() as session:
        token, board, _agent, _task = await _seed_agent_task(session)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/agent/boards/{board.id}/tasks/{uuid4()}/comments",
                headers={"X-Agent-Token": token},
            )

        assert response.status_code == 404
    finally:
        await engine.dispose()
