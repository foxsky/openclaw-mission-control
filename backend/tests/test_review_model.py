# ruff: noqa: INP001
"""Unit tests for the Phase II Review + ReviewBlocker models (plan §I4).

Keeps scope to the column contract — the FAIL-requires-blockers
invariant lives at the API layer in a follow-up commit.
"""

from __future__ import annotations

from typing import Literal, get_args
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blockers import Blocker
from app.models.reviews import Review, ReviewBlocker

# Mirror the verdict enum in one test-local Literal; the model + the
# migration hold the CHECK constraint as the authoritative definition.
ReviewVerdict = Literal["pass", "fail", "needs_changes"]
REVIEW_VERDICTS = get_args(ReviewVerdict)


def _review(**overrides: object) -> Review:
    defaults: dict[str, object] = {
        "board_id": uuid4(),
        "task_id": uuid4(),
        "verdict": "pass",
    }
    defaults.update(overrides)
    return Review(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_canonical_verdicts_round_trip(sqlite_session: AsyncSession) -> None:
    assert set(REVIEW_VERDICTS) == {"pass", "fail", "needs_changes"}
    for verdict in REVIEW_VERDICTS:
        sqlite_session.add(_review(verdict=verdict))
    await sqlite_session.commit()


@pytest.mark.asyncio
async def test_unknown_verdict_rejected(sqlite_session: AsyncSession) -> None:
    sqlite_session.add(_review(verdict="approved"))
    with pytest.raises(IntegrityError):
        await sqlite_session.commit()


@pytest.mark.asyncio
async def test_review_blocker_unique_per_pair(sqlite_session: AsyncSession) -> None:
    """A review cannot cite the same blocker twice — that's noise, not signal."""

    review = _review()
    blocker = Blocker(
        board_id=review.board_id,
        task_id=review.task_id,
        category="source",
        owner_role="frontend-dev",
    )
    sqlite_session.add(review)
    sqlite_session.add(blocker)
    await sqlite_session.commit()

    sqlite_session.add(ReviewBlocker(review_id=review.id, blocker_id=blocker.id))
    await sqlite_session.commit()
    sqlite_session.add(ReviewBlocker(review_id=review.id, blocker_id=blocker.id))
    with pytest.raises(IntegrityError):
        await sqlite_session.commit()
