# ruff: noqa: INP001
"""Tests for ``scrape_gateway_metrics`` — turns a gateway WS URL into the
matching HTTP base, calls ``/api/diagnostics/prometheus`` with the gateway
token, and returns parsed samples."""

from __future__ import annotations

import httpx
import pytest

from app.services.openclaw.observability_poller import (
    gateway_http_base,
    scrape_gateway_metrics,
)


@pytest.mark.parametrize(
    ("ws_url", "expected"),
    [
        ("ws://192.168.2.60:18789", "http://192.168.2.60:18789"),
        ("wss://gateway.example.com", "https://gateway.example.com"),
        ("http://localhost:1234", "http://localhost:1234"),
        ("https://gateway.example.com/path", "https://gateway.example.com/path"),
    ],
)
def test_gateway_http_base_normalizes_scheme(ws_url: str, expected: str) -> None:
    assert gateway_http_base(ws_url) == expected


@pytest.mark.asyncio
async def test_scrape_parses_endpoint_response() -> None:
    body = (
        "# HELP openclaw_a foo\n"
        "openclaw_a 5\n"
        'openclaw_b{x="y"} 2.5\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/diagnostics/prometheus"
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await scrape_gateway_metrics(
            gateway_url="ws://192.168.2.60:18789",
            gateway_token="test-token",
            client=client,
        )
    assert out == {
        ("openclaw_a", frozenset()): 5.0,
        ("openclaw_b", frozenset({("x", "y")})): 2.5,
    }


@pytest.mark.asyncio
async def test_scrape_raises_on_non_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await scrape_gateway_metrics(
                gateway_url="ws://192.168.2.60:18789",
                gateway_token="bad-token",
                client=client,
            )
