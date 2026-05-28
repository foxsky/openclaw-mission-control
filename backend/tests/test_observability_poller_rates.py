# ruff: noqa: INP001
"""Tests for ``filter_error_samples`` and ``compute_rate_deltas``."""

from __future__ import annotations

from app.services.openclaw.observability_poller import (
    ERROR_METRIC_NAMES,
    PriorSample,
    compute_rate_deltas,
    filter_error_samples,
)


def _make_samples(*pairs: tuple[str, dict[str, str], float]) -> dict:
    return {(name, frozenset(labels.items())): value for name, labels, value in pairs}


def test_filter_drops_non_error_metric_names() -> None:
    samples = _make_samples(
        ("openclaw_memory_bytes", {"kind": "rss"}, 500_000.0),
        ("openclaw_liveness_warning_total", {"reason": "eld"}, 4.0),
    )
    assert filter_error_samples(samples) == {}


def test_filter_keeps_model_call_total_when_outcome_error() -> None:
    samples = _make_samples(
        (
            "openclaw_model_call_total",
            {"model": "gpt-5.5", "outcome": "error", "provider": "openai-codex"},
            3.0,
        ),
        (
            "openclaw_model_call_total",
            {"model": "gpt-5.5", "outcome": "completed", "provider": "openai-codex"},
            42.0,
        ),
    )
    out = filter_error_samples(samples)
    assert len(out) == 1
    [(key, value)] = out.items()
    assert key[0] == "openclaw_model_call_total"
    assert ("outcome", "error") in key[1]
    assert value == 3.0


def test_filter_keeps_harness_run_total_when_outcome_error() -> None:
    samples = _make_samples(
        ("openclaw_harness_run_total", {"outcome": "error", "harness": "codex"}, 1.0),
        ("openclaw_harness_run_total", {"outcome": "completed", "harness": "codex"}, 8.0),
    )
    out = filter_error_samples(samples)
    assert len(out) == 1


def test_filter_keeps_run_completed_total_when_outcome_error() -> None:
    """The catalog uses ``outcome`` here, not ``state`` (which belongs
    to the orthogonal ``session_state_total`` family). A filter mismatch
    here would silently drop every run-failure signal."""

    samples = _make_samples(
        ("openclaw_run_completed_total", {"outcome": "error"}, 2.0),
        ("openclaw_run_completed_total", {"outcome": "completed"}, 9.0),
    )
    out = filter_error_samples(samples)
    assert len(out) == 1
    [(key, value)] = out.items()
    assert ("outcome", "error") in key[1]
    assert value == 2.0


def test_filter_drops_run_completed_total_when_only_state_label() -> None:
    """Defense against regressions: the old (wrong) ``state=failed``
    label must NOT match — the catalog never emits this combination."""

    samples = _make_samples(
        ("openclaw_run_completed_total", {"state": "failed"}, 2.0),
    )
    assert filter_error_samples(samples) == {}


def test_filter_ignores_error_metric_without_outcome_label() -> None:
    """An error metric without the matching label is not an error sample."""
    samples = _make_samples(
        ("openclaw_model_call_total", {"model": "gpt-5.5"}, 5.0),
    )
    assert filter_error_samples(samples) == {}


def test_compute_rate_deltas_first_observation_returns_null_rate() -> None:
    current = _make_samples(
        ("openclaw_model_call_total", {"outcome": "error", "model": "gpt-5.5"}, 1.0),
    )
    rows = compute_rate_deltas(prior={}, current=current, elapsed_seconds=60.0)
    assert len(rows) == 1
    row = rows[0]
    assert row.counter_value == 1.0
    assert row.rate_per_second is None
    assert row.elapsed_seconds is None


def test_compute_rate_deltas_basic_delta() -> None:
    key = (
        "openclaw_model_call_total",
        frozenset({("outcome", "error"), ("model", "gpt-5.5")}),
    )
    prior = {key: PriorSample(counter_value=5.0)}
    current = {key: 8.0}
    rows = compute_rate_deltas(prior=prior, current=current, elapsed_seconds=60.0)
    assert len(rows) == 1
    row = rows[0]
    assert row.counter_value == 8.0
    assert row.rate_per_second == 0.05  # (8 - 5) / 60
    assert row.elapsed_seconds == 60.0


def test_compute_rate_deltas_counter_reset_returns_null_rate() -> None:
    """If the gateway restarted, the counter resets to zero. We must
    NOT report a negative rate; surface ``rate=None`` so the consumer
    can distinguish."""

    key = ("openclaw_model_call_total", frozenset({("outcome", "error")}))
    prior = {key: PriorSample(counter_value=10.0)}
    current = {key: 0.0}
    rows = compute_rate_deltas(prior=prior, current=current, elapsed_seconds=60.0)
    assert len(rows) == 1
    assert rows[0].rate_per_second is None
    assert rows[0].counter_value == 0.0


def test_compute_rate_deltas_unchanged_counter_yields_zero_rate() -> None:
    key = ("openclaw_model_call_total", frozenset({("outcome", "error")}))
    prior = {key: PriorSample(counter_value=10.0)}
    current = {key: 10.0}
    rows = compute_rate_deltas(prior=prior, current=current, elapsed_seconds=60.0)
    assert rows[0].rate_per_second == 0.0


def test_compute_rate_deltas_zero_elapsed_seconds_returns_null_rate() -> None:
    """Defensive: if the loop's wall clock claims zero elapsed, don't divide."""

    key = ("openclaw_model_call_total", frozenset({("outcome", "error")}))
    prior = {key: PriorSample(counter_value=5.0)}
    current = {key: 8.0}
    rows = compute_rate_deltas(prior=prior, current=current, elapsed_seconds=0.0)
    assert rows[0].rate_per_second is None


def test_error_metric_names_constant_includes_three_canonical_names() -> None:
    assert "openclaw_model_call_total" in ERROR_METRIC_NAMES
    assert "openclaw_harness_run_total" in ERROR_METRIC_NAMES
    assert "openclaw_run_completed_total" in ERROR_METRIC_NAMES


def test_delta_row_carries_metric_name_and_labels_dict() -> None:
    """``DeltaRow.labels`` should be a dict (not frozenset) for JSON storage."""

    key = (
        "openclaw_model_call_total",
        frozenset({("outcome", "error"), ("model", "gpt-5.5")}),
    )
    current = {key: 1.0}
    rows = compute_rate_deltas(prior={}, current=current, elapsed_seconds=60.0)
    row = rows[0]
    assert isinstance(row.labels, dict)
    assert row.labels == {"outcome": "error", "model": "gpt-5.5"}
    assert row.metric_name == "openclaw_model_call_total"
