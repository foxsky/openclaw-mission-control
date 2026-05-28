# ruff: noqa: INP001
"""End-to-end test for ``poll_gateway_once`` — exercises load_prior →
scrape (mocked) → filter → compute → persist."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.models.gateway_observability_samples import GatewayObservabilitySample
from app.models.gateways import Gateway
from app.models.organizations import Organization
from app.services.openclaw.observability_poller import poll_gateway_once


@pytest_asyncio.fixture()
async def session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture()
async def session(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_maker() as s:
        yield s


@pytest_asyncio.fixture()
async def gateway(session: AsyncSession) -> Gateway:
    org = Organization(id=uuid4(), name="Test Org", slug="test-org")
    session.add(org)
    await session.flush()
    gw = Gateway(
        id=uuid4(),
        organization_id=org.id,
        name="Test Gateway",
        url="ws://gateway.test:18789",
        workspace_root="/tmp",
        token="test-token",
    )
    session.add(gw)
    await session.commit()
    await session.refresh(gw)
    return gw


def _prom_body(model_call_error: int, harness_error: int, run_failed: int) -> str:
    """Produce a Prometheus scrape body containing only error series.

    Per the OpenClaw Prometheus catalog (docs/gateway/prometheus.md),
    ``run_completed_total`` is labelled ``outcome`` (NOT ``state`` —
    that belongs to ``session_state_total``).
    """
    return (
        f'openclaw_model_call_total{{model="gpt-5.5",outcome="error",'
        f'provider="openai-codex"}} {model_call_error}\n'
        f'openclaw_harness_run_total{{harness="codex",outcome="error"}} '
        f"{harness_error}\n"
        f'openclaw_run_completed_total{{outcome="error"}} {run_failed}\n'
        f'openclaw_memory_bytes{{kind="rss"}} 500000\n'  # not stored
    )


@pytest.mark.asyncio
async def test_poll_once_inserts_three_rows_with_null_rate_on_first_observation(
    session: AsyncSession, gateway: Gateway
) -> None:
    body = _prom_body(model_call_error=1, harness_error=2, run_failed=3)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await poll_gateway_once(session, gateway, client=client)

    assert result.rows_inserted == 3
    rows = (await session.exec(select(GatewayObservabilitySample))).all()
    assert len(rows) == 3
    assert all(r.rate_per_second is None for r in rows)
    assert all(r.elapsed_seconds is None for r in rows)
    counter_by_metric = {r.metric_name: r.counter_value for r in rows}
    assert counter_by_metric == {
        "openclaw_model_call_total": 1.0,
        "openclaw_harness_run_total": 2.0,
        "openclaw_run_completed_total": 3.0,
    }


@pytest.mark.asyncio
async def test_poll_once_computes_rate_against_prior_sample(
    session: AsyncSession, gateway: Gateway
) -> None:
    """Insert a prior sample manually, then poll — rate should match
    (current - prior) / elapsed_seconds."""

    sixty_seconds_ago = utcnow() - timedelta(seconds=60)
    prior = GatewayObservabilitySample(
        gateway_id=gateway.id,
        scraped_at=sixty_seconds_ago,
        metric_name="openclaw_model_call_total",
        labels={"model": "gpt-5.5", "outcome": "error", "provider": "openai-codex"},
        counter_value=10.0,
    )
    session.add(prior)
    await session.commit()

    body = (
        'openclaw_model_call_total{model="gpt-5.5",outcome="error",' 'provider="openai-codex"} 16\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await poll_gateway_once(session, gateway, client=client)

    new_rows = (
        await session.exec(
            select(GatewayObservabilitySample)
            .where(GatewayObservabilitySample.scraped_at > sixty_seconds_ago)
            .order_by(GatewayObservabilitySample.scraped_at.desc())  # type: ignore[arg-type]
        )
    ).all()
    fresh = next(r for r in new_rows if r.scraped_at != sixty_seconds_ago)
    assert fresh.counter_value == 16.0
    assert fresh.rate_per_second is not None
    # Allow a bit of slack — the elapsed seconds is computed from
    # actual wall-clock difference, not the prior's faked timestamp.
    assert 55.0 <= (fresh.elapsed_seconds or 0) <= 65.0
    assert fresh.rate_per_second == pytest.approx(6.0 / (fresh.elapsed_seconds or 60.0), rel=0.05)


@pytest.mark.asyncio
async def test_poll_once_skips_non_error_metrics(session: AsyncSession, gateway: Gateway) -> None:
    """Only the 3 error-rate metrics should produce rows."""

    body = (
        'openclaw_memory_bytes{kind="rss"} 500000\n'
        'openclaw_liveness_warning_total{reason="eld"} 4\n'
        'openclaw_model_call_total{model="gpt-5.5",outcome="completed"} 99\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await poll_gateway_once(session, gateway, client=client)

    assert result.rows_inserted == 0
    rows = (await session.exec(select(GatewayObservabilitySample))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_poll_once_scrape_failure_inserts_no_rows_and_does_not_raise(
    session: AsyncSession, gateway: Gateway
) -> None:
    """If the gateway scrape returns 5xx, the loop must keep running.
    poll_gateway_once swallows the error and reports zero inserts."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="bad gateway")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await poll_gateway_once(session, gateway, client=client)

    assert result.rows_inserted == 0
    assert result.error is not None
    rows = (await session.exec(select(GatewayObservabilitySample))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_poll_once_ignores_prior_samples_outside_lookback_window(
    session: AsyncSession, gateway: Gateway
) -> None:
    """A prior sample older than ``gateway_observability_prior_lookback_seconds``
    must be ignored (treated as first observation) — otherwise the
    lookup degrades into a full-history scan and rate computation
    bridges over arbitrarily long gaps."""

    from app.core.config import settings

    far_past = utcnow() - timedelta(
        seconds=settings.gateway_observability_prior_lookback_seconds + 60
    )
    stale = GatewayObservabilitySample(
        gateway_id=gateway.id,
        scraped_at=far_past,
        metric_name="openclaw_model_call_total",
        labels={"outcome": "error", "model": "gpt-5.5", "provider": "openai-codex"},
        counter_value=100.0,
    )
    session.add(stale)
    await session.commit()

    body = _prom_body(model_call_error=200, harness_error=0, run_failed=0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await poll_gateway_once(session, gateway, client=client)

    fresh_rows = (
        await session.exec(
            select(GatewayObservabilitySample).where(
                GatewayObservabilitySample.scraped_at > far_past
            )
        )
    ).all()
    # Exactly 3 fresh rows. The model_call_total row should have a null
    # rate (stale prior beyond lookback → first-observation behavior),
    # NOT a rate inferred from the ancient 100 → 200 delta.
    assert len(fresh_rows) == 3
    model_call = next(r for r in fresh_rows if r.metric_name == "openclaw_model_call_total")
    assert model_call.rate_per_second is None
    assert model_call.elapsed_seconds is None
