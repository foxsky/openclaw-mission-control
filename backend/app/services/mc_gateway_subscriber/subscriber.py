"""WebSocket subscriber for OpenClaw gateway events.

Implements the gateway connect handshake observed in
``app/services/openclaw/gateway_rpc.py`` (frame shapes: ``req``,
``res``, ``event``; first message from the gateway is
``connect.challenge`` with a nonce). After the handshake, sends each
configured subscription as its own ``req`` and then loops on incoming
``event`` messages, dispatching to handlers registered with
``.on(event_name, fn)``.

Designed to run as a long-lived asyncio task inside a dedicated
``mc-gateway-subscriber`` worker process.

Failure model:
- Handler exceptions are logged and the loop continues.
- WS connection drops trigger reconnect with exponential backoff
  (capped). Backoff resets on each successful connect.
- On reconnect, the connect handshake AND all subscriptions are
  re-issued (gateway state isn't persisted across the WS lifetime).
- Caller signals shutdown by setting an ``asyncio.Event``; the
  subscriber closes its WS and returns from ``run()`` cleanly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import uuid4

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

# Operator role + scopes match what gateway_rpc.py sends from MC. Keeps
# the subscriber's auth surface symmetric with the existing one-shot
# RPC client.
_DEFAULT_ROLE = "operator"
_DEFAULT_SCOPES = (
    "operator.read",
    "operator.admin",
    "operator.approvals",
    "operator.pairing",
)
_DEFAULT_PROTOCOL_VERSION = 3


class Subscriber:
    """Persistent WebSocket consumer for OpenClaw gateway events.

    Args:
        url: ``ws://`` or ``wss://`` URL of the gateway WS endpoint.
        token: bearer token sent as ``Authorization: Bearer <token>``
            on the WS handshake AND embedded in the ``connect`` req's
            ``params.auth.token`` field.
        subscriptions: ordered list of subscription RPC method names
            (e.g. ``"sessions.subscribe"``) to send after the
            handshake.
        reconnect_initial_delay: first backoff duration in seconds.
        reconnect_max_delay: cap on backoff duration in seconds.
        client_id: identifier announced in the connect payload.
        protocol_version: min/max protocol version sent in connect.
    """

    def __init__(
        self,
        *,
        url: str,
        token: str,
        subscriptions: Sequence[str] = (),
        reconnect_initial_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
        client_id: str = "mc-gateway-subscriber",
        protocol_version: int = _DEFAULT_PROTOCOL_VERSION,
    ) -> None:
        self._url = url
        self._token = token
        self._subscriptions = tuple(subscriptions)
        self._initial_delay = reconnect_initial_delay
        self._max_delay = reconnect_max_delay
        self._client_id = client_id
        self._protocol_version = protocol_version
        self._handlers: dict[str, EventHandler] = {}

    def on(self, event_name: str, handler: EventHandler) -> None:
        """Register an async handler for events whose ``event`` field matches."""
        self._handlers[event_name] = handler

    async def run(self, stop: asyncio.Event) -> None:
        """Connect, handshake, subscribe, listen, dispatch — until ``stop``."""
        delay = self._initial_delay
        while not stop.is_set():
            try:
                async with websockets.connect(
                    self._url,
                    additional_headers={"Authorization": f"Bearer {self._token}"},
                ) as ws:
                    await self._handshake_and_subscribe(ws)
                    delay = self._initial_delay
                    await self._listen(ws, stop)
            except ConnectionClosed:
                logger.info("gateway WS connection closed; will reconnect")
            except Exception:
                logger.exception("gateway WS connect failed; will retry")
            if stop.is_set():
                return
            await self._sleep_with_stop(delay, stop)
            delay = min(delay * 2, self._max_delay)

    # --- handshake + subscribe ---

    async def _handshake_and_subscribe(self, ws: Any) -> None:
        """Receive the ``connect.challenge`` event, send the ``connect`` req
        with the echoed nonce, then send one ``req`` per subscription.
        """
        # 1. Wait for connect.challenge (with timeout so a misbehaving
        #    gateway can't pin us forever pre-auth).
        connect_nonce: str | None = None
        try:
            first_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("gateway did not send connect.challenge within 5s")
            return
        try:
            first = json.loads(first_raw if isinstance(first_raw, str) else first_raw.decode())
        except json.JSONDecodeError:
            logger.warning("gateway sent non-JSON first frame; aborting")
            return
        if (
            isinstance(first, dict)
            and first.get("type") == "event"
            and first.get("event") == "connect.challenge"
        ):
            payload = first.get("payload")
            if isinstance(payload, dict):
                nonce = payload.get("nonce")
                if isinstance(nonce, str) and nonce.strip():
                    connect_nonce = nonce.strip()

        # 2. Send connect req with the echoed nonce.
        connect_params: dict[str, Any] = {
            "minProtocol": self._protocol_version,
            "maxProtocol": self._protocol_version,
            "role": _DEFAULT_ROLE,
            "scopes": list(_DEFAULT_SCOPES),
            "client": {
                "id": self._client_id,
                "version": "1.0.0",
                "mode": "subscriber",
            },
            "auth": {"token": self._token},
        }
        if connect_nonce is not None:
            connect_params["connectNonce"] = connect_nonce
        await self._send_req(ws, "connect", connect_params)

        # 3. Send each subscription as its own req.
        for method in self._subscriptions:
            await self._send_req(ws, method, {})

    async def _send_req(self, ws: Any, method: str, params: dict[str, Any]) -> None:
        message = {
            "type": "req",
            "id": str(uuid4()),
            "method": method,
            "params": params,
        }
        await ws.send(json.dumps(message))

    # --- listen + dispatch ---

    async def _listen(self, ws: Any, stop: asyncio.Event) -> None:
        recv_task = asyncio.create_task(self._recv_loop(ws))
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait(
                {recv_task, stop_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                await ws.close()
        finally:
            for t in (recv_task, stop_task):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

    async def _recv_loop(self, ws: Any) -> None:
        async for raw in ws:
            await self._dispatch(raw)

    async def _dispatch(self, raw: Any) -> None:
        text = raw if isinstance(raw, str) else raw.decode(errors="replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("dropping non-JSON message: %s", text[:200])
            return
        if not isinstance(payload, dict):
            return
        # Only dispatch events; ignore res frames left over from
        # subscribe-style req/res cycles after the handshake.
        if payload.get("type") != "event":
            return
        event_name = payload.get("event")
        if not isinstance(event_name, str):
            return
        handler = self._handlers.get(event_name)
        if handler is None:
            return
        try:
            await handler(payload)
        except Exception:
            logger.exception("handler for %s raised; continuing", event_name)

    async def _sleep_with_stop(self, seconds: float, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
