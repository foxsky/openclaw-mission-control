# ruff: noqa: INP001, S101
"""Child tasks inherit the parent's delivery contract at creation.

A decomposed child task (created with a ``parent_task_id``) routinely
omits the delivery contract — ``review_packet_type`` plus, for packet
types that require it, the ``validation_target`` triplet — because the
squad agents that will execute the child are API-forbidden from setting
those lead-only fields. Without inheritance the child is born without a
contract and silently deadlocks the first time anyone moves it to
``in_progress``: the move 422s on the missing fields, and the assigned
agent cannot set them. Inheriting the parent's contract (set once by an
operator/lead) keeps the whole decomposed subtree actionable.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.tasks import _inherit_delivery_contract_from_parent
from app.models.tasks import Task


async def _session() -> AsyncSession:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    return AsyncSession(engine, expire_on_commit=False)


def _parent_with_full_contract(board_id, parent_id) -> Task:
    return Task(
        id=parent_id,
        board_id=board_id,
        title="umbrella",
        description="",
        status="inbox",
        review_packet_type="frontend_ui",
        validation_target="http://localhost:3000",
        validation_target_kind="live_url",
        validation_target_scope="review",
    )


@pytest.mark.asyncio
async def test_child_inherits_full_contract_when_unset() -> None:
    async with await _session() as session:
        board_id = uuid4()
        parent_id = uuid4()
        session.add(_parent_with_full_contract(board_id, parent_id))
        await session.flush()

        child = Task(
            id=uuid4(),
            board_id=board_id,
            title="scaffold",
            description="",
            status="inbox",
            parent_task_id=parent_id,
        )
        await _inherit_delivery_contract_from_parent(session, task=child)

        assert child.review_packet_type == "frontend_ui"
        assert child.validation_target == "http://localhost:3000"
        assert child.validation_target_kind == "live_url"
        assert child.validation_target_scope == "review"


@pytest.mark.asyncio
async def test_explicit_child_values_are_not_overridden() -> None:
    async with await _session() as session:
        board_id = uuid4()
        parent_id = uuid4()
        session.add(_parent_with_full_contract(board_id, parent_id))
        await session.flush()

        child = Task(
            id=uuid4(),
            board_id=board_id,
            title="backend slice",
            description="",
            status="inbox",
            parent_task_id=parent_id,
            review_packet_type="backend_api",  # explicit: must win
        )
        await _inherit_delivery_contract_from_parent(session, task=child)

        assert child.review_packet_type == "backend_api"
        # The triplet the child left unset is still filled from the parent.
        assert child.validation_target == "http://localhost:3000"
        assert child.validation_target_kind == "live_url"
        assert child.validation_target_scope == "review"


@pytest.mark.asyncio
async def test_no_parent_is_a_noop() -> None:
    async with await _session() as session:
        child = Task(
            id=uuid4(),
            board_id=uuid4(),
            title="top-level",
            description="",
            status="inbox",
            parent_task_id=None,
        )
        await _inherit_delivery_contract_from_parent(session, task=child)
        assert child.review_packet_type is None
        assert child.validation_target is None


@pytest.mark.asyncio
async def test_missing_parent_is_a_noop() -> None:
    async with await _session() as session:
        child = Task(
            id=uuid4(),
            board_id=uuid4(),
            title="orphan-ref",
            description="",
            status="inbox",
            parent_task_id=uuid4(),  # no such parent row
        )
        await _inherit_delivery_contract_from_parent(session, task=child)
        assert child.review_packet_type is None
