# ruff: noqa: INP001
"""Unit tests for the factored gateway error mapper."""

from __future__ import annotations

from app.api.gateway import _map_gateway_error_common
from app.services.openclaw.gateway_rpc import OpenClawGatewayError


def test_common_extracts_canonical_fields_from_details() -> None:
    exc = OpenClawGatewayError(
        "down",
        details={"code": "UNAVAILABLE", "message": "gateway is down"},
    )
    out = _map_gateway_error_common(exc)
    assert out["code"] == "UNAVAILABLE"
    assert out["message"] == "gateway is down"
    assert out["lowered"] == "gateway is down"


def test_common_falls_back_to_str_when_details_missing() -> None:
    exc = OpenClawGatewayError("transport boom")
    out = _map_gateway_error_common(exc)
    assert out["code"] == ""
    assert out["message"] == "transport boom"
    assert out["request_id"] is None


def test_common_handles_method_not_supported_codes() -> None:
    for code in ("METHOD_NOT_FOUND", "METHOD_NOT_REGISTERED", "NOT_IMPLEMENTED"):
        exc = OpenClawGatewayError("nope", details={"code": code, "message": "nope"})
        assert _map_gateway_error_common(exc)["is_method_unsupported"] is True


def test_common_detects_method_unsupported_via_message_substring() -> None:
    exc = OpenClawGatewayError(
        "boom",
        details={"code": "INVALID_REQUEST", "message": "unknown method: device.pair.remove"},
    )
    assert _map_gateway_error_common(exc)["is_method_unsupported"] is True


def test_common_does_not_mark_random_invalid_request_as_unsupported() -> None:
    exc = OpenClawGatewayError(
        "boom",
        details={"code": "INVALID_REQUEST", "message": "bad input"},
    )
    assert _map_gateway_error_common(exc)["is_method_unsupported"] is False
