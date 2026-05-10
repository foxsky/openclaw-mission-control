# ruff: noqa: INP001

from __future__ import annotations

from typing import Any

import pytest

from app.services.openclaw import gateway_dispatch
from app.services.openclaw.gateway_dispatch import GatewayDispatchService
from app.services.openclaw.gateway_rpc import GatewayConfig


@pytest.mark.asyncio
async def test_send_agent_message_uses_steer_when_interrupt_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_ensure_session(
        session_key: str,
        *,
        config: GatewayConfig,
        label: str | None = None,
    ) -> None:
        calls.append(("ensure", {"session_key": session_key, "label": label}))

    async def _fake_send_message(
        message: str,
        *,
        session_key: str,
        config: GatewayConfig,
        deliver: bool = False,
    ) -> None:
        calls.append(
            (
                "send",
                {"message": message, "session_key": session_key, "deliver": deliver},
            )
        )

    async def _fake_steer_session(
        message: str,
        *,
        session_key: str,
        config: GatewayConfig,
        deliver: bool = False,
    ) -> None:
        calls.append(
            (
                "steer",
                {"message": message, "session_key": session_key, "deliver": deliver},
            )
        )

    monkeypatch.setattr(gateway_dispatch, "ensure_session", _fake_ensure_session)
    monkeypatch.setattr(gateway_dispatch, "send_message", _fake_send_message)
    monkeypatch.setattr(gateway_dispatch, "steer_session", _fake_steer_session)

    await GatewayDispatchService(session=object()).send_agent_message(  # type: ignore[arg-type]
        session_key="agent:pf:session",
        config=GatewayConfig(url="https://gateway.example.local"),
        agent_name="Programmer-Frontend",
        message="TASK MENTION",
        deliver=True,
        interrupt_if_active=True,
    )

    assert calls == [
        ("ensure", {"session_key": "agent:pf:session", "label": "Programmer-Frontend"}),
        (
            "steer",
            {
                "message": "TASK MENTION",
                "session_key": "agent:pf:session",
                "deliver": True,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_send_agent_message_uses_chat_send_without_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_ensure_session(
        session_key: str,
        *,
        config: GatewayConfig,
        label: str | None = None,
    ) -> None:
        calls.append(("ensure", {"session_key": session_key, "label": label}))

    async def _fake_send_message(
        message: str,
        *,
        session_key: str,
        config: GatewayConfig,
        deliver: bool = False,
    ) -> None:
        calls.append(
            (
                "send",
                {"message": message, "session_key": session_key, "deliver": deliver},
            )
        )

    async def _fake_steer_session(
        message: str,
        *,
        session_key: str,
        config: GatewayConfig,
        deliver: bool = False,
    ) -> None:
        calls.append(
            (
                "steer",
                {"message": message, "session_key": session_key, "deliver": deliver},
            )
        )

    monkeypatch.setattr(gateway_dispatch, "ensure_session", _fake_ensure_session)
    monkeypatch.setattr(gateway_dispatch, "send_message", _fake_send_message)
    monkeypatch.setattr(gateway_dispatch, "steer_session", _fake_steer_session)

    await GatewayDispatchService(session=object()).send_agent_message(  # type: ignore[arg-type]
        session_key="agent:pf:session",
        config=GatewayConfig(url="https://gateway.example.local"),
        agent_name="Programmer-Frontend",
        message="NEW TASK COMMENT",
        deliver=False,
    )

    assert calls == [
        ("ensure", {"session_key": "agent:pf:session", "label": "Programmer-Frontend"}),
        (
            "send",
            {
                "message": "NEW TASK COMMENT",
                "session_key": "agent:pf:session",
                "deliver": False,
            },
        ),
    ]
