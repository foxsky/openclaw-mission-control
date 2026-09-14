# ruff: noqa: INP001
"""Mission Control's block inside OpenClaw heartbeat monitor scratch.

OpenClaw 2026.8+ never reads HEARTBEAT.md; ordinary heartbeat polls append the agent's
monitor scratch to the heartbeat prompt. MC owns one marked block at the top and keeps
everything else as agent notes.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from typing import Any

import pytest

import app.services.openclaw.heartbeat_scratch as heartbeat_scratch
from app.services.openclaw.gateway_rpc import OpenClawGatewayError

BEGIN = heartbeat_scratch.MC_BLOCK_BEGIN
END = heartbeat_scratch.MC_BLOCK_END
INSTRUCTIONS = "# Heartbeat checklist\n\n1. Check in."


def _openclaw_effectively_empty(content: str) -> bool:
    """Port of ``isHeartbeatContentEffectivelyEmpty`` (OpenClaw v2026.9.4
    ``src/auto-reply/heartbeat.ts``). Empty scratch makes OpenClaw skip the heartbeat."""
    in_comment = False
    for raw_line in content.split("\n"):
        line = raw_line
        while in_comment or line.lstrip().startswith("<!--"):
            search = line if in_comment else line.lstrip()
            end = search.find("-->")
            if end == -1:
                in_comment = True
                line = ""
                break
            in_comment = False
            if search == line:
                line = line[end + 3 :]
            else:
                lead = len(line) - len(search)
                line = line[:lead] + search[end + 3 :]
        trimmed = line.strip()
        if (
            not trimmed
            or re.match(r"^#+(\s|$)", trimmed)
            or re.match(r"^[-*+]\s*(\[[\sXx]?\]\s*)?$", trimmed)
            or re.match(r"^```[A-Za-z0-9_-]*$", trimmed)
        ):
            continue
        return False
    return True


def _expected(notes: str) -> str:
    notes_section = f"## Agent notes\n{notes}\n" if notes else "## Agent notes\n"
    return f"{BEGIN}\n{INSTRUCTIONS}\n{END}\n\n{notes_section}"


_CASES = [
    (None, ""),
    ("", ""),
    ("  \n", ""),
    ("remember the deploy window", "remember the deploy window"),
    (f"{BEGIN}\nold\n{END}\n\n## Agent notes\nkeep me\n", "keep me"),
    (f"lead text\n{BEGIN}\nold\n{END}\ntail", "lead text\ntail"),
    (f"{BEGIN}\nold\n{END}\n{BEGIN}\ndup\n{END}\n", f"{BEGIN}\ndup\n{END}"),
    (f"{BEGIN}\nno end marker", f"{BEGIN}\nno end marker"),
    (f"{END}\nstray end", f"{END}\nstray end"),
    ("## Agent notes\nalready headed", "already headed"),
    ("## Agent notes", ""),
]
_CASE_IDS = [
    "none",
    "empty",
    "blank",
    "notes-only",
    "block-and-notes",
    "block-after-text",
    "duplicate-block",
    "lone-begin",
    "lone-end",
    "headed-notes",
    "heading-only",
]


@pytest.mark.parametrize(("existing", "notes"), _CASES, ids=_CASE_IDS)
def test_splice_puts_block_first_and_keeps_notes(existing: str | None, notes: str) -> None:
    assert heartbeat_scratch.splice_mc_block(existing, INSTRUCTIONS) == _expected(notes)


@pytest.mark.parametrize(("existing", "notes"), _CASES, ids=_CASE_IDS)
def test_splice_is_idempotent(existing: str | None, notes: str) -> None:
    once = heartbeat_scratch.splice_mc_block(existing, INSTRUCTIONS)
    assert heartbeat_scratch.splice_mc_block(once, INSTRUCTIONS) == once


def test_splice_replaces_old_instructions_and_strips_whitespace() -> None:
    old = heartbeat_scratch.splice_mc_block("my note", "old checklist")
    assert heartbeat_scratch.splice_mc_block(old, f"\n\n{INSTRUCTIONS}\n") == _expected("my note")


@pytest.mark.parametrize(
    "existing",
    [None, "<!-- unclosed agent comment\nnote", "# only a heading"],
    ids=["none", "unclosed-comment-in-notes", "heading-notes"],
)
def test_spliced_scratch_is_never_effectively_empty(existing: str | None) -> None:
    assert not _openclaw_effectively_empty(
        heartbeat_scratch.splice_mc_block(existing, INSTRUCTIONS)
    )


def test_markers_with_empty_notes_alone_would_be_empty() -> None:
    # Guards the port: markers + heading only is empty in OpenClaw, so MC must always
    # ship real instructions inside the block.
    assert _openclaw_effectively_empty(f"{BEGIN}\n{END}\n\n## Agent notes\n")


AGENT = "mc-agent-x"


def _heartbeat_job(job_id: str = "job-1", *, kind: str = "heartbeat") -> dict[str, Any]:
    return {
        "id": job_id,
        "declarationKey": f"heartbeat:{AGENT}",
        "enabled": False,
        "payload": {"kind": kind},
    }


def _other_job(job_id: str = "job-other") -> dict[str, Any]:
    return {"id": job_id, "declarationKey": None, "enabled": True, "payload": {"kind": "agentTurn"}}


def _page(jobs: list[dict[str, Any]], *, next_offset: int | None = None) -> dict[str, Any]:
    return {"jobs": jobs, "hasMore": next_offset is not None, "nextOffset": next_offset}


def _state(content: str | None, revision: int) -> dict[str, Any]:
    scratch = (
        None if content is None else {"content": content, "revision": revision, "updatedAtMs": 1}
    )
    return {"scratch": scratch, "currentRevision": revision, "maxBytes": 262144}


def _set_ok(content: str, revision: int) -> dict[str, Any]:
    return {
        "ok": True,
        "scratch": {"content": content, "revision": revision, "updatedAtMs": 1},
        "currentRevision": revision,
        "maxBytes": 262144,
    }


def _conflict(revision: int) -> dict[str, Any]:
    return {"ok": False, "reason": "revision-conflict", "currentRevision": revision}


class _ScriptedGateway:
    """Returns queued responses per RPC method; raises queued exceptions."""

    def __init__(self, responses: dict[str, list[object]]) -> None:
        self._responses = {method: list(queue) for method, queue in responses.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, method: str, params: dict[str, Any]) -> object:
        self.calls.append((method, params))
        queue = self._responses.get(method)
        if not queue:
            raise AssertionError(f"unexpected call: {method}")
        response = queue.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def params(self, method: str) -> list[dict[str, Any]]:
        return [params for called, params in self.calls if called == method]


def _writer(gateway: _ScriptedGateway) -> heartbeat_scratch.HeartbeatScratchWriter:
    return heartbeat_scratch.HeartbeatScratchWriter(gateway, lookup_delays=(0.0, 0.0))


@pytest.mark.asyncio
async def test_writer_sets_spliced_scratch_with_current_revision_when_scratch_unset() -> None:
    gateway = _ScriptedGateway(
        {
            "cron.list": [_page([_other_job(), _heartbeat_job()])],
            "cron.scratch.get": [_state(None, 3)],
            "cron.scratch.set": [
                _set_ok(heartbeat_scratch.splice_mc_block(None, INSTRUCTIONS), 4),
            ],
        },
    )

    warning = await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS)

    assert warning is None
    assert gateway.params("cron.list") == [
        {
            "agentId": AGENT,
            "includeDisabled": True,
            "includeDeliveryPreviews": False,
            "limit": 200,
            "offset": 0,
        },
    ]
    assert gateway.params("cron.scratch.get") == [{"id": "job-1"}]
    assert gateway.params("cron.scratch.set") == [
        {
            "id": "job-1",
            "content": heartbeat_scratch.splice_mc_block(None, INSTRUCTIONS),
            "expectedRevision": 3,
        },
    ]


@pytest.mark.asyncio
async def test_writer_pages_until_it_finds_the_heartbeat_job() -> None:
    gateway = _ScriptedGateway(
        {
            "cron.list": [_page([_other_job()], next_offset=1), _page([_heartbeat_job()])],
            "cron.scratch.get": [_state(None, 0)],
            "cron.scratch.set": [
                _set_ok(heartbeat_scratch.splice_mc_block(None, INSTRUCTIONS), 1),
            ],
        },
    )

    assert await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS) is None
    assert [params["offset"] for params in gateway.params("cron.list")] == [0, 1]


@pytest.mark.asyncio
async def test_writer_retries_lookup_until_the_monitor_job_appears() -> None:
    gateway = _ScriptedGateway(
        {
            "cron.list": [_page([]), _page([]), _page([_heartbeat_job()])],
            "cron.scratch.get": [_state(None, 0)],
            "cron.scratch.set": [
                _set_ok(heartbeat_scratch.splice_mc_block(None, INSTRUCTIONS), 1),
            ],
        },
    )

    assert await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS) is None
    assert len(gateway.params("cron.list")) == 3


@pytest.mark.asyncio
async def test_writer_reports_missing_job_after_three_lookups() -> None:
    gateway = _ScriptedGateway(
        {"cron.list": [_page([]), _page([_heartbeat_job(kind="agentTurn")]), _page([])]},
    )

    warning = await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS)

    assert warning == heartbeat_scratch.WARNING_JOB_MISSING
    assert [method for method, _ in gateway.calls] == ["cron.list"] * 3


@pytest.mark.asyncio
async def test_writer_skips_set_when_scratch_already_current() -> None:
    current = heartbeat_scratch.splice_mc_block("note", INSTRUCTIONS)
    gateway = _ScriptedGateway(
        {"cron.list": [_page([_heartbeat_job()])], "cron.scratch.get": [_state(current, 7)]},
    )

    assert await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS) is None
    assert gateway.params("cron.scratch.set") == []


@pytest.mark.asyncio
async def test_writer_rereads_after_one_conflict_and_keeps_new_notes() -> None:
    gateway = _ScriptedGateway(
        {
            "cron.list": [_page([_heartbeat_job("job-1")]), _page([_heartbeat_job("job-2")])],
            "cron.scratch.get": [_state(None, 4), _state("note written by the agent", 5)],
            "cron.scratch.set": [
                _conflict(5),
                _set_ok(
                    heartbeat_scratch.splice_mc_block("note written by the agent", INSTRUCTIONS),
                    6,
                ),
            ],
        },
    )

    assert await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS) is None
    assert gateway.params("cron.scratch.get")[1] == {"id": "job-2"}
    second_set = gateway.params("cron.scratch.set")[1]
    assert second_set["id"] == "job-2"
    assert second_set["expectedRevision"] == 5
    assert second_set["content"] == heartbeat_scratch.splice_mc_block(
        "note written by the agent",
        INSTRUCTIONS,
    )


@pytest.mark.asyncio
async def test_writer_gives_up_after_two_conflicts() -> None:
    gateway = _ScriptedGateway(
        {
            "cron.list": [_page([_heartbeat_job()]), _page([_heartbeat_job()])],
            "cron.scratch.get": [_state(None, 1), _state(None, 2)],
            "cron.scratch.set": [_conflict(2), _conflict(3)],
        },
    )

    warning = await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS)

    assert warning == heartbeat_scratch.WARNING_CONFLICT


@pytest.mark.asyncio
async def test_writer_refuses_content_over_the_utf8_byte_limit() -> None:
    # 131,073 "é" fit the RPC schema's 262,144-character limit but are 262,146 bytes.
    gateway = _ScriptedGateway(
        {"cron.list": [_page([_heartbeat_job()])], "cron.scratch.get": [_state(None, 0)]},
    )

    warning = await _writer(gateway).write(agent_id=AGENT, instructions="é" * 131_073)

    assert warning == heartbeat_scratch.WARNING_TOO_LARGE
    assert gateway.params("cron.scratch.set") == []


@pytest.mark.asyncio
async def test_writer_treats_not_found_as_missing_job() -> None:
    gateway = _ScriptedGateway(
        {
            "cron.list": [_page([_heartbeat_job()])],
            "cron.scratch.get": [
                OpenClawGatewayError("Automation not found: job-1. List automations and retry."),
            ],
        },
    )

    warning = await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS)

    assert warning == heartbeat_scratch.WARNING_JOB_MISSING


@pytest.mark.asyncio
async def test_writer_contains_gateway_errors() -> None:
    gateway = _ScriptedGateway({"cron.list": [OpenClawGatewayError("connect call failed")]})

    warning = await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS)

    assert warning == heartbeat_scratch.WARNING_GATEWAY_ERROR


@pytest.mark.asyncio
async def test_writer_bounds_each_rpc_with_a_timeout() -> None:
    async def _hanging_call(method: str, params: dict[str, Any]) -> object:
        await asyncio.sleep(10)
        return None

    writer = heartbeat_scratch.HeartbeatScratchWriter(
        _hanging_call,
        call_timeout_seconds=0.01,
        lookup_delays=(),
    )

    assert await writer.write(agent_id=AGENT, instructions=INSTRUCTIONS) == (
        heartbeat_scratch.WARNING_GATEWAY_ERROR
    )


@pytest.mark.asyncio
async def test_writer_propagates_cancellation() -> None:
    gateway = _ScriptedGateway({"cron.list": [asyncio.CancelledError()]})

    with pytest.raises(asyncio.CancelledError):
        await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS)


@pytest.mark.asyncio
async def test_writer_contains_unexpected_errors() -> None:
    # openclaw_call can raise things besides OpenClawGatewayError/TimeoutError, e.g.
    # ValueError from _build_gateway_url outside its try, or AttributeError on an
    # unexpected frame. Those must still come back as a warning code, never raise.
    gateway = _ScriptedGateway({"cron.list": [ValueError("bad url")]})

    warning = await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS)

    assert warning == heartbeat_scratch.WARNING_GATEWAY_ERROR


@pytest.mark.asyncio
async def test_writer_redacts_secrets_from_warning_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_url = "wss://gateway.example/ws?token=synthetic-secret-123"
    gateway = _ScriptedGateway(
        {"cron.list": [OpenClawGatewayError(f"connect call failed: {secret_url}")]},
    )

    caplog.set_level(logging.WARNING, logger="app.services.openclaw.heartbeat_scratch")
    warning = await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS)

    assert warning == heartbeat_scratch.WARNING_GATEWAY_ERROR
    assert "synthetic-secret-123" not in caplog.text


@pytest.mark.asyncio
async def test_writer_bounds_total_work_with_a_deadline() -> None:
    async def _slow_call(method: str, params: dict[str, Any]) -> object:
        await asyncio.sleep(0.2)
        return None

    writer = heartbeat_scratch.HeartbeatScratchWriter(
        _slow_call,
        call_timeout_seconds=1.0,
        total_timeout_seconds=0.05,
        lookup_delays=(),
    )

    assert await writer.write(agent_id=AGENT, instructions=INSTRUCTIONS) == (
        heartbeat_scratch.WARNING_GATEWAY_ERROR
    )


@pytest.mark.asyncio
async def test_writer_stops_paging_when_next_offset_does_not_advance() -> None:
    gateway = _ScriptedGateway({"cron.list": [_page([], next_offset=0)]})

    warning = await heartbeat_scratch.HeartbeatScratchWriter(
        gateway,
        lookup_delays=(),
    ).write(agent_id=AGENT, instructions=INSTRUCTIONS)

    assert warning == heartbeat_scratch.WARNING_JOB_MISSING
    assert len(gateway.params("cron.list")) == 1


def test_writer_default_budget_stays_well_under_the_lifecycle_deadline() -> None:
    """heartbeat_sweep/lifecycle_reconcile enforce a 60 s lifecycle deadline around the whole
    agent lifecycle (session reset, credential verification, wake) and scratch writes run
    before those steps, so the writer's own default budget must leave them plenty of room."""
    params = inspect.signature(heartbeat_scratch.HeartbeatScratchWriter).parameters

    assert params["call_timeout_seconds"].default == 5.0
    assert params["total_timeout_seconds"].default == 15.0
    assert params["total_timeout_seconds"].default <= 20.0  # well under the 60 s lifecycle deadline
