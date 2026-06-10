"""Scheduled poller that scrapes the OpenClaw gateway's
``/api/diagnostics/prometheus`` endpoint and persists error-rate samples
so MC can surface model/harness/run failure trends without standing up
a full Prometheus stack.

See ``docs/plans/2026-05-28-gateway-observability-poller-design.md`` for
the design rationale. The poller intentionally projects only the three
error metrics that fire under our Codex-stdio fleet (``model_call_total``
with ``outcome=error``, ``harness_run_total`` with ``outcome=error``,
``run_completed_total`` with ``outcome=error``) — ``model_failover_total``
is silent for Codex-harness 404 aborts (see ``project_openclaw_v526_state``
memory) so we do not rely on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

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
    # Per OpenClaw Prometheus catalog (docs/gateway/prometheus.md), the
    # ``run_completed_total`` family is labelled ``outcome`` — NOT
    # ``state`` (that label belongs to the orthogonal
    # ``session_state_total`` family). Using the wrong key silently
    # drops every failure on this metric, which is why we cite the
    # docs explicitly here.
    "openclaw_run_completed_total": ("outcome", "error"),
}

# Auth-expiry alerts repeat at most once per (gateway, provider, profile,
# severity) within this window. Process-local state — resets on restart,
# which just means one extra warning, never a missed one.
AUTH_ALERT_REWARN_SECONDS = 1800
_auth_alert_log_state: dict[tuple[str, str, str, str], float] = {}
_auth_last_checked: dict[str, float] = {}


def evaluate_auth_alerts(snapshot: object, *, warn_below_ms: float) -> list[dict[str, object]]:
    """Evaluate a ``models.authStatus`` snapshot into alert dicts.

    Only OAuth profiles carry expiry semantics — ``type: token`` /
    ``status: static`` profiles never alert. The provider-level
    ``status`` field is a worst-profile rollup (a provider with one
    expired and one healthy profile reports ``expired`` while calls
    still succeed), so provider-level alerts fire only when EVERY
    OAuth profile is expired.
    """
    if not isinstance(snapshot, dict):
        return []
    providers = snapshot.get("providers")
    if not isinstance(providers, list):
        return []
    alerts: list[dict[str, object]] = []
    for provider_entry in providers:
        if not isinstance(provider_entry, dict):
            continue
        provider = provider_entry.get("provider")
        profiles = provider_entry.get("profiles")
        oauth_profiles = [
            p
            for p in (profiles if isinstance(profiles, list) else [])
            if isinstance(p, dict) and p.get("type") == "oauth"
        ]
        expired_count = 0
        for profile in oauth_profiles:
            expiry = profile.get("expiry")
            remaining = expiry.get("remainingMs") if isinstance(expiry, dict) else None
            if not isinstance(remaining, (int, float)):
                continue
            base = {
                "provider": provider,
                "profile_id": profile.get("profileId"),
                "remaining_ms": remaining,
            }
            if profile.get("status") == "expired" or remaining <= 0:
                expired_count += 1
                alerts.append({**base, "severity": "expired"})
            elif remaining < warn_below_ms:
                alerts.append({**base, "severity": "expiring"})
        if oauth_profiles and expired_count == len(oauth_profiles):
            alerts.append(
                {
                    "provider": provider,
                    "profile_id": None,
                    "remaining_ms": None,
                    "severity": "provider_expired",
                }
            )
    return alerts


def _should_log_auth_alert(
    key: tuple[str, str, str, str],
    *,
    now: float,
    rewarn_seconds: float,
    state: dict[tuple[str, str, str, str], float],
) -> bool:
    last = state.get(key)
    if last is not None and (now - last) < rewarn_seconds:
        return False
    state[key] = now
    return True


async def check_gateway_auth_once(gateway: "Gateway") -> list[dict[str, object]]:
    """Fetch ``models.authStatus`` and log throttled expiry warnings.

    Failures (older gateway, transport error) return an empty list —
    this check must never disturb the metrics poll it rides along with.
    """
    import time

    from app.core.config import settings
    from app.services.openclaw.gateway_rpc import GatewayConfig, models_auth_status

    config = GatewayConfig(
        url=gateway.url,
        token=gateway.token,
        allow_insecure_tls=gateway.allow_insecure_tls,
        disable_device_pairing=gateway.disable_device_pairing,
    )
    snapshot = await models_auth_status(config=config)
    if snapshot is None:
        return []
    warn_below_ms = settings.gateway_auth_expiry_warn_hours * 3_600_000
    alerts = evaluate_auth_alerts(snapshot, warn_below_ms=warn_below_ms)
    now = time.monotonic()
    for alert in alerts:
        key = (
            str(gateway.id),
            str(alert["provider"]),
            str(alert["profile_id"]),
            str(alert["severity"]),
        )
        if _should_log_auth_alert(
            key,
            now=now,
            rewarn_seconds=AUTH_ALERT_REWARN_SECONDS,
            state=_auth_alert_log_state,
        ):
            remaining = alert["remaining_ms"]
            logger.warning(
                "observability_poller.auth_alert gateway_id=%s provider=%s "
                "profile=%s severity=%s remaining_hours=%s",
                gateway.id,
                alert["provider"],
                alert["profile_id"],
                alert["severity"],
                round(remaining / 3_600_000, 1) if isinstance(remaining, (int, float)) else None,
            )
    return alerts


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
    """Convert a gateway URL (typically ``ws(s)://...``) into the
    HTTP scheme+netloc used to reach diagnostics endpoints.

    Any path/query/fragment in the WebSocket URL (e.g. ``/ws``) is
    intentionally dropped — the diagnostics-prometheus endpoint is
    rooted at ``/api/diagnostics/prometheus``, not under the WS path.
    """

    parsed = urlparse(gateway_url)
    scheme_map = {"ws": "http", "wss": "https"}
    http_scheme = scheme_map.get(parsed.scheme, parsed.scheme)
    if not parsed.netloc:
        # Fallback for malformed inputs — preserve old behavior so
        # we don't regress on bare-host URLs.
        return gateway_url
    return urlunparse((http_scheme, parsed.netloc, "", "", "", ""))


async def scrape_gateway_metrics(
    *,
    gateway_url: str,
    gateway_token: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = _SCRAPE_TIMEOUT_SECONDS,
    allow_insecure_tls: bool = False,
) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    """Fetch and parse the gateway's Prometheus diagnostics endpoint.

    ``allow_insecure_tls`` matches the same-named flag on the Gateway
    record — gateways using self-signed certs on the WS path will also
    serve diagnostics with the same cert; disabling cert verification
    there keeps the scraper aligned with the WS RPC layer.
    """

    base = gateway_http_base(gateway_url).rstrip("/")
    url = base + _PROMETHEUS_PATH
    headers = {"Authorization": f"Bearer {gateway_token}"}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=timeout),
            verify=not allow_insecure_tls,
        )
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


def _rate_from_counter_delta(
    *,
    current: float,
    prior: float,
    elapsed_seconds: float,
) -> tuple[float | None, float | None]:
    """Pure rate-math helper used by both ``compute_rate_deltas`` (tests)
    and ``poll_gateway_once`` (production). Returns ``(rate, elapsed)``
    or ``(None, None)`` when the sample isn't safe to attribute as a
    rate (counter regression, zero elapsed)."""

    if elapsed_seconds <= 0:
        return None, None
    if current < prior:
        # Counter regression — gateway restart suspected. Surface null
        # so consumers can distinguish "no signal" from a real zero.
        return None, None
    return (current - prior) / elapsed_seconds, elapsed_seconds


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

    from app.core.config import settings
    from app.core.time import utcnow
    from app.models.gateway_observability_samples import GatewayObservabilitySample

    if not gateway.token:
        # Gateway.token is Optional in the model but the diagnostics
        # endpoint requires operator-scope auth. A null token means
        # this gateway row is misconfigured; skip rather than 401-loop.
        logger.warning(
            "observability_poller.skipping_unauthenticated_gateway gateway_id=%s",
            gateway.id,
        )
        return PollResult(rows_inserted=0, error="missing_gateway_token")

    try:
        raw_samples = await scrape_gateway_metrics(
            gateway_url=gateway.url,
            gateway_token=gateway.token,
            client=client,
            timeout=settings.gateway_observability_scrape_timeout_seconds,
            allow_insecure_tls=gateway.allow_insecure_tls,
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
        else:
            elapsed_seconds = (now - prior_sample.scraped_at).total_seconds()
            rate, elapsed = _rate_from_counter_delta(
                current=counter_value,
                prior=prior_sample.counter_value,
                elapsed_seconds=elapsed_seconds,
            )
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
    """Load the most recent sample per ``(metric_name, labels)`` for
    one gateway. Bounded by ``gateway_observability_prior_lookback_seconds``
    so historical retention doesn't make this a full-table scan."""

    from datetime import timedelta

    from sqlmodel import col, select

    from app.core.config import settings
    from app.core.time import utcnow
    from app.models.gateway_observability_samples import GatewayObservabilitySample

    lookback = timedelta(seconds=settings.gateway_observability_prior_lookback_seconds)
    cutoff = utcnow() - lookback
    statement = (
        select(GatewayObservabilitySample)
        .where(GatewayObservabilitySample.gateway_id == gateway_id)
        .where(GatewayObservabilitySample.scraped_at >= cutoff)
        .order_by(col(GatewayObservabilitySample.scraped_at).desc())
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


async def _maybe_check_gateway_auth(gateway: "Gateway") -> None:
    """Run the auth check at its own (slower) cadence than the metrics poll."""
    import time

    from app.core.config import settings

    interval = settings.gateway_auth_status_check_interval_seconds
    if interval <= 0 or not gateway.token:
        return
    now = time.monotonic()
    last = _auth_last_checked.get(str(gateway.id))
    if last is not None and (now - last) < interval:
        return
    _auth_last_checked[str(gateway.id)] = now
    await check_gateway_auth_once(gateway)


async def observability_poller_loop(stop_event: "asyncio.Event") -> None:
    """Long-running task: poll every configured gateway at the
    configured interval until stopped.

    Uses one DB session to enumerate gateways into plain rows, closes
    it, then opens a FRESH session per gateway for the actual
    scrape+persist. This keeps an unrelated failure in one gateway
    from poisoning the next iteration's session state.
    """

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
                async with async_session_maker() as enum_session:
                    gateways = (await enum_session.exec(select(Gateway))).all()
                # Snapshot to a list of detached references — each
                # gateway gets its own session below so a failure or
                # slow scrape doesn't hold the enum session open.
                for gateway in list(gateways):
                    try:
                        async with async_session_maker() as gw_session:
                            await poll_gateway_once(gw_session, gateway)
                    except Exception:
                        logger.exception(
                            "observability_poller.gateway_iteration_failed gateway_id=%s",
                            gateway.id,
                        )
                    try:
                        await _maybe_check_gateway_auth(gateway)
                    except Exception:
                        logger.exception(
                            "observability_poller.auth_check_failed gateway_id=%s",
                            gateway.id,
                        )
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
