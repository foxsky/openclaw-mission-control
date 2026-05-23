# ruff: noqa: INP001
"""Schema + projection unit tests for the gateway devices endpoint."""

from __future__ import annotations

from uuid import uuid4

from app.api.gateway import _project_gateway_device
from app.schemas.gateway_api import GatewayDeviceListResponse


def test_project_flattens_tokens_into_scopes_union_and_last_used() -> None:
    raw = {
        "deviceId": "abc123",
        "publicKey": "PUBKEYBASE64URL",
        "platform": "linux",
        "clientId": "cli",
        "clientMode": "cli",
        "role": "operator",
        "scopes": ["operator.admin"],
        "remoteIp": "192.168.2.64",
        "approvedAtMs": 1000,
        "tokens": [
            {
                "role": "operator",
                "scopes": ["operator.read", "operator.admin"],
                "createdAtMs": 1000,
                "lastUsedAtMs": 1500,
            },
            {
                "role": "operator",
                "scopes": ["operator.write"],
                "createdAtMs": 1100,
                "lastUsedAtMs": 1200,
            },
        ],
    }
    out = _project_gateway_device(raw, local_device_id="other-device")
    assert out.device_id == "abc123"
    assert out.token_count == 2
    assert sorted(out.scopes) == ["operator.admin", "operator.read", "operator.write"]
    assert out.last_used_at_ms == 1500
    assert out.is_self is False


def test_project_marks_is_self_when_device_id_matches_local() -> None:
    raw = {"deviceId": "ME", "publicKey": "K", "tokens": []}
    out = _project_gateway_device(raw, local_device_id="ME")
    assert out.is_self is True


def test_project_handles_missing_tokens_and_missing_last_used() -> None:
    raw = {"deviceId": "x", "publicKey": "K"}
    out = _project_gateway_device(raw, local_device_id=None)
    assert out.token_count == 0
    assert out.scopes == []
    assert out.last_used_at_ms is None
    assert out.is_self is False


def test_response_round_trips_alias_keys() -> None:
    payload = {
        "gateway_id": uuid4(),
        "devices": [
            {
                "deviceId": "x",
                "publicKey": "K",
                "tokenCount": 1,
                "lastUsedAtMs": 100,
                "isSelf": False,
            }
        ],
    }
    resp = GatewayDeviceListResponse.model_validate(payload)
    assert resp.devices[0].token_count == 1
    assert resp.devices[0].is_self is False
