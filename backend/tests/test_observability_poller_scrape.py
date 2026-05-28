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
        # Path/query are intentionally dropped — the diagnostics endpoint
        # lives at the host root, NOT under the gateway's WS path.
        ("ws://gateway.example/ws", "http://gateway.example"),
        ("wss://gateway.example.com/ws/path?token=x", "https://gateway.example.com"),
    ],
)
def test_gateway_http_base_normalizes_scheme(ws_url: str, expected: str) -> None:
    assert gateway_http_base(ws_url) == expected


@pytest.mark.asyncio
async def test_scrape_strips_ws_path_before_hitting_diagnostics() -> None:
    """Regression: a gateway URL with a path segment (e.g. ``/ws``)
    must NOT cause the diagnostics endpoint to be looked up under
    that path."""

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, text="openclaw_a 1\n")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await scrape_gateway_metrics(
            gateway_url="ws://gateway.example/ws",
            gateway_token="t",
            client=client,
        )
    assert out == {("openclaw_a", frozenset()): 1.0}
    assert captured["url"] == "http://gateway.example/api/diagnostics/prometheus"


@pytest.mark.asyncio
async def test_scrape_passes_allow_insecure_tls_flag() -> None:
    """When ``allow_insecure_tls=True`` and a fresh client is created,
    ``verify=False`` should be configured. We test the public signature
    by passing a pre-built client and ensuring the call still succeeds —
    the flag is wired into the auto-created branch only."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await scrape_gateway_metrics(
            gateway_url="wss://gateway.example",
            gateway_token="t",
            client=client,
            allow_insecure_tls=True,
        )
    assert out == {}


@pytest.mark.asyncio
async def test_scrape_parses_endpoint_response() -> None:
    body = "# HELP openclaw_a foo\n" "openclaw_a 5\n" 'openclaw_b{x="y"} 2.5\n'

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
