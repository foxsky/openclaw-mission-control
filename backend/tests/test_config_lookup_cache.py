# ruff: noqa: INP001
"""Unit tests for the gateway config-lookup TTL singleflight cache."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.services.openclaw.config_lookup_cache import ConfigLookupCache


@pytest.mark.asyncio
async def test_first_call_invokes_loader() -> None:
    cache = ConfigLookupCache(ttl_seconds=30.0)
    gateway_id = uuid4()
    calls = 0

    async def _load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"reloadKind": "restart"}

    result = await cache.get_or_load((gateway_id, "agents"), _load)

    assert result == {"reloadKind": "restart"}
    assert calls == 1


@pytest.mark.asyncio
async def test_cache_hit_within_ttl_skips_loader() -> None:
    cache = ConfigLookupCache(ttl_seconds=30.0)
    gateway_id = uuid4()
    calls = 0

    async def _load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"v": calls}

    first = await cache.get_or_load((gateway_id, "agents"), _load)
    second = await cache.get_or_load((gateway_id, "agents"), _load)

    assert first == second == {"v": 1}
    assert calls == 1


@pytest.mark.asyncio
async def test_singleflight_concurrent_calls_share_loader() -> None:
    cache = ConfigLookupCache(ttl_seconds=30.0)
    gateway_id = uuid4()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def _load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"v": calls}

    task_a = asyncio.create_task(cache.get_or_load((gateway_id, "p"), _load))
    await started.wait()
    task_b = asyncio.create_task(cache.get_or_load((gateway_id, "p"), _load))
    await asyncio.sleep(0)  # let task_b register on the same future
    release.set()

    a, b = await asyncio.gather(task_a, task_b)
    assert a == b == {"v": 1}
    assert calls == 1


@pytest.mark.asyncio
async def test_loader_exception_is_not_cached() -> None:
    cache = ConfigLookupCache(ttl_seconds=30.0)
    gateway_id = uuid4()
    calls = 0

    async def _failing() -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await cache.get_or_load((gateway_id, "agents"), _failing)

    async def _ok() -> dict[str, object]:
        return {"ok": True}

    result = await cache.get_or_load((gateway_id, "agents"), _ok)
    assert result == {"ok": True}
    assert calls == 1  # the failing loader ran once; subsequent _ok ran independently


@pytest.mark.asyncio
async def test_ttl_expiry_refetches() -> None:
    cache = ConfigLookupCache(ttl_seconds=0.0)  # instant expiry
    gateway_id = uuid4()
    calls = 0

    async def _load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"v": calls}

    first = await cache.get_or_load((gateway_id, "agents"), _load)
    second = await cache.get_or_load((gateway_id, "agents"), _load)
    assert first == {"v": 1}
    assert second == {"v": 2}
