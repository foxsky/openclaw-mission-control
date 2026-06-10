# ruff: noqa: S101
"""Tests for the gateway auth-expiry early-warning check.

``models.authStatus`` (gateway 4.15+) reports per-provider OAuth profile
expiry. The poller evaluates each snapshot and logs throttled WARNINGs so
an expiring primary-provider credential is visible BEFORE the fleet
starts failing over (the historical failure mode was discovering expiry
from a 429/error storm).
"""

from __future__ import annotations

from app.services.openclaw.observability_poller import (
    _should_log_auth_alert,
    evaluate_auth_alerts,
)

HOUR_MS = 3_600_000
WARN_BELOW = 48 * HOUR_MS


def _snapshot(providers: list[dict]) -> dict:
    return {"ts": 1781127881278, "providers": providers}


def test_expired_oauth_profile_alerts() -> None:
    snap = _snapshot(
        [
            {
                "provider": "openai",
                "status": "expired",
                "profiles": [
                    {
                        "profileId": "openai:dead@example.com",
                        "type": "oauth",
                        "status": "expired",
                        "expiry": {"at": 1, "remainingMs": -2_526_381_320, "label": "0m"},
                    },
                    {
                        "profileId": "openai:ok@example.com",
                        "type": "oauth",
                        "status": "ok",
                        "expiry": {"at": 2, "remainingMs": 9 * 24 * HOUR_MS, "label": "9d"},
                    },
                ],
            }
        ]
    )
    alerts = evaluate_auth_alerts(snap, warn_below_ms=WARN_BELOW)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "expired"
    assert alerts[0]["provider"] == "openai"
    assert alerts[0]["profile_id"] == "openai:dead@example.com"


def test_expiring_soon_profile_alerts() -> None:
    snap = _snapshot(
        [
            {
                "provider": "openai",
                "status": "ok",
                "profiles": [
                    {
                        "profileId": "openai:soon@example.com",
                        "type": "oauth",
                        "status": "ok",
                        "expiry": {"at": 3, "remainingMs": 12 * HOUR_MS, "label": "12h"},
                    }
                ],
            }
        ]
    )
    alerts = evaluate_auth_alerts(snap, warn_below_ms=WARN_BELOW)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "expiring"
    assert alerts[0]["remaining_ms"] == 12 * HOUR_MS


def test_all_profiles_expired_escalates_to_provider_level() -> None:
    snap = _snapshot(
        [
            {
                "provider": "openai",
                "status": "expired",
                "profiles": [
                    {
                        "profileId": "openai:a",
                        "type": "oauth",
                        "status": "expired",
                        "expiry": {"at": 1, "remainingMs": -10, "label": "0m"},
                    },
                    {
                        "profileId": "openai:b",
                        "type": "oauth",
                        "status": "expired",
                        "expiry": {"at": 1, "remainingMs": -20, "label": "0m"},
                    },
                ],
            }
        ]
    )
    alerts = evaluate_auth_alerts(snap, warn_below_ms=WARN_BELOW)
    severities = [a["severity"] for a in alerts]
    assert severities.count("expired") == 2
    assert "provider_expired" in severities
    provider_alert = next(a for a in alerts if a["severity"] == "provider_expired")
    assert provider_alert["profile_id"] is None


def test_healthy_and_static_profiles_do_not_alert() -> None:
    snap = _snapshot(
        [
            {
                "provider": "anthropic",
                "status": "ok",
                "profiles": [
                    {"profileId": "anthropic:manual", "type": "token", "status": "static"}
                ],
            },
            {
                "provider": "openai",
                "status": "ok",
                "profiles": [
                    {
                        "profileId": "openai:fine",
                        "type": "oauth",
                        "status": "ok",
                        "expiry": {"at": 2, "remainingMs": 9 * 24 * HOUR_MS, "label": "9d"},
                    }
                ],
            },
        ]
    )
    assert evaluate_auth_alerts(snap, warn_below_ms=WARN_BELOW) == []


def test_malformed_snapshots_yield_no_alerts() -> None:
    assert evaluate_auth_alerts(None, warn_below_ms=WARN_BELOW) == []
    assert evaluate_auth_alerts({}, warn_below_ms=WARN_BELOW) == []
    assert evaluate_auth_alerts({"providers": "nope"}, warn_below_ms=WARN_BELOW) == []
    assert (
        evaluate_auth_alerts(
            _snapshot([{"provider": "x", "profiles": [{"type": "oauth"}]}]),
            warn_below_ms=WARN_BELOW,
        )
        == []
    )


def test_alert_log_throttle() -> None:
    state: dict = {}
    key = ("gw1", "openai", "openai:a", "expired")
    assert _should_log_auth_alert(key, now=1000.0, rewarn_seconds=1800, state=state)
    # immediately again -> suppressed
    assert not _should_log_auth_alert(key, now=1010.0, rewarn_seconds=1800, state=state)
    # after the rewarn window -> logs again
    assert _should_log_auth_alert(key, now=1000.0 + 1801, rewarn_seconds=1800, state=state)
    # different key unaffected
    other = ("gw1", "openai", "openai:b", "expired")
    assert _should_log_auth_alert(other, now=1010.0, rewarn_seconds=1800, state=state)
