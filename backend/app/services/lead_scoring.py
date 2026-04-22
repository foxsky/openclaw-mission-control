"""Phase VI §I5 lead heartbeat no-op scoring.

A lead heartbeat only scores as success if it produced at least one
of the four action categories the plan enumerates. Commentary alone
is a no-op; two consecutive no-op scorings trigger the operator
alert.

Attribution strategy (v1, three of four categories):
- ``Blocker`` rows where ``created_by_agent_id`` matches the lead
  (clean attribution via Phase II).
- ``ActivityEvent`` rows with ``event_type`` in the mutation set
  below AND ``agent_id`` matching the lead.

The "dependency graph changed" category is deferred — ``task_dependencies``
currently has no ``created_by`` column, so attribution would be
noisy. v2 can add that column and expand the scoring.

Bookmark strategy: derived from shadow_metric_events. ``last_scored_at``
is ``MAX(created_at)`` over the noop-candidate + streak-alert event
types for the lead. No new state columns — the metric table IS the
truth. Matches the A+X choice from the design brainstorm.

Streak detection: before emitting a candidate, read whether the most
recent prior candidate for the lead is within the last sweep window
(2× configured interval gives us "consecutive heartbeats" tolerance
even if one sweep pass was skipped). If yes, emit the streak-alert
row alongside the candidate.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.logging import get_logger
from app.core.time import utcnow
from app.models.agents import Agent
from app.models.blockers import Blocker
from app.models.boards import Board
from app.models.activity_events import ActivityEvent
from app.models.shadow_metric_events import ShadowMetricEvent
from app.services.shadow_metrics import (
    EVENT_SUPERVISOR_HEARTBEAT_NOOP_CANDIDATE,
    EVENT_SUPERVISOR_HEARTBEAT_NOOP_STREAK_ALERT,
    emit_supervisor_heartbeat_noop_candidate,
    emit_supervisor_heartbeat_noop_streak_alert,
)

logger = get_logger(__name__)

# ActivityEvent types that count as real mutations by the lead. We
# deliberately exclude ``task.comment`` (commentary is the no-op
# §I5 wants to catch) and ``*_notified`` / ``*_wake_*`` side-effect
# types (those are byproducts, not lead-authored actions).
_LEAD_ACTION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "task.created",
        "task.updated",
        "task.status_changed",
    }
)

_LEAD_SCORING_FLAG = "lead_scoring_v1"


async def count_lead_actions_since(
    session: AsyncSession,
    *,
    agent_id: UUID,
    board_id: UUID,
    bookmark: datetime,
) -> int:
    """Count real actions by the lead since ``bookmark``.

    Returns the sum of:
    - open blockers the lead filed
    - task activity events in the mutation set above, authored by
      the lead on this board
    """

    blocker_count = await session.scalar(
        select(func.count(col(Blocker.id)))
        .where(col(Blocker.board_id) == board_id)
        .where(col(Blocker.created_by_agent_id) == agent_id)
        .where(col(Blocker.created_at) > bookmark)
    ) or 0

    activity_count = await session.scalar(
        select(func.count(col(ActivityEvent.id)))
        .where(col(ActivityEvent.board_id) == board_id)
        .where(col(ActivityEvent.agent_id) == agent_id)
        .where(col(ActivityEvent.event_type).in_(_LEAD_ACTION_EVENT_TYPES))
        .where(col(ActivityEvent.created_at) > bookmark)
    ) or 0

    return int(blocker_count) + int(activity_count)


async def last_scoring_bookmark(
    session: AsyncSession, *, agent_id: UUID
) -> datetime | None:
    """Return the most-recent scoring timestamp for the lead, or None
    if they've never been scored. Reads from shadow_metric_events so
    no new state columns are needed — the event table is the truth.
    """

    return await session.scalar(
        select(func.max(col(ShadowMetricEvent.created_at)))
        .where(col(ShadowMetricEvent.agent_id) == agent_id)
        .where(
            col(ShadowMetricEvent.event_type).in_(
                {
                    EVENT_SUPERVISOR_HEARTBEAT_NOOP_CANDIDATE,
                    EVENT_SUPERVISOR_HEARTBEAT_NOOP_STREAK_ALERT,
                }
            )
        )
    )


async def _previous_noop_candidate_at(
    session: AsyncSession,
    *,
    agent_id: UUID,
    cutoff: datetime,
) -> datetime | None:
    """Return the timestamp of the most-recent prior noop-candidate
    event for the lead, restricted to the given cutoff window. Used
    to decide whether this sweep's candidate constitutes a streak."""

    return await session.scalar(
        select(func.max(col(ShadowMetricEvent.created_at)))
        .where(col(ShadowMetricEvent.agent_id) == agent_id)
        .where(
            col(ShadowMetricEvent.event_type)
            == EVENT_SUPERVISOR_HEARTBEAT_NOOP_CANDIDATE
        )
        .where(col(ShadowMetricEvent.created_at) >= cutoff)
    )


async def score_lead_once(
    session: AsyncSession,
    *,
    agent: Agent,
    board_id: UUID,
    sweep_interval: timedelta,
    now: datetime | None = None,
) -> bool:
    """Score one lead and emit metrics. Returns True if a no-op
    candidate was emitted (the lead did nothing in the window).

    ``sweep_interval`` is used to size the streak-detection lookback
    window (2× the interval) so a single dropped sweep doesn't reset
    the streak. The sweeper passes the configured interval through.
    """

    evaluated_at = now or utcnow()
    bookmark = await last_scoring_bookmark(session, agent_id=agent.id)

    # Capture the prior-candidate timestamp BEFORE emitting this
    # sweep's candidate — otherwise the streak lookup would find its
    # own freshly-written row and fire an alert on the first sweep.
    # 2× interval tolerates one dropped sweep in between.
    streak_cutoff = evaluated_at - (sweep_interval * 2)
    previous_candidate_at = await _previous_noop_candidate_at(
        session,
        agent_id=agent.id,
        cutoff=streak_cutoff,
    )

    # First-ever evaluation: window starts at (now - sweep_interval)
    # so we don't over-count actions from the distant past on boot.
    window_start = bookmark if bookmark is not None else evaluated_at - sweep_interval

    action_count = await count_lead_actions_since(
        session,
        agent_id=agent.id,
        board_id=board_id,
        bookmark=window_start,
    )
    if action_count > 0:
        return False

    await emit_supervisor_heartbeat_noop_candidate(
        agent_id=agent.id,
        board_id=board_id,
        window_started_at=window_start,
    )

    if previous_candidate_at is not None:
        await emit_supervisor_heartbeat_noop_streak_alert(
            agent_id=agent.id,
            board_id=board_id,
            previous_candidate_at=previous_candidate_at,
        )
    return True


async def score_all_leads_once(
    session: AsyncSession,
    *,
    sweep_interval: timedelta,
    now: datetime | None = None,
) -> int:
    """Score every lead agent on a board where ``lead_scoring_v1``
    is enabled. Returns the number of no-op candidates emitted."""

    evaluated_at = now or utcnow()
    # Join leads to their boards, filter on the rollout flag.
    stmt = (
        select(Agent, col(Board.id))
        .join(Board, col(Agent.board_id) == col(Board.id))
        .where(col(Agent.is_board_lead).is_(True))
    )
    rows = (await session.exec(stmt)).all()
    emitted = 0
    for agent, board_id in rows:
        board_flags = await session.scalar(
            select(Board.rollout_flags).where(Board.id == board_id)
        )
        if not board_flags or not board_flags.get(_LEAD_SCORING_FLAG):
            continue
        try:
            fired = await score_lead_once(
                session,
                agent=agent,
                board_id=board_id,
                sweep_interval=sweep_interval,
                now=evaluated_at,
            )
        except Exception:
            # A slow DB or a transient emit failure must not kill the
            # whole sweep. Log and continue so a single bad lead
            # doesn't starve the rest.
            logger.exception(
                "lead_scoring.score_once_failed agent_id=%s board_id=%s",
                agent.id,
                board_id,
            )
            continue
        if fired:
            emitted += 1
    return emitted
