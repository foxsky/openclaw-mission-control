"""Connection-lifecycle + protocol tests for the gateway event subscriber.

Per the live `app/services/openclaw/gateway_rpc.py` client, the
OpenClaw gateway protocol is:

  - WebSocket transport, JSON messages.
  - Server sends ``{"type":"event","event":"connect.challenge",
    "payload":{"nonce":"..."}}`` immediately after handshake.
  - Client must reply with
    ``{"type":"req","id":<uuid>,"method":"connect","params":{...}}``.
  - Server replies with ``{"type":"res","id":<id>,"ok":true,...}``.
  - After ``connect``, client sends one ``req`` per subscription
    (e.g. ``sessions.subscribe``).
  - Events thereafter arrive as
    ``{"type":"event","event":"<name>","payload":{...}}``.

Subscriber MUST:
  T1. Connect, send the ``connect`` req with operator scopes, then send
      one subscribe req per configured subscription topic.
  T2. Survive a connection drop with exponential-backoff reconnect, and
      re-issue the connect handshake + subscriptions on each reconnect.
  T3. Stop cleanly when the caller sets ``asyncio.Event``.
  T4. Dispatch each incoming ``event`` message to handlers registered
      with ``.on(event_name, fn)``.
  T5. Survive handler exceptions; the next event still dispatches.
  T6. Silently skip events with no registered handler.

Per ``feedback_tdd_discipline``: this file expresses the contract the
``Subscriber`` MUST satisfy. Implementation can change so long as
these assertions still pass.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import pytest_asyncio
import websockets

from app.services.mc_gateway_subscriber.subscriber import Subscriber


@pytest_asyncio.fixture
async def stub_gateway():
    """Spin a websockets server that speaks the OpenClaw connect handshake.

    The server:
      1. On each new connection, immediately sends the ``connect.challenge``
         event with a random nonce.
      2. Receives the client's ``connect`` req and replies with a synthetic
         ok ``res``.
      3. Receives subscription reqs and replies with ok ``res``.
      4. Forwards arbitrary events queued by the test back to the client.

    Exposes a handle for tests to drive event delivery and inspect the
    sub-messages the client sent.
    """
    sent_to_server: list[dict[str, Any]] = []
    event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    connections: list[Any] = []
    headers_seen: list[dict[str, str]] = []

    async def handler(ws):
        connections.append(ws)
        headers_seen.append(dict(ws.request.headers))
        # 1. Send connect.challenge.
        await ws.send(json.dumps({
            "type": "event",
            "event": "connect.challenge",
            "payload": {"nonce": "stub-nonce-xyz"},
        }))

        async def receive_loop() -> None:
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    msg = json.loads(raw)
                    sent_to_server.append(msg)
                    # Reply ok to any req.
                    if msg.get("type") == "req":
                        await ws.send(json.dumps({
                            "type": "res",
                            "id": msg.get("id"),
                            "ok": True,
                            "payload": {"echo": msg.get("method")},
                        }))
            except websockets.exceptions.ConnectionClosed:
                pass

        async def event_driver() -> None:
            try:
                while True:
                    item = await event_queue.get()
                    if item is None:
                        await ws.close()
                        return
                    await ws.send(json.dumps(item))
            except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
                pass

        event_task = asyncio.create_task(event_driver())
        try:
            await receive_loop()
        finally:
            event_task.cancel()
            try:
                await event_task
            except (asyncio.CancelledError, Exception):
                pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    sock = next(iter(server.sockets))
    port = sock.getsockname()[1]

    class Handle:
        url = f"ws://127.0.0.1:{port}"

        @staticmethod
        async def push_event(event_name: str, payload: dict[str, Any] | None = None) -> None:
            await event_queue.put({
                "type": "event",
                "event": event_name,
                "payload": payload or {},
            })

        @staticmethod
        async def kick_connection() -> None:
            if connections:
                await connections[-1].close()

        @staticmethod
        def server_received() -> list[dict[str, Any]]:
            return list(sent_to_server)

        @staticmethod
        def connection_count() -> int:
            return len(connections)

        @staticmethod
        def headers_first() -> dict[str, str]:
            return headers_seen[0] if headers_seen else {}

    try:
        yield Handle
    finally:
        server.close()
        await server.wait_closed()


async def _wait_for(predicate, *, timeout: float = 2.0, interval: float = 0.02) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)


# --- T1: connect handshake + subscribe at start ---


@pytest.mark.asyncio
async def test_subscriber_sends_connect_then_subscribe(stub_gateway) -> None:
    """On connect, subscriber sends a ``connect`` req then one ``req`` per
    subscription topic, in order, with a unique ``id`` each.
    """
    handle = stub_gateway
    sub = Subscriber(
        url=handle.url,
        token="t",
        subscriptions=["sessions.subscribe", "sessions.messages.subscribe"],
        reconnect_initial_delay=0.05,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sub.run(stop))
    await _wait_for(lambda: len(handle.server_received()) >= 3)

    msgs = handle.server_received()
    assert msgs[0]["type"] == "req"
    assert msgs[0]["method"] == "connect"
    assert msgs[1]["type"] == "req"
    assert msgs[1]["method"] == "sessions.subscribe"
    assert msgs[2]["type"] == "req"
    assert msgs[2]["method"] == "sessions.messages.subscribe"
    # Distinct ids.
    ids = {m["id"] for m in msgs[:3]}
    assert len(ids) == 3

    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


# --- T2: bearer auth on handshake ---


@pytest.mark.asyncio
async def test_subscriber_sends_bearer_token_on_connect(stub_gateway) -> None:
    handle = stub_gateway
    sub = Subscriber(
        url=handle.url,
        token="my-secret-token",
        subscriptions=[],
        reconnect_initial_delay=0.05,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sub.run(stop))
    await _wait_for(lambda: bool(handle.headers_first()))
    auth = handle.headers_first().get("Authorization") or handle.headers_first().get("authorization")
    assert auth == "Bearer my-secret-token"
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


# --- T3: reconnect after drop, re-handshake + re-subscribe ---


@pytest.mark.asyncio
async def test_subscriber_reconnects_and_resubscribes(stub_gateway) -> None:
    handle = stub_gateway
    sub = Subscriber(
        url=handle.url,
        token="t",
        subscriptions=["sessions.subscribe"],
        reconnect_initial_delay=0.05,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sub.run(stop))
    # First connect: connect + subscribe = 2 reqs.
    await _wait_for(lambda: len(handle.server_received()) >= 2)
    assert handle.connection_count() == 1

    await handle.kick_connection()
    # Second connect: another connect + subscribe = 4 total.
    await _wait_for(lambda: handle.connection_count() >= 2 and len(handle.server_received()) >= 4)
    msgs = handle.server_received()
    methods = [m.get("method") for m in msgs if m.get("type") == "req"]
    # Order on each connect: connect, then subscribe.
    assert methods[:2] == ["connect", "sessions.subscribe"]
    assert methods[2:4] == ["connect", "sessions.subscribe"]

    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


# --- T4: dispatch event to registered handler ---


@pytest.mark.asyncio
async def test_subscriber_dispatches_event_by_name(stub_gateway) -> None:
    handle = stub_gateway
    received_events: list[dict[str, Any]] = []

    async def on_changed(payload: dict[str, Any]) -> None:
        received_events.append(payload)

    sub = Subscriber(
        url=handle.url,
        token="t",
        subscriptions=["sessions.subscribe"],
        reconnect_initial_delay=0.05,
    )
    sub.on("sessions.changed", on_changed)
    stop = asyncio.Event()
    task = asyncio.create_task(sub.run(stop))
    await _wait_for(lambda: handle.connection_count() >= 1)

    await handle.push_event("sessions.changed", {"agent": "lead-x"})
    await _wait_for(lambda: len(received_events) >= 1)

    assert received_events[0]["event"] == "sessions.changed"
    assert received_events[0]["payload"]["agent"] == "lead-x"

    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


# --- T5: handler exception doesn't crash subscriber ---


@pytest.mark.asyncio
async def test_subscriber_handler_exception_does_not_crash(stub_gateway) -> None:
    handle = stub_gateway
    call_count = 0

    async def bad_handler(payload: dict[str, Any]) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("simulated bug")

    sub = Subscriber(
        url=handle.url,
        token="t",
        subscriptions=[],
        reconnect_initial_delay=0.05,
    )
    sub.on("sessions.changed", bad_handler)
    stop = asyncio.Event()
    task = asyncio.create_task(sub.run(stop))
    await _wait_for(lambda: handle.connection_count() >= 1)

    await handle.push_event("sessions.changed", {"i": 1})
    await handle.push_event("sessions.changed", {"i": 2})
    await _wait_for(lambda: call_count >= 2)

    assert not task.done()
    assert call_count >= 2

    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


# --- T6: unknown events silently skipped ---


@pytest.mark.asyncio
async def test_subscriber_skips_unknown_events(stub_gateway) -> None:
    handle = stub_gateway
    seen: list[dict[str, Any]] = []

    async def only_changed(payload: dict[str, Any]) -> None:
        seen.append(payload)

    sub = Subscriber(
        url=handle.url,
        token="t",
        subscriptions=[],
        reconnect_initial_delay=0.05,
    )
    sub.on("sessions.changed", only_changed)
    stop = asyncio.Event()
    task = asyncio.create_task(sub.run(stop))
    await _wait_for(lambda: handle.connection_count() >= 1)

    await handle.push_event("presence", {"x": 1})
    await handle.push_event("sessions.changed", {"x": 2})
    await handle.push_event("tick", {"x": 3})
    await _wait_for(lambda: len(seen) >= 1)

    assert len(seen) == 1
    assert seen[0]["event"] == "sessions.changed"

    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


# --- T7: connect.challenge nonce is reflected in connect req ---


@pytest.mark.asyncio
async def test_subscriber_echoes_connect_nonce(stub_gateway) -> None:
    """The gateway sends a nonce on ``connect.challenge``; the client's
    ``connect`` req must include it under ``params.connectNonce`` so the
    server can correlate the handshake.
    """
    handle = stub_gateway
    sub = Subscriber(
        url=handle.url,
        token="t",
        subscriptions=[],
        reconnect_initial_delay=0.05,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sub.run(stop))
    await _wait_for(lambda: len(handle.server_received()) >= 1)
    connect_req = handle.server_received()[0]
    assert connect_req["method"] == "connect"
    params = connect_req.get("params", {})
    assert params.get("connectNonce") == "stub-nonce-xyz"
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
