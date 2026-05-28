"""Scheduled poller that scrapes the OpenClaw gateway's
``/api/diagnostics/prometheus`` endpoint and persists error-rate samples
so MC can surface model/harness/run failure trends without standing up
a full Prometheus stack.

See ``docs/plans/2026-05-28-gateway-observability-poller-design.md`` for
the design rationale. The poller intentionally projects only the three
error metrics that fire under our Codex-stdio fleet (``model_call_total``
with ``outcome=error``, ``harness_run_total`` with ``outcome=error``,
``run_completed_total`` with ``state=failed``) — ``model_failover_total``
is silent for Codex-harness 404 aborts (see ``project_openclaw_v526_state``
memory) so we do not rely on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from app.core.logging import get_logger

if TYPE_CHECKING:
    import asyncio
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.models.gateways import Gateway

logger = get_logger(__name__)

_PROMETHEUS_PATH = "/api/diagnostics/prometheus"
_SCRAPE_TIMEOUT_SECONDS = 5.0

# The Prometheus counter families we project into MC's local DB. Each
# entry maps the metric name to the label key+value that selects the
# "error" sub-series we care about for alerting.
ERROR_METRIC_NAMES: dict[str, tuple[str, str]] = {
    "openclaw_model_call_total": ("outcome", "error"),
    "openclaw_harness_run_total": ("outcome", "error"),
    "openclaw_run_completed_total": ("state", "failed"),
}


@dataclass(frozen=True)
class PriorSample:
    """Previous-sample state used to compute the delta on the next scrape.

    ``scraped_at`` is optional: tests that exercise the rate math with
    a uniform ``elapsed_seconds`` parameter leave it unset, while the
    real-world poller populates it from the DB row's timestamp so each
    series gets its own elapsed window.
    """

    counter_value: float
    scraped_at: datetime | None = None


@dataclass(frozen=True)
class DeltaRow:
    """One row's worth of computed sample, ready to persist."""

    metric_name: str
    labels: dict[str, str]
    counter_value: float
    rate_per_second: float | None
    elapsed_seconds: float | None


# Lines we accept have shape ``metric_name{labels} value`` or
# ``metric_name value``. Labels are zero or more ``key="value"`` pairs.
# We deliberately stay forgiving — any line that does not match is
# silently dropped (the Prometheus exporter occasionally emits headers
# or empty values during plugin reload).
_SAMPLE_RE = re.compile(
    r"""
    ^
    (?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)       # metric name
    (?:\{(?P<labels>[^}]*)\})?               # optional label block
    \s+
    (?P<value>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|\+Inf|-Inf|NaN)
    \s*
    $
    """,
    re.VERBOSE,
)

_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def parse_prometheus_text(
    text: str,
) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    """Parse a Prometheus text-format payload into a dict keyed by
    ``(metric_name, frozenset_of_label_pairs)`` with float values.

    Comment lines, blank lines, and any line we cannot recognize are
    silently skipped (Prometheus exporters periodically emit partial
    output during plugin lifecycle events).
    """

    samples: dict[tuple[str, frozenset[tuple[str, str]]], float] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SAMPLE_RE.match(stripped)
        if not match:
            continue
        name = match.group("name")
        label_block = match.group("labels") or ""
        raw_value = match.group("value")
        try:
            value = float(raw_value)
        except ValueError:
            continue
        labels = frozenset(_LABEL_RE.findall(label_block))
        samples[(name, labels)] = value
    return samples


def gateway_http_base(gateway_url: str) -> str:
    """Convert a gateway URL (typically ``ws(s)://...``) into the matching
    HTTP base used to reach diagnostics endpoints."""

    if gateway_url.startswith("ws://"):
        return "http://" + gateway_url[len("ws://") :]
    if gateway_url.startswith("wss://"):
        return "https://" + gateway_url[len("wss://") :]
    return gateway_url


async def scrape_gateway_metrics(
    *,
    gateway_url: str,
    gateway_token: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = _SCRAPE_TIMEOUT_SECONDS,
) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    """Fetch and parse the gateway's Prometheus diagnostics endpoint."""

    base = gateway_http_base(gateway_url).rstrip("/")
    url = base + _PROMETHEUS_PATH
    headers = {"Authorization": f"Bearer {gateway_token}"}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=timeout))
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return parse_prometheus_text(response.text)
    finally:
        if owns_client:
            await client.aclose()


def filter_error_samples(
    samples: dict[tuple[str, frozenset[tuple[str, str]]], float],
) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    """Drop any sample that isn't one of our three error-rate metrics."""

    out: dict[tuple[str, frozenset[tuple[str, str]]], float] = {}
    for (name, labels), value in samples.items():
        if name not in ERROR_METRIC_NAMES:
            continue
        required_key, required_value = ERROR_METRIC_NAMES[name]
        if (required_key, required_value) not in labels:
            continue
        out[(name, labels)] = value
    return out


def compute_rate_deltas(
    *,
    prior: dict[tuple[str, frozenset[tuple[str, str]]], PriorSample],
    current: dict[tuple[str, frozenset[tuple[str, str]]], float],
    elapsed_seconds: float,
) -> list[DeltaRow]:  # noqa: D401
    # NOTE: elapsed_seconds is the fall-through window. Per-series
    # elapsed (used by the real poller) is computed in
    # ``poll_gateway_once`` directly from each PriorSample's scraped_at
    # before this function is reached.
    """Build one ``DeltaRow`` per current sample, attaching the rate
    when we have a prior observation."""

    rows: list[DeltaRow] = []
    for key, counter_value in current.items():
        name, labels = key
        prior_sample = prior.get(key)
        if prior_sample is None or elapsed_seconds <= 0:
            rate: float | None = None
            elapsed: float | None = None
        elif counter_value < prior_sample.counter_value:
            # Counter regression — almost certainly a gateway restart.
            # Surface null so the consumer can distinguish "no signal"
            # from a real zero-rate window.
            rate = None
            elapsed = None
        else:
            rate = (counter_value - prior_sample.counter_value) / elapsed_seconds
            elapsed = elapsed_seconds
        rows.append(
            DeltaRow(
                metric_name=name,
                labels=dict(labels),
                counter_value=counter_value,
                rate_per_second=rate,
                elapsed_seconds=elapsed,
            )
        )
    return rows


@dataclass(frozen=True)
class PollResult:
    """Outcome of one ``poll_gateway_once`` invocation."""

    rows_inserted: int
    error: str | None = None


async def poll_gateway_once(
    session: "AsyncSession",
    gateway: "Gateway",
    *,
    client: httpx.AsyncClient | None = None,
) -> PollResult:
    """Scrape one gateway, persist new observability samples.

    Errors are caught and reported via ``PollResult.error`` so the
    recurring loop keeps running. The function returns after at most
    one scrape attempt; retry policy belongs to the caller.
    """

    from app.core.time import utcnow
    from app.models.gateway_observability_samples import GatewayObservabilitySample

    try:
        raw_samples = await scrape_gateway_metrics(
            gateway_url=gateway.url,
            gateway_token=gateway.token,
            client=client,
        )
    except Exception as exc:
        logger.warning(
            "observability_poller.scrape_failed gateway_id=%s error=%r",
            gateway.id,
            exc,
        )
        return PollResult(rows_inserted=0, error=str(exc))

    current = filter_error_samples(raw_samples)
    if not current:
        return PollResult(rows_inserted=0)

    prior = await _load_latest_prior_samples(session, gateway.id)
    now = utcnow()
    rows: list[GatewayObservabilitySample] = []
    for key, counter_value in current.items():
        name, labels = key
        prior_sample = prior.get(key)
        if prior_sample is None or prior_sample.scraped_at is None:
            rate: float | None = None
            elapsed: float | None = None
        elif counter_value < prior_sample.counter_value:
            # Counter regression — gateway restart suspected.
            rate = None
            elapsed = None
        else:
            elapsed = (now - prior_sample.scraped_at).total_seconds()
            if elapsed <= 0:
                rate = None
                elapsed = None
            else:
                rate = (counter_value - prior_sample.counter_value) / elapsed
        rows.append(
            GatewayObservabilitySample(
                gateway_id=gateway.id,
                scraped_at=now,
                metric_name=name,
                labels=dict(labels),
                counter_value=counter_value,
                rate_per_second=rate,
                elapsed_seconds=elapsed,
            )
        )

    session.add_all(rows)
    try:
        await session.commit()
    except Exception as exc:
        logger.exception(
            "observability_poller.persist_failed gateway_id=%s error=%r",
            gateway.id,
            exc,
        )
        await session.rollback()
        return PollResult(rows_inserted=0, error=str(exc))

    return PollResult(rows_inserted=len(rows))


async def _load_latest_prior_samples(
    session: "AsyncSession",
    gateway_id: "UUID",
) -> dict[tuple[str, frozenset[tuple[str, str]]], PriorSample]:
    """Load the most recent sample per ``(metric_name, labels)`` for one gateway.

    Returns an empty dict when no rows exist yet. Each PriorSample
    carries ``scraped_at`` so the poll loop can compute per-series
    elapsed time.
    """

    from sqlmodel import select

    from app.models.gateway_observability_samples import GatewayObservabilitySample

    statement = (
        select(GatewayObservabilitySample)
        .where(GatewayObservabilitySample.gateway_id == gateway_id)
        .order_by(GatewayObservabilitySample.scraped_at.desc())  # type: ignore[arg-type]
    )
    result = await session.exec(statement)
    out: dict[tuple[str, frozenset[tuple[str, str]]], PriorSample] = {}
    for row in result:
        key = (
            row.metric_name,
            frozenset((k, v) for k, v in row.labels.items()),
        )
        # Take the most-recent row per key (the query orders desc,
        # so the first occurrence wins).
        if key not in out:
            out[key] = PriorSample(
                counter_value=row.counter_value,
                scraped_at=row.scraped_at,
            )
    return out


async def observability_poller_loop(stop_event: "asyncio.Event") -> None:
    """Long-running task: poll every configured gateway at the
    configured interval until stopped."""

    import asyncio

    from sqlmodel import select

    from app.core.config import settings
    from app.db.session import async_session_maker
    from app.models.gateways import Gateway

    interval = settings.gateway_observability_poll_interval_seconds
    if interval <= 0:
        logger.info("observability_poller.disabled interval=%s", interval)
        return

    logger.info("observability_poller.loop_started interval_seconds=%s", interval)
    try:
        while not stop_event.is_set():
            try:
                async with async_session_maker() as session:
                    gateways = (await session.exec(select(Gateway))).all()
                    for gateway in gateways:
                        await poll_gateway_once(session, gateway)
            except Exception:
                logger.exception("observability_poller.iteration_failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                continue
    finally:
        logger.info("observability_poller.loop_stopped")


async def stop_observability_poller(
    task: "asyncio.Task[None] | None",
    stop_event: "asyncio.Event",
) -> None:
    """Graceful shutdown for the poller loop."""

    import asyncio
    from contextlib import suppress

    stop_event.set()
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
