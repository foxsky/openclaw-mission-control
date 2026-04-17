"""Phase 0 shadow-metric emitters.

This module wires the shared comment classifier into the comment write
path without changing caller-visible behavior. Each hook returns a list
of ``ShadowMetricEvent`` rows the caller adds to the same session so
the comment and its observability signals commit atomically.

See ``docs/plans/2026-04-17-mc-delivery-enforcement-plan-phase-1-amendments.md``
sections A.2 and A.4.

Retention of the events themselves is 90 days per amendment §A.4; a
separate purge job (not in this module) enforces it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_
from sqlmodel import col, desc, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.models.activity_events import ActivityEvent
from app.models.shadow_metric_events import ShadowMetricEvent
from app.services.comment_classifier import ClassifierFlag, classify
from app.services.comment_classifier.patterns import (
    NEAR_DUPLICATE_WINDOW_SECONDS,
)

logger = logging.getLogger(__name__)

# Canonical event type constants. Keep these in sync with downstream
# operator dashboards — changing a value rewrites history from the
# operator's POV.
EVENT_COMMENT_ACK_ONLY = "comment.ack_only_candidate"
EVENT_COMMENT_NEAR_DUPLICATE = "comment.near_duplicate_candidate"


def _flag_to_event_type(flag: ClassifierFlag) -> str:
    if flag is ClassifierFlag.ACK_ONLY:
        return EVENT_COMMENT_ACK_ONLY
    if flag is ClassifierFlag.NEAR_DUPLICATE:
        return EVENT_COMMENT_NEAR_DUPLICATE
    raise ValueError(f"Unknown classifier flag: {flag!r}")


async def _fetch_prior_comment(
    session: AsyncSession,
    *,
    task_id: UUID,
    agent_id: UUID,
    since: datetime,
) -> ActivityEvent | None:
    """Most recent comment by ``agent_id`` on ``task_id`` at or after ``since``.

    Uses the composite index on
    ``(task_id, agent_id, event_type, created_at)`` added in migration
    ``d4e5f6a7b8c9``; the filter order matches the index's leading
    columns so the planner does a single range scan + pick-top.
    """

    statement = (
        select(ActivityEvent)
        .where(
            and_(
                col(ActivityEvent.task_id) == task_id,
                col(ActivityEvent.agent_id) == agent_id,
                col(ActivityEvent.event_type) == "task.comment",
                col(ActivityEvent.created_at) >= since,
            )
        )
        .order_by(desc(col(ActivityEvent.created_at)))
        .limit(1)
    )
    return (await session.exec(statement)).first()


async def build_shadow_events_for_comment(
    session: AsyncSession,
    *,
    task_id: UUID,
    board_id: UUID | None,
    agent_id: UUID | None,
    source_event_id: UUID,
    message: str,
    packet_type: str | None,
    now: datetime | None = None,
) -> list[ShadowMetricEvent]:
    """Classify a new comment and return shadow-metric events for it.

    Must be called BEFORE the new comment's ``ActivityEvent`` is added
    to the session so the prior-comment query does not see it.

    Any exception inside this function is logged and the caller receives
    an empty list — classifier failures must not break comment writes.
    """

    try:
        reference = now if now is not None else utcnow()
        prior_message: str | None = None
        prior_created_at: datetime | None = None
        if agent_id is not None:
            window_start = reference - timedelta(seconds=NEAR_DUPLICATE_WINDOW_SECONDS)
            prior = await _fetch_prior_comment(
                session,
                task_id=task_id,
                agent_id=agent_id,
                since=window_start,
            )
            if prior is not None:
                prior_message = prior.message
                prior_created_at = prior.created_at

        flags = classify(
            message,
            packet_type=packet_type,
            prior_comment=prior_message,
            prior_comment_created_at=prior_created_at,
            now=reference,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "shadow_metrics.classify_failed task_id=%s agent_id=%s",
            task_id,
            agent_id,
        )
        return []

    if not flags:
        return []

    return [
        ShadowMetricEvent(
            event_type=_flag_to_event_type(flag),
            task_id=task_id,
            agent_id=agent_id,
            board_id=board_id,
            source_event_id=source_event_id,
            metadata_json={
                "packet_type": packet_type,
                "message_length": len(message),
            },
        )
        for flag in flags
    ]
