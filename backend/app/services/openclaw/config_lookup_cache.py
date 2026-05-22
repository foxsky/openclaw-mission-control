"""In-process singleflight TTL cache for gateway config schema lookups.

Schema is near-static at gateway runtime, but the inspector page hits the
endpoint per keystroke (debounced) and per breadcrumb click. Without this
cache every request opens a fresh WebSocket (gateway_rpc.py:606-609).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class _Entry:
    value: object
    expires_at: float


class ConfigLookupCache:
    """Per-key TTL cache with singleflight semantics."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._values: dict[tuple[UUID, str, str], _Entry] = {}
        self._inflight: dict[tuple[UUID, str, str], asyncio.Future[object]] = {}
        self._lock = asyncio.Lock()

    async def get_or_load(
        self,
        key: tuple[UUID, str, str],
        loader: Callable[[], Awaitable[object]],
    ) -> object:
        now = time.monotonic()
        async with self._lock:
            entry = self._values.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value
            inflight = self._inflight.get(key)
            if inflight is None:
                inflight = asyncio.get_running_loop().create_future()
                self._inflight[key] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            return await asyncio.shield(inflight)

        try:
            value = await loader()
        except BaseException as exc:  # noqa: BLE001 — propagate (incl. CancelledError) to all waiters
            async with self._lock:
                self._inflight.pop(key, None)
            if not inflight.done():
                inflight.set_exception(exc)
            # Mark consumed so asyncio doesn't warn when no waiter retrieved it.
            inflight.exception()
            raise

        async with self._lock:
            self._values[key] = _Entry(value=value, expires_at=time.monotonic() + self._ttl)
            self._inflight.pop(key, None)
        if not inflight.done():
            inflight.set_result(value)
        return value
