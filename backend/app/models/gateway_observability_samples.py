"""Persisted samples from the gateway Prometheus diagnostics scrape.

One row per ``(gateway_id, scraped_at, metric_name, labels)`` tuple.
The poller stores only the error-rate metric families that actually
fire under our Codex-stdio fleet (``model_call_total``,
``harness_run_total``, ``run_completed_total``) — see
``project_openclaw_v526_state`` memory for the rationale on why
``model_failover_total`` is silent and not stored.

``counter_value`` is the absolute Prometheus counter value at the
scrape moment. ``rate_per_second`` is computed at write time from the
previous sample for the same ``(gateway_id, metric_name, labels)``
triplet; null on the first observation when no prior sample exists.
``labels`` is a JSON dict keyed by Prometheus label name (e.g.
``{"model": "gpt-5.5", "provider": "openai-codex"}``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

RUNTIME_ANNOTATION_TYPES = (datetime, UUID)


class GatewayObservabilitySample(QueryModel, table=True):
    """Single Prometheus sample captured by the scheduled poller."""

    __tablename__ = "gateway_observability_samples"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    gateway_id: UUID = Field(foreign_key="gateways.id", index=True, nullable=False)
    scraped_at: datetime = Field(default_factory=utcnow, index=True, nullable=False)
    metric_name: str = Field(index=True, nullable=False)
    labels: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    counter_value: float = Field(nullable=False)
    rate_per_second: float | None = Field(default=None)
    elapsed_seconds: float | None = Field(default=None)
