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
  (capped). Backoff resets only after a connection has been alive long
  enough to be considered healthy — guards against post-handshake
  immediate-close storms (e.g. bad token, role mismatch).
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

from app.services.openclaw.gateway_rpc import (
    GATEWAY_OPERATOR_SCOPES,
    PROTOCOL_VERSION,
)

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

# OpenClaw gateway protocol frame types and well-known event/method
# names. Defined here as constants so a typo in a string literal
# anywhere in this module surfaces at import time, not at runtime.
FRAME_TYPE_REQ = "req"
FRAME_TYPE_RES = "res"
FRAME_TYPE_EVENT = "event"
EVENT_CONNECT_CHALLENGE = "connect.challenge"
METHOD_CONNECT = "connect"

# Connect-frame defaults. Role + scopes match what
# ``gateway_rpc._build_connect_params`` sends for operator clients;
# ``OPERATOR_ROLE`` is mirrored here as a string because the upstream
# definition is a function-local literal.
OPERATOR_ROLE = "operator"

# A connection that survives this many seconds is "healthy" for backoff
# purposes — only such connections reset the reconnect delay. Picked
# to be longer than a typical bad-token close (which happens within
# ~1s of the handshake) but short enough that legitimate slow streams
# still get the benefit of a reset.
HEALTHY_CONNECTION_SECONDS = 5.0

_HANDSHAKE_TIMEOUT_SECONDS = 5.0


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
            Defaults to the upstream constant from ``gateway_rpc``.
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
        protocol_version: int = PROTOCOL_VERSION,
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
        loop = asyncio.get_event_loop()
        while not stop.is_set():
            connection_started_at = loop.time()
            try:
                async with websockets.connect(
                    self._url,
                    additional_headers={"Authorization": f"Bearer {self._token}"},
                ) as ws:
                    if not await self._handshake_and_subscribe(ws):
                        # Bad challenge / silent gateway / non-protocol
                        # first frame. Don't subscribe; let the WS
                        # close and back off.
                        continue
                    await self._listen(ws, stop)
            except ConnectionClosed:
                logger.info("gateway WS connection closed; will reconnect")
            except Exception:
                logger.exception("gateway WS connect failed; will retry")
            if stop.is_set():
                return
            # Only reset backoff after a connection that lived long
            # enough to be considered healthy. Otherwise an immediate
            # post-handshake close (bad token, scope mismatch) would
            # hammer the gateway forever at the initial delay.
            connection_uptime = loop.time() - connection_started_at
            if connection_uptime >= HEALTHY_CONNECTION_SECONDS:
                delay = self._initial_delay
            await self._sleep_with_stop(delay, stop)
            delay = min(delay * 2, self._max_delay)

    # --- handshake + subscribe ---

    async def _handshake_and_subscribe(self, ws: Any) -> bool:
        """Receive ``connect.challenge``, send ``connect`` req with the
        echoed nonce, then send one ``req`` per subscription.

        Returns ``True`` iff the handshake completed against a valid
        ``connect.challenge`` frame; ``False`` if the gateway timed out,
        sent non-JSON, or sent a frame that wasn't ``connect.challenge``.
        On ``False``, the caller MUST NOT send any subscribe reqs and
        SHOULD close the connection (let the outer loop reconnect with
        backoff).
        """
        try:
            first_raw = await asyncio.wait_for(ws.recv(), timeout=_HANDSHAKE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("gateway did not send %s within %ss",
                           EVENT_CONNECT_CHALLENGE, _HANDSHAKE_TIMEOUT_SECONDS)
            return False
        try:
            first = json.loads(first_raw if isinstance(first_raw, str) else first_raw.decode())
        except json.JSONDecodeError:
            logger.warning("gateway sent non-JSON first frame; aborting handshake")
            return False
        if not (
            isinstance(first, dict)
            and first.get("type") == FRAME_TYPE_EVENT
            and first.get("event") == EVENT_CONNECT_CHALLENGE
        ):
            logger.warning(
                "gateway first frame was not %s (got type=%s event=%s); aborting handshake",
                EVENT_CONNECT_CHALLENGE,
                first.get("type") if isinstance(first, dict) else type(first).__name__,
                first.get("event") if isinstance(first, dict) else None,
            )
            return False
        connect_nonce: str | None = None
        payload = first.get("payload")
        if isinstance(payload, dict):
            nonce = payload.get("nonce")
            if isinstance(nonce, str) and nonce.strip():
                connect_nonce = nonce.strip()

        connect_params: dict[str, Any] = {
            "minProtocol": self._protocol_version,
            "maxProtocol": self._protocol_version,
            "role": OPERATOR_ROLE,
            "scopes": list(GATEWAY_OPERATOR_SCOPES),
            "client": {
                "id": self._client_id,
                "version": "1.0.0",
                "mode": "subscriber",
            },
            "auth": {"token": self._token},
        }
        if connect_nonce is not None:
            connect_params["connectNonce"] = connect_nonce
        await self._send_req(ws, METHOD_CONNECT, connect_params)
        for method in self._subscriptions:
            await self._send_req(ws, method, {})
        return True

    async def _send_req(self, ws: Any, method: str, params: dict[str, Any]) -> None:
        message = {
            "type": FRAME_TYPE_REQ,
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
                    except (asyncio.CancelledError, ConnectionClosed):
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
        if payload.get("type") != FRAME_TYPE_EVENT:
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
