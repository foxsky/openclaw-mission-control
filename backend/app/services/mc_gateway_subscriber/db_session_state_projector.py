"""Postgres-backed projector for ``sessions.changed`` event frames.

Drop-in replacement for the in-memory ``SessionStateProjector`` in the
worker entry point. Reads the existing row to enforce the
last-write-wins ts ordering AND the no-op-skip diff guard before
issuing a write — heartbeat ticks emit identical-field updates every
~10s and an unguarded projector would amplify the Postgres write rate
to match the heartbeat tick count rather than real session activity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.services.mc_gateway_subscriber.session_state_projector import (
    SessionState,
    build_state_from_frame,
)
from app.services.mc_gateway_subscriber.session_state_repo import (
    SessionStateRepo,
)


class DbSessionStateProjector:
    """Persisting projector. ``session_factory`` returns an async context
    manager yielding an ``AsyncSession`` — usually the production
    ``async_sessionmaker`` from ``app.db.session``."""

    def __init__(self, *, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    async def __call__(self, frame: dict[str, Any]) -> None:
        new_state = build_state_from_frame(frame)
        if new_state is None:
            return

        async with self._session_factory() as session:
            existing = await SessionStateRepo.get(
                session,
                agent_id=new_state.agent_id,
                session_label=new_state.session_label,
            )
            if existing is not None:
                if new_state.last_changed_at_ms <= existing.last_changed_at_ms:
                    return
                if _is_field_equal_to_existing(new_state, existing):
                    return
            await SessionStateRepo.upsert(session, new_state)
            await session.commit()


def _is_field_equal_to_existing(new_state: SessionState, existing: Any) -> bool:
    """Return True if every projected field except ``last_changed_at_ms``
    matches the row already in the DB. ts is the gateway's clock and
    advances on every heartbeat tick — only treat the event as
    write-worthy when it carries new information about the session."""
    candidate = replace(new_state, last_changed_at_ms=0)
    mirrored = SessionState(
        agent_id=existing.agent_id,
        session_label=existing.session_label,
        session_id=existing.session_id,
        last_phase=existing.last_phase,
        last_message_seq=existing.last_message_seq,
        last_changed_at_ms=0,
        input_tokens=existing.input_tokens,
        output_tokens=existing.output_tokens,
        total_tokens=existing.total_tokens,
        channel=existing.channel,
        aborted_last_run=existing.aborted_last_run,
    )
    return candidate == mirrored
