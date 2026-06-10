# ruff: noqa: S101
"""Tests for the post-sync lead message-tool smoke check.

MC seeds ``tools.alsoAllow: ["message"]`` on lead-* agents so the
Supervisor can reply on WhatsApp/Discord. If that grant is ever lost
(config drift, gateway upgrade), the symptom is a silently mute
Supervisor. The sweep now asserts the grant via ``tools.effective``
on the lead's stable heartbeat session and logs a WARNING when the
``message`` tool is absent.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

import app.services.openclaw.heartbeat_sweep as sweep
from app.services.openclaw.heartbeat_sweep import (
    _collect_effective_tool_ids,
    check_lead_message_tools_once,
)


def _payload(tool_ids: list[str]) -> dict:
    return {
        "agentId": "lead-x",
        "profile": "coding",
        "groups": [
            {
                "id": "core",
                "label": "Built-in tools",
                "source": "core",
                "tools": [{"id": tid, "label": tid, "source": "core"} for tid in tool_ids],
            },
        ],
        "notices": [],
    }


def test_collect_tool_ids_from_groups() -> None:
    ids = _collect_effective_tool_ids(_payload(["cron", "message", "apply_patch"]))
    assert ids == {"cron", "message", "apply_patch"}


def test_collect_tool_ids_indeterminate_shapes() -> None:
    assert _collect_effective_tool_ids(None) is None
    assert _collect_effective_tool_ids("nope") is None
    assert _collect_effective_tool_ids({}) is None
    assert _collect_effective_tool_ids({"groups": "nope"}) is None
    # malformed entries are tolerated, valid ones still collected
    ids = _collect_effective_tool_ids(
        {"groups": [{"tools": [{"id": "message"}, "junk", {"label": "noid"}]}, "junk"]}
    )
    assert ids == {"message"}


@pytest.mark.asyncio
async def test_check_warns_when_message_tool_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    board_id = uuid4()

    async def _fake_board_ids():
        return [board_id]

    async def _fake_config(_board_id):
        return object()

    async def _fake_tools_effective(session_key, *, config):
        assert session_key == f"agent:lead-{board_id}:main:heartbeat"
        return _payload(["cron", "apply_patch"])  # no message tool

    monkeypatch.setattr(sweep, "_fetch_board_ids_for_lead_check", _fake_board_ids)
    monkeypatch.setattr(sweep, "_gateway_config_for_board_id", _fake_config)
    monkeypatch.setattr(sweep, "get_tools_effective", _fake_tools_effective)

    with caplog.at_level(logging.WARNING):
        result = await check_lead_message_tools_once()

    assert result == {"checked": 1, "missing": 1}
    warning = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning) == 1
    assert "message" in warning[0].getMessage()
    assert str(board_id) in warning[0].getMessage()


@pytest.mark.asyncio
async def test_check_silent_when_message_tool_present(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    board_id = uuid4()

    async def _fake_board_ids():
        return [board_id]

    async def _fake_config(_board_id):
        return object()

    async def _fake_tools_effective(session_key, *, config):
        return _payload(["cron", "message"])

    monkeypatch.setattr(sweep, "_fetch_board_ids_for_lead_check", _fake_board_ids)
    monkeypatch.setattr(sweep, "_gateway_config_for_board_id", _fake_config)
    monkeypatch.setattr(sweep, "get_tools_effective", _fake_tools_effective)

    with caplog.at_level(logging.WARNING):
        result = await check_lead_message_tools_once()

    assert result == {"checked": 1, "missing": 0}
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_check_skips_unknown_session_and_missing_config(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RPC errors (e.g. archived session) and missing gateway config are
    indeterminate — skipped without warnings, never raised."""
    from app.services.openclaw.gateway_rpc import OpenClawGatewayError

    b1, b2 = uuid4(), uuid4()

    async def _fake_board_ids():
        return [b1, b2]

    async def _fake_config(board_id):
        return object() if board_id == b1 else None

    async def _fake_tools_effective(session_key, *, config):
        raise OpenClawGatewayError('unknown session key "x"')

    monkeypatch.setattr(sweep, "_fetch_board_ids_for_lead_check", _fake_board_ids)
    monkeypatch.setattr(sweep, "_gateway_config_for_board_id", _fake_config)
    monkeypatch.setattr(sweep, "get_tools_effective", _fake_tools_effective)

    with caplog.at_level(logging.WARNING):
        result = await check_lead_message_tools_once()

    assert result == {"checked": 0, "missing": 0}
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
