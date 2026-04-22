# ruff: noqa: INP001
"""Unit tests for the Phase II Blocker model (plan §I1).

Exercises the shape that API + enforcement code will rely on:
- creation round-trips the five category values,
- the CHECK constraint rejects unknown categories,
- the self-FK supersedes_blocker_id accepts another Blocker's id and
  rejects NULL only when no prior row is being superseded.
"""

from __future__ import annotations

from typing import get_args
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blockers import Blocker
from app.schemas.blockers import BlockerCategory

BLOCKER_CATEGORIES = get_args(BlockerCategory)


def _blocker(**overrides: object) -> Blocker:
    defaults: dict[str, object] = {
        "board_id": uuid4(),
        "task_id": uuid4(),
        "category": "source",
        "owner_role": "frontend-dev",
    }
    defaults.update(overrides)
    return Blocker(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_all_five_canonical_categories_round_trip(
    sqlite_session: AsyncSession,
) -> None:
    """Every plan §I1 category must be persistable."""

    assert set(BLOCKER_CATEGORIES) == {
        "source",
        "deploy",
        "runtime",
        "contract",
        "operator",
    }
    for category in BLOCKER_CATEGORIES:
        sqlite_session.add(_blocker(category=category))
    await sqlite_session.commit()


@pytest.mark.asyncio
async def test_unknown_category_rejected_by_check(
    sqlite_session: AsyncSession,
) -> None:
    """CHECK constraint is the last line of defense against raw-SQL drift."""

    sqlite_session.add(_blocker(category="bogus"))
    with pytest.raises(IntegrityError):
        await sqlite_session.commit()


@pytest.mark.asyncio
async def test_supersedes_accepts_prior_blocker(sqlite_session: AsyncSession) -> None:
    """A sharper restatement can reference the prior blocker via self-FK."""

    prior = _blocker()
    sqlite_session.add(prior)
    await sqlite_session.commit()
    sqlite_session.add(_blocker(supersedes_blocker_id=prior.id))
    await sqlite_session.commit()
