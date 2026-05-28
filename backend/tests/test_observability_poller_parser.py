# ruff: noqa: INP001
"""Unit tests for the Prometheus text-format parser used by the gateway
observability poller."""

from __future__ import annotations

from app.services.openclaw.observability_poller import parse_prometheus_text


def test_parser_skips_comment_lines() -> None:
    text = "# HELP openclaw_foo bar\n# TYPE openclaw_foo counter\n"
    assert parse_prometheus_text(text) == {}


def test_parser_skips_blank_lines() -> None:
    text = "\n\n   \n"
    assert parse_prometheus_text(text) == {}


def test_parser_reads_simple_counter_no_labels() -> None:
    text = "openclaw_foo_total 42\n"
    out = parse_prometheus_text(text)
    assert out == {("openclaw_foo_total", frozenset()): 42.0}


def test_parser_reads_counter_with_single_label() -> None:
    text = 'openclaw_foo_total{reason="event_loop_delay"} 7\n'
    out = parse_prometheus_text(text)
    key = ("openclaw_foo_total", frozenset({("reason", "event_loop_delay")}))
    assert out == {key: 7.0}


def test_parser_reads_counter_with_multiple_labels() -> None:
    text = (
        'openclaw_model_call_total{api="openai-codex-responses",'
        'error_category="error",model="gpt-nonexistent",'
        'outcome="error",provider="openai-codex",transport="stdio"} 1\n'
    )
    out = parse_prometheus_text(text)
    expected_labels = frozenset(
        {
            ("api", "openai-codex-responses"),
            ("error_category", "error"),
            ("model", "gpt-nonexistent"),
            ("outcome", "error"),
            ("provider", "openai-codex"),
            ("transport", "stdio"),
        }
    )
    assert out == {("openclaw_model_call_total", expected_labels): 1.0}


def test_parser_reads_float_values() -> None:
    text = "openclaw_some_duration_seconds_sum 6.171\n"
    out = parse_prometheus_text(text)
    assert out == {("openclaw_some_duration_seconds_sum", frozenset()): 6.171}


def test_parser_handles_multi_family_mixed_block() -> None:
    text = (
        "# HELP openclaw_a Bar\n"
        "# TYPE openclaw_a counter\n"
        "openclaw_a 1\n"
        "openclaw_b{x=\"y\"} 2.5\n"
        "\n"
        '# HELP openclaw_c\nopenclaw_c{x="y",z="w"} 3\n'
    )
    out = parse_prometheus_text(text)
    assert out == {
        ("openclaw_a", frozenset()): 1.0,
        ("openclaw_b", frozenset({("x", "y")})): 2.5,
        ("openclaw_c", frozenset({("x", "y"), ("z", "w")})): 3.0,
    }


def test_parser_silently_skips_malformed_lines() -> None:
    text = (
        "openclaw_good 1\n"
        "this is not a metric\n"
        "openclaw_also_good{x=\"y\"} 2\n"
        "openclaw_no_value\n"
    )
    out = parse_prometheus_text(text)
    assert out == {
        ("openclaw_good", frozenset()): 1.0,
        ("openclaw_also_good", frozenset({("x", "y")})): 2.0,
    }


def test_parser_handles_histogram_bucket_lines() -> None:
    """Histogram bucket lines are valid samples — parser should not special-case them."""
    text = (
        'openclaw_h_bucket{le="0.005",reason="event_loop_delay"} 0\n'
        'openclaw_h_bucket{le="+Inf",reason="event_loop_delay"} 1\n'
        'openclaw_h_count{reason="event_loop_delay"} 1\n'
        'openclaw_h_sum{reason="event_loop_delay"} 1.9755\n'
    )
    out = parse_prometheus_text(text)
    assert len(out) == 4
    assert (
        out[("openclaw_h_bucket", frozenset({("le", "+Inf"), ("reason", "event_loop_delay")}))]
        == 1.0
    )
    assert (
        out[("openclaw_h_sum", frozenset({("reason", "event_loop_delay")}))] == 1.9755
    )
