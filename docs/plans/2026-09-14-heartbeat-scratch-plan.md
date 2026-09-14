# Heartbeat Instructions via Monitor Scratch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On keyed-layout OpenClaw gateways, Mission Control writes each agent's rendered heartbeat checklist into the agent's heartbeat monitor scratch (instead of the rejected `HEARTBEAT.md`), removes its legacy "Read HEARTBEAT.md" prompts, and makes its templates stop pointing at `HEARTBEAT.md`.

**Architecture:** A new `heartbeat_scratch` module holds a pure splice function and a CAS writer over `cron.list` / `cron.scratch.get` / `cron.scratch.set`. The lifecycle control plane remembers the config layout it read in `patch_agent_heartbeats`; `_set_agent_files` routes `HEARTBEAT.md` to the writer on keyed layouts after all physical files are written. Writer failures become warning codes that flow through `LifecycleResult` to the template-sync result, never exceptions.

**Tech Stack:** Python 3.12, FastAPI/SQLModel backend, pytest + pytest-asyncio, Jinja2 templates, uv; Black/isort/flake8/mypy (strict).

**Spec:** `docs/plans/2026-09-14-heartbeat-scratch-design.md`

## Global Constraints

- Work in `/root/.openclaw/workspace/openclaw-mission-control/.worktrees/heartbeat-scratch` on branch `design/heartbeat-scratch`. Run backend commands from `backend/`.
- Test command: `uv run pytest <path> -q`. Full gate before PR: `uv run pytest -q`, `uv run black --check .`, `uv run isort --check-only .`, `uv run flake8 --config .flake8`, `uv run mypy` (same as `make backend-lint backend-test`).
- Tests must not use `from app.services.openclaw import ...` (import-boundary test); import modules as `import app.services.openclaw.<module> as <alias>` or `from app.services.openclaw.<module> import X`.
- Keyed layout only. Legacy-layout gateways (`agents.list`) keep writing `HEARTBEAT.md`, keep stored prompts, and render today's template wording.
- Marker strings (exact):
  - `<!-- mission-control:heartbeat:begin (managed by Mission Control; edits here are overwritten) -->`
  - `<!-- mission-control:heartbeat:end -->`
  - notes heading `## Agent notes`
- Scratch limit: 262,144 **UTF-8 bytes** (use the gateway's `maxBytes`).
- Always send the top-level `currentRevision` as `expectedRevision`.
- Warning codes (exact): `heartbeat_scratch.job_missing`, `heartbeat_scratch.conflict`, `heartbeat_scratch.too_large`, `heartbeat_scratch.gateway_error`.
- Writer bounds: 10 s per RPC (`asyncio.wait_for`), lookup delays `(0.5, 1.0)` → 3 lookups, at most 2 CAS rounds. `asyncio.CancelledError` always propagates.
- A prompt is "legacy" iff it is a string containing `HEARTBEAT.md`. On keyed layouts legacy prompts are removed (`"prompt": null` in the merge patch); other prompts are untouched.
- Warnings never count as sync errors, never change the CLI exit code, never set `last_provision_error`.
- Commits: `git -c user.name=foxsky -c user.email=miguel@viapersonal.com.br commit`, message ending with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0153jb2YEniuYF1icYb5wPXK
  ```
- No push, PR, merge, deploy, gateway write, or remote write without explicit operator approval.

---

### Task 1: Splice MC's block into scratch

**Files:**
- Create: `backend/app/services/openclaw/heartbeat_scratch.py`
- Test: `backend/tests/test_heartbeat_scratch.py`

**Interfaces:**
- Produces: `MC_BLOCK_BEGIN: str`, `MC_BLOCK_END: str`, `AGENT_NOTES_HEADING: str`, `splice_mc_block(existing: str | None, instructions: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_heartbeat_scratch.py`:

```python
# ruff: noqa: INP001
"""Mission Control's block inside OpenClaw heartbeat monitor scratch.

OpenClaw 2026.8+ never reads HEARTBEAT.md; ordinary heartbeat polls append the agent's
monitor scratch to the heartbeat prompt. MC owns one marked block at the top and keeps
everything else as agent notes.
"""

from __future__ import annotations

import re

import pytest

import app.services.openclaw.heartbeat_scratch as heartbeat_scratch

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
    assert not _openclaw_effectively_empty(heartbeat_scratch.splice_mc_block(existing, INSTRUCTIONS))


def test_markers_with_empty_notes_alone_would_be_empty() -> None:
    # Guards the port: markers + heading only is empty in OpenClaw, so MC must always
    # ship real instructions inside the block.
    assert _openclaw_effectively_empty(f"{BEGIN}\n{END}\n\n## Agent notes\n")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_heartbeat_scratch.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'app.services.openclaw.heartbeat_scratch'`.

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/openclaw/heartbeat_scratch.py`:

```python
"""Mission Control heartbeat instructions in OpenClaw heartbeat monitor scratch.

OpenClaw 2026.8+ (keyed ``agents.entries`` layout) never reads ``HEARTBEAT.md``. Ordinary
heartbeat polls append the agent's monitor-job scratch to the heartbeat prompt instead.
Mission Control owns one marked block at the top of that scratch; every other line is kept
as agent notes.
"""

from __future__ import annotations

MC_BLOCK_BEGIN = (
    "<!-- mission-control:heartbeat:begin "
    "(managed by Mission Control; edits here are overwritten) -->"
)
MC_BLOCK_END = "<!-- mission-control:heartbeat:end -->"
AGENT_NOTES_HEADING = "## Agent notes"


def splice_mc_block(existing: str | None, instructions: str) -> str:
    """Return scratch with MC's block first and all other existing text kept as notes.

    Only the first BEGIN line with a later END line is MC's block. Keeping the block first
    also means no unclosed HTML comment in agent notes can hide MC's instructions from
    OpenClaw's effectively-empty check (which skips the heartbeat).
    """
    lines = (existing or "").split("\n")
    begin = next((i for i, line in enumerate(lines) if line.strip() == MC_BLOCK_BEGIN), None)
    if begin is not None:
        end = next(
            (i for i in range(begin + 1, len(lines)) if lines[i].strip() == MC_BLOCK_END),
            None,
        )
        if end is not None:
            lines = lines[:begin] + lines[end + 1 :]
    notes = "\n".join(lines).strip()
    first_line, _, rest = notes.partition("\n")
    if first_line.strip() == AGENT_NOTES_HEADING:
        notes = rest.strip()
    notes_section = f"{AGENT_NOTES_HEADING}\n{notes}\n" if notes else f"{AGENT_NOTES_HEADING}\n"
    return f"{MC_BLOCK_BEGIN}\n{instructions.strip()}\n{MC_BLOCK_END}\n\n{notes_section}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_heartbeat_scratch.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/openclaw/heartbeat_scratch.py backend/tests/test_heartbeat_scratch.py
git -c user.name=foxsky -c user.email=miguel@viapersonal.com.br commit -m "feat(openclaw): splice Mission Control block into heartbeat scratch

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0153jb2YEniuYF1icYb5wPXK"
```

---

### Task 2: Compare-and-set scratch writer

**Files:**
- Modify: `backend/app/services/openclaw/heartbeat_scratch.py`
- Test: `backend/tests/test_heartbeat_scratch.py` (append)

**Interfaces:**
- Consumes: `splice_mc_block` (Task 1).
- Produces:
  - `GatewayCall = Callable[[str, dict[str, Any]], Awaitable[object]]`
  - `class HeartbeatScratchWriter(call: GatewayCall, *, call_timeout_seconds: float = 10.0, lookup_delays: tuple[float, ...] = (0.5, 1.0))` with `async def write(self, *, agent_id: str, instructions: str) -> str | None`
  - constants `WARNING_JOB_MISSING`, `WARNING_CONFLICT`, `WARNING_TOO_LARGE`, `WARNING_GATEWAY_ERROR`, `DEFAULT_SCRATCH_MAX_BYTES`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_heartbeat_scratch.py` (add `import asyncio` and `from typing import Any` to the imports at the top, plus `from app.services.openclaw.gateway_rpc import OpenClawGatewayError`):

```python
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
    scratch = None if content is None else {"content": content, "revision": revision, "updatedAtMs": 1}
    return {"scratch": scratch, "currentRevision": revision, "maxBytes": 262144}


_SET_OK = {"ok": True, "scratch": None, "currentRevision": 1, "maxBytes": 262144}


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
            "cron.scratch.set": [_SET_OK],
        },
    )

    warning = await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS)

    assert warning is None
    assert gateway.params("cron.list") == [
        {"agentId": AGENT, "includeDisabled": True, "limit": 200, "offset": 0},
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
            "cron.list": [_page([_other_job()], next_offset=200), _page([_heartbeat_job()])],
            "cron.scratch.get": [_state(None, 0)],
            "cron.scratch.set": [_SET_OK],
        },
    )

    assert await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS) is None
    assert [params["offset"] for params in gateway.params("cron.list")] == [0, 200]


@pytest.mark.asyncio
async def test_writer_retries_lookup_until_the_monitor_job_appears() -> None:
    gateway = _ScriptedGateway(
        {
            "cron.list": [_page([]), _page([]), _page([_heartbeat_job()])],
            "cron.scratch.get": [_state(None, 0)],
            "cron.scratch.set": [_SET_OK],
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
            "cron.list": [_page([_heartbeat_job()]), _page([_heartbeat_job()])],
            "cron.scratch.get": [_state(None, 4), _state("note written by the agent", 5)],
            "cron.scratch.set": [_conflict(5), _SET_OK],
        },
    )

    assert await _writer(gateway).write(agent_id=AGENT, instructions=INSTRUCTIONS) is None
    second_set = gateway.params("cron.scratch.set")[1]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_heartbeat_scratch.py -q`
Expected: new tests fail with `AttributeError: module 'app.services.openclaw.heartbeat_scratch' has no attribute 'HeartbeatScratchWriter'`; Task 1 tests still pass.

- [ ] **Step 3: Write the implementation**

In `backend/app/services/openclaw/heartbeat_scratch.py`, replace the import block `from __future__ import annotations` with:

```python
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.services.openclaw.gateway_rpc import OpenClawGatewayError

logger = get_logger(__name__)
```

After the `AGENT_NOTES_HEADING` constant add:

```python
# OpenClaw's CRON_JOB_SCRATCH_MAX_BYTES (UTF-8 bytes); used only if a gateway omits maxBytes.
DEFAULT_SCRATCH_MAX_BYTES = 262_144

WARNING_JOB_MISSING = "heartbeat_scratch.job_missing"
WARNING_CONFLICT = "heartbeat_scratch.conflict"
WARNING_TOO_LARGE = "heartbeat_scratch.too_large"
WARNING_GATEWAY_ERROR = "heartbeat_scratch.gateway_error"

GatewayCall = Callable[[str, dict[str, Any]], Awaitable[object]]
```

Append at the end of the file:

```python
class HeartbeatScratchWriter:
    """Writes MC's block into one agent's heartbeat monitor scratch with compare-and-set.

    Failures come back as warning codes so heartbeat instructions never block credentials,
    workspace files, or the wake; the next lifecycle run retries.
    """

    def __init__(
        self,
        call: GatewayCall,
        *,
        call_timeout_seconds: float = 10.0,
        lookup_delays: tuple[float, ...] = (0.5, 1.0),
    ) -> None:
        self._call = call
        self._call_timeout_seconds = call_timeout_seconds
        # A new agent's monitor job appears after gateway reconciliation; OpenClaw retries a
        # failed reconcile after 30 s, so this short wait is best effort only.
        self._lookup_delays = lookup_delays

    async def write(self, *, agent_id: str, instructions: str) -> str | None:
        """Return a warning code, or None when scratch holds the current block."""
        detail = ""
        try:
            warning = await self._write(agent_id, instructions)
        except OpenClawGatewayError as exc:
            detail = str(exc)
            # The job can disappear between lookup and read/write (agent re-enrolled).
            missing = "not found" in detail.lower()
            warning = WARNING_JOB_MISSING if missing else WARNING_GATEWAY_ERROR
        except TimeoutError:
            detail = f"no response within {self._call_timeout_seconds}s"
            warning = WARNING_GATEWAY_ERROR
        if warning is not None:
            logger.warning("gateway.%s agent_id=%s detail=%s", warning, agent_id, detail)
        return warning

    async def _write(self, agent_id: str, instructions: str) -> str | None:
        for _attempt in range(2):
            job_id = await self._resolve_job_id(agent_id)
            if job_id is None:
                return WARNING_JOB_MISSING
            content, revision, max_bytes = _scratch_state(
                await self._rpc("cron.scratch.get", {"id": job_id}),
            )
            new_content = splice_mc_block(content, instructions)
            if new_content == content:
                # OpenClaw bumps the revision even for identical writes.
                return None
            if len(new_content.encode("utf-8")) > max_bytes:
                return WARNING_TOO_LARGE
            result = await self._rpc(
                "cron.scratch.set",
                {"id": job_id, "content": new_content, "expectedRevision": revision},
            )
            if not isinstance(result, dict):
                msg = "cron.scratch.set returned invalid payload"
                raise OpenClawGatewayError(msg)
            if result.get("ok") is True:
                return None
            if result.get("reason") != "revision-conflict":
                msg = "cron.scratch.set returned invalid payload"
                raise OpenClawGatewayError(msg)
        return WARNING_CONFLICT

    async def _resolve_job_id(self, agent_id: str) -> str | None:
        for delay in self._lookup_delays:
            job_id = await self._find_job_id(agent_id)
            if job_id is not None:
                return job_id
            await asyncio.sleep(delay)
        return await self._find_job_id(agent_id)

    async def _find_job_id(self, agent_id: str) -> str | None:
        declaration_key = f"heartbeat:{agent_id}"
        offset = 0
        while True:
            page = await self._rpc(
                "cron.list",
                {"agentId": agent_id, "includeDisabled": True, "limit": 200, "offset": offset},
            )
            if not isinstance(page, dict):
                msg = "cron.list returned invalid payload"
                raise OpenClawGatewayError(msg)
            for job in page.get("jobs") or []:
                if _is_heartbeat_job(job, declaration_key):
                    return str(job["id"])
            next_offset = page.get("nextOffset")
            if page.get("hasMore") is not True or not isinstance(next_offset, int):
                return None
            offset = next_offset

    async def _rpc(self, method: str, params: dict[str, Any]) -> object:
        return await asyncio.wait_for(
            self._call(method, params),
            timeout=self._call_timeout_seconds,
        )


def _is_heartbeat_job(job: object, declaration_key: str) -> bool:
    if not isinstance(job, dict) or job.get("declarationKey") != declaration_key:
        return False
    payload = job.get("payload")
    kind = payload.get("kind") if isinstance(payload, dict) else None
    return kind == "heartbeat" and isinstance(job.get("id"), str)


def _scratch_state(payload: object) -> tuple[str | None, int, int]:
    """Return (content, currentRevision, maxBytes) from ``cron.scratch.get``.

    ``currentRevision`` stays positive after an unset (tombstone), so it is the only valid
    ``expectedRevision``, even when ``scratch`` is null.
    """
    if not isinstance(payload, dict):
        msg = "cron.scratch.get returned invalid payload"
        raise OpenClawGatewayError(msg)
    revision = payload.get("currentRevision")
    if not isinstance(revision, int):
        msg = "cron.scratch.get returned invalid payload"
        raise OpenClawGatewayError(msg)
    scratch = payload.get("scratch")
    content = scratch.get("content") if isinstance(scratch, dict) else None
    max_bytes = payload.get("maxBytes")
    return (
        content if isinstance(content, str) else None,
        revision,
        max_bytes if isinstance(max_bytes, int) else DEFAULT_SCRATCH_MAX_BYTES,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_heartbeat_scratch.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/openclaw/heartbeat_scratch.py backend/tests/test_heartbeat_scratch.py
git -c user.name=foxsky -c user.email=miguel@viapersonal.com.br commit -m "feat(openclaw): write heartbeat scratch with compare-and-set

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0153jb2YEniuYF1icYb5wPXK"
```

---

### Task 3: Control plane exposes layout and scratch writes

**Files:**
- Modify: `backend/app/services/openclaw/provisioning.py` (imports; `GatewayControlPlane` ~738-786; `OpenClawGatewayControlPlane.__init__` ~792; `patch_agent_heartbeats` ~926)
- Test: `backend/tests/test_gateway_config_layout.py` (append)

**Interfaces:**
- Consumes: `HeartbeatScratchWriter` (Task 2).
- Produces on `GatewayControlPlane` (abstract) and `OpenClawGatewayControlPlane`:
  - `async def uses_keyed_agent_entries(self) -> bool`
  - `async def write_heartbeat_scratch(self, *, agent_id: str, instructions: str) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_gateway_config_layout.py`:

```python
@pytest.mark.asyncio
async def test_control_plane_remembers_layout_read_by_patch_agent_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config({"mc-agent-x": {"workspace": "/w/x", "heartbeat": {"every": "10m"}}}),
    )

    await control_plane.patch_agent_heartbeats([("mc-agent-x", "/w/x", {"every": "10m"})])

    assert await control_plane.uses_keyed_agent_entries() is True
    assert [method for method, _ in calls] == ["config.get"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"hash": "h", "config": {"agents": {"entries": {}}}}, True),
        ({"hash": "h", "config": {"agents": {"list": []}}}, False),
    ],
    ids=["keyed", "legacy"],
)
@pytest.mark.asyncio
async def test_control_plane_reads_layout_once_when_nothing_recorded(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    expected: bool,
) -> None:
    control_plane, calls = _control_plane_with(monkeypatch, payload)

    assert await control_plane.uses_keyed_agent_entries() is expected
    assert await control_plane.uses_keyed_agent_entries() is expected
    assert [method for method, _ in calls] == ["config.get"]


@pytest.mark.asyncio
async def test_control_plane_writes_heartbeat_scratch_through_gateway_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []
    job = {"id": "job-1", "declarationKey": "heartbeat:mc-agent-x", "payload": {"kind": "heartbeat"}}

    async def _fake_openclaw_call(
        method: str,
        params: dict[str, Any] | None = None,
        config: object = None,
    ) -> object:
        _ = config
        calls.append((method, params))
        responses: dict[str, object] = {
            "cron.list": {"jobs": [job], "hasMore": False, "nextOffset": None},
            "cron.scratch.get": {"scratch": None, "currentRevision": 0, "maxBytes": 262144},
            "cron.scratch.set": {"ok": True, "scratch": None, "currentRevision": 1, "maxBytes": 1},
        }
        return responses[method]

    monkeypatch.setattr(agent_provisioning, "openclaw_call", _fake_openclaw_call)
    control_plane = agent_provisioning.OpenClawGatewayControlPlane(
        agent_provisioning.GatewayClientConfig(url="ws://gateway.example/ws", token=None),
    )

    warning = await control_plane.write_heartbeat_scratch(
        agent_id="mc-agent-x",
        instructions="1. Check in.",
    )

    assert warning is None
    assert [method for method, _ in calls] == ["cron.list", "cron.scratch.get", "cron.scratch.set"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gateway_config_layout.py -q`
Expected: 4 new tests fail with `AttributeError: 'OpenClawGatewayControlPlane' object has no attribute 'uses_keyed_agent_entries'` / `'write_heartbeat_scratch'`.

- [ ] **Step 3: Write the implementation**

In `provisioning.py` imports, after the `gateway_dispatch` import add:

```python
from app.services.openclaw.heartbeat_scratch import HeartbeatScratchWriter
```

In `class GatewayControlPlane(ABC)`, after `patch_agent_heartbeats` add:

```python
    @abstractmethod
    async def uses_keyed_agent_entries(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def write_heartbeat_scratch(self, *, agent_id: str, instructions: str) -> str | None:
        raise NotImplementedError
```

Replace `OpenClawGatewayControlPlane.__init__` with:

```python
    def __init__(self, config: GatewayClientConfig) -> None:
        self._config = config
        # Layout from this control plane's last config read. A lifecycle builds its own control
        # plane and reads config in patch_agent_heartbeats before any file is rendered.
        self._keyed_agent_entries: bool | None = None
```

In `OpenClawGatewayControlPlane.patch_agent_heartbeats`, directly after
`base_hash, config_data, keyed_entries = await _gateway_config_snapshot(self._config)` add:

```python
        # Recorded before the no-change return below; file routing relies on it.
        self._keyed_agent_entries = keyed_entries
```

After `patch_agent_heartbeats` in `OpenClawGatewayControlPlane` add:

```python
    async def uses_keyed_agent_entries(self) -> bool:
        if self._keyed_agent_entries is None:
            _, _, keyed_entries = await _gateway_config_snapshot(self._config)
            self._keyed_agent_entries = keyed_entries
            return keyed_entries
        return self._keyed_agent_entries

    async def write_heartbeat_scratch(self, *, agent_id: str, instructions: str) -> str | None:
        async def _call(method: str, params: dict[str, Any]) -> object:
            return await openclaw_call(method, params, config=self._config)

        writer = HeartbeatScratchWriter(_call)
        return await writer.write(agent_id=agent_id, instructions=instructions)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gateway_config_layout.py tests/test_heartbeat_scratch.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/openclaw/provisioning.py backend/tests/test_gateway_config_layout.py
git -c user.name=foxsky -c user.email=miguel@viapersonal.com.br commit -m "feat(openclaw): expose config layout and scratch writes on the control plane

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0153jb2YEniuYF1icYb5wPXK"
```

---

### Task 4: Remove legacy HEARTBEAT.md prompts on keyed layouts

**Files:**
- Modify: `backend/app/services/openclaw/provisioning.py` (`patch_agent_heartbeats` keyed branch ~942-947; `_without_retired_heartbeat_keys` ~1061; `_merged_agent_entry` ~1115; `_updated_agent_entries` ~1165)
- Test: `backend/tests/test_gateway_config_layout.py` (append)

**Interfaces:**
- Produces: `_references_heartbeat_md(heartbeat: object) -> bool`, `_keyed_heartbeat(heartbeat: dict[str, Any]) -> dict[str, Any]`; `_merged_agent_entry(raw_entry, workspace_path, heartbeat, *, drop_heartbeat_md_prompt: bool = False)`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_gateway_config_layout.py`:

```python
# Every MC agent on the 2026.9.4 gateway carried this prompt; 2026.8+ never reads HEARTBEAT.md,
# and OpenClaw's default prompt already follows heartbeat monitor scratch.
_LEGACY_PROMPT = (
    "Read HEARTBEAT.md and follow it strictly. Do not infer or repeat old tasks from prior "
    "chats. If nothing needs attention, reply HEARTBEAT_OK."
)


def _apply_merge_patch(target: object, patch: object) -> object:
    """RFC 7396 merge patch, as OpenClaw's config.patch applies it (null deletes)."""
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _apply_merge_patch(result.get(key), value)
    return result


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_deletes_heartbeat_md_prompt_on_keyed_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config(
            {"mc-agent-x": {"workspace": "/w/x", "heartbeat": {"every": "10m", "prompt": _LEGACY_PROMPT}}},
        ),
    )

    await control_plane.patch_agent_heartbeats([("mc-agent-x", "/w/x", {"every": "10m"})])

    patch = json.loads(calls[1][1]["raw"])
    assert patch["agents"]["entries"]["mc-agent-x"]["heartbeat"] == {"every": "10m", "prompt": None}


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_deletes_heartbeat_md_prompt_for_disabled_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config(
            {"mc-agent-x": {"workspace": "/w/x", "heartbeat": {"every": "0m", "prompt": _LEGACY_PROMPT}}},
        ),
    )

    await control_plane.patch_agent_heartbeats([("mc-agent-x", "/w/x", {"every": "0m"})])

    patch = json.loads(calls[1][1]["raw"])
    assert patch["agents"]["entries"]["mc-agent-x"]["heartbeat"] == {"every": "0m", "prompt": None}


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_ignores_stored_heartbeat_md_prompt_on_keyed_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config({"mc-agent-x": {"workspace": "/w/x", "heartbeat": {"every": "10m"}}}),
    )

    await control_plane.patch_agent_heartbeats(
        [("mc-agent-x", "/w/x", {"every": "10m", "prompt": _LEGACY_PROMPT})],
    )

    assert [method for method, _ in calls] == ["config.get"]


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_keeps_custom_prompt_on_keyed_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config({"mc-agent-x": {"workspace": "/w/x", "heartbeat": {"every": "10m"}}}),
    )

    await control_plane.patch_agent_heartbeats(
        [("mc-agent-x", "/w/x", {"every": "20m", "prompt": "Check the deploy queue."})],
    )

    patch = json.loads(calls[1][1]["raw"])
    assert patch["agents"]["entries"]["mc-agent-x"]["heartbeat"] == {
        "every": "20m",
        "prompt": "Check the deploy queue.",
    }


@pytest.mark.asyncio
async def test_prompt_removal_converges_after_gateway_applies_the_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _canonical_config(
        {
            "mc-enabled": {
                "workspace": "/w/e",
                "heartbeat": {"every": "10m", "target": "last", "prompt": _LEGACY_PROMPT},
            },
            "mc-disabled": {"workspace": "/w/d", "heartbeat": {"every": "0m", "prompt": _LEGACY_PROMPT}},
        },
    )
    control_plane, calls = _control_plane_with(monkeypatch, payload)
    desired = [
        ("mc-enabled", "/w/e", {"every": "10m", "target": "last", "prompt": _LEGACY_PROMPT}),
        ("mc-disabled", "/w/d", {"every": "0m", "target": "last", "prompt": _LEGACY_PROMPT}),
    ]

    await control_plane.patch_agent_heartbeats(desired)
    payload["config"] = _apply_merge_patch(payload["config"], json.loads(calls[1][1]["raw"]))
    await control_plane.patch_agent_heartbeats(desired)

    assert [method for method, _ in calls] == ["config.get", "config.patch", "config.get"]
    entries = payload["config"]["agents"]["entries"]
    assert "prompt" not in entries["mc-enabled"]["heartbeat"]
    assert "prompt" not in entries["mc-disabled"]["heartbeat"]


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_keeps_heartbeat_md_prompt_on_legacy_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "hash": "h-legacy",
        "config": {
            "agents": {"list": [{"id": "mc-agent-x", "workspace": "/w/x", "heartbeat": {}}]},
            "channels": {"defaults": {"heartbeat": dict(_VISIBILITY)}},
        },
    }
    control_plane, calls = _control_plane_with(monkeypatch, payload)

    await control_plane.patch_agent_heartbeats(
        [("mc-agent-x", "/w/x", {"every": "10m", "prompt": _LEGACY_PROMPT})],
    )

    (entry,) = json.loads(calls[1][1]["raw"])["agents"]["list"]
    assert entry["heartbeat"] == {"every": "10m", "prompt": _LEGACY_PROMPT}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gateway_config_layout.py -q`
Expected: `deletes_heartbeat_md_prompt_on_keyed_layout`, `..._for_disabled_heartbeat`, `ignores_stored_heartbeat_md_prompt`, and `prompt_removal_converges` fail (prompt still present / extra patch); the custom-prompt and legacy tests pass already.

- [ ] **Step 3: Write the implementation**

In `patch_agent_heartbeats`, replace the keyed block

```python
            # Stripped before comparing too, so a retired key alone never forces a patch.
            keyed_heartbeats = {
                agent_id: (workspace_path, _without_retired_heartbeat_keys(heartbeat))
                for agent_id, (workspace_path, heartbeat) in entry_by_id.items()
            }
```

with

```python
            # Normalized before comparing too, so a retired key or MC's legacy prompt alone
            # never forces a patch.
            keyed_heartbeats = {
                agent_id: (workspace_path, _keyed_heartbeat(heartbeat))
                for agent_id, (workspace_path, heartbeat) in entry_by_id.items()
            }
```

After `_without_retired_heartbeat_keys` add:

```python
def _references_heartbeat_md(heartbeat: object) -> bool:
    """Whether a heartbeat prompt points at HEARTBEAT.md, which 2026.8+ gateways never read."""
    if not isinstance(heartbeat, dict):
        return False
    prompt = heartbeat.get("prompt")
    return isinstance(prompt, str) and "HEARTBEAT.md" in prompt


def _keyed_heartbeat(heartbeat: dict[str, Any]) -> dict[str, Any]:
    """MC's desired heartbeat for keyed-layout (2026.8+) gateways.

    Prompts naming HEARTBEAT.md are MC's legacy prompts. Leaving ``prompt`` unset lets
    OpenClaw's default prompt apply, which follows the heartbeat monitor scratch MC writes.
    """
    keyed = _without_retired_heartbeat_keys(heartbeat)
    if _references_heartbeat_md(keyed):
        del keyed["prompt"]
    return keyed
```

Replace `_merged_agent_entry` with:

```python
def _merged_agent_entry(
    raw_entry: dict[str, Any],
    workspace_path: str,
    heartbeat: dict[str, Any],
    *,
    drop_heartbeat_md_prompt: bool = False,
) -> dict[str, Any] | None:
    """Return the entry with MC's workspace/heartbeat applied, or None when unchanged."""
    current_heartbeat = raw_entry.get("heartbeat")
    # Checked apart from the heartbeat comparison, which ignores every field of a disabled
    # heartbeat; otherwise disabled agents would keep the dead prompt.
    stale_prompt = drop_heartbeat_md_prompt and _references_heartbeat_md(current_heartbeat)
    workspace_changed = raw_entry.get("workspace") != workspace_path
    heartbeat_changed = stale_prompt or not _heartbeat_configs_equal(current_heartbeat, heartbeat)
    if not workspace_changed and not heartbeat_changed:
        return None
    new_entry = dict(raw_entry)
    new_entry["workspace"] = workspace_path
    if heartbeat_changed:
        # Merge: start from existing gateway config, then overlay MC values.
        # Gateway-only fields (model, ackMaxChars, prompt) survive because
        # the merge starts from dict(existing) and MC's heartbeat dict
        # typically doesn't contain them (unless explicitly set in DB).
        existing_hb = current_heartbeat or {}
        merged_hb = dict(existing_hb)
        merged_hb.update(heartbeat)
        if stale_prompt:
            # JSON null deletes the key under config.patch merge-patch semantics.
            merged_hb["prompt"] = None
        new_entry["heartbeat"] = merged_hb
    else:
        new_entry["heartbeat"] = current_heartbeat
    return new_entry
```

In `_updated_agent_entries`, replace

```python
            merged = _merged_agent_entry(raw_entry, workspace_path, heartbeat)
```

with

```python
            merged = _merged_agent_entry(
                raw_entry,
                workspace_path,
                heartbeat,
                drop_heartbeat_md_prompt=True,
            )
            if merged is not None and _references_heartbeat_md(raw_entry.get("heartbeat")):
                logger.info("gateway.heartbeat_prompt.heartbeat_md_removed agent_id=%s", agent_id)
```

(`_updated_agent_list`, the legacy path, keeps calling `_merged_agent_entry` without the flag.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gateway_config_layout.py tests/test_agent_provisioning_utils.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/openclaw/provisioning.py backend/tests/test_gateway_config_layout.py
git -c user.name=foxsky -c user.email=miguel@viapersonal.com.br commit -m "fix(openclaw): drop legacy HEARTBEAT.md heartbeat prompts on 2026.8+ gateways

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0153jb2YEniuYF1icYb5wPXK"
```

---

### Task 5: Route HEARTBEAT.md to scratch and carry warnings out of the lifecycle

**Files:**
- Modify: `backend/app/services/openclaw/provisioning.py` (`LifecycleResult` ~89; `_set_agent_files` ~1262; `provision` ~1389; `apply_agent_lifecycle` ~1807-1869)
- Modify: `backend/tests/test_agent_provisioning_utils.py` (5 `_fake_set_agent_files` fakes), `backend/tests/test_global_heartbeats_reconciliation.py` (2 `_fake_provision` fakes)
- Test: `backend/tests/test_heartbeat_scratch_routing.py` (create)

**Interfaces:**
- Consumes: `GatewayControlPlane.uses_keyed_agent_entries`, `write_heartbeat_scratch` (Task 3).
- Produces:
  - `LifecycleResult.warnings: tuple[str, ...] = ()`
  - `_set_agent_files(..., heartbeat_in_scratch: bool = False) -> list[str]`
  - `provision(...) -> list[str]`
  - render context key `heartbeat_in_scratch` with value `"true"`/`"false"` (used by Task 7 templates).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_heartbeat_scratch_routing.py`:

```python
# ruff: noqa: INP001
"""HEARTBEAT.md goes to heartbeat monitor scratch on keyed-layout (2026.8+) gateways."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

import app.services.openclaw.provisioning as agent_provisioning
from app.services.openclaw.gateway_rpc import OpenClawGatewayError


class _ControlPlaneStub:
    def __init__(
        self,
        *,
        keyed: bool = True,
        scratch_warning: str | None = None,
        unsupported: frozenset[str] = frozenset(),
    ) -> None:
        self.keyed = keyed
        self.scratch_warning = scratch_warning
        self.unsupported = unsupported
        self.events: list[tuple[str, str]] = []
        self.scratch_writes: list[tuple[str, str]] = []

    async def uses_keyed_agent_entries(self) -> bool:
        return self.keyed

    async def upsert_agent(self, registration: object) -> None:
        self.events.append(("upsert", "agent"))

    async def list_agent_files(self, agent_id: str) -> dict[str, dict[str, Any]]:
        return {}

    async def set_agent_file(self, *, agent_id: str, name: str, content: str) -> None:
        self.events.append(("set", name))
        if name in self.unsupported:
            raise OpenClawGatewayError(f'INVALID_REQUEST unsupported file "{name}"')

    async def delete_agent_file(self, *, agent_id: str, name: str) -> None:
        self.events.append(("delete", name))

    async def write_heartbeat_scratch(self, *, agent_id: str, instructions: str) -> str | None:
        self.events.append(("scratch", agent_id))
        self.scratch_writes.append((agent_id, instructions))
        return self.scratch_warning


def _lead_manager(control_plane: _ControlPlaneStub) -> agent_provisioning.BoardAgentLifecycleManager:
    return agent_provisioning.BoardAgentLifecycleManager(
        SimpleNamespace(workspace_root="/w"),  # type: ignore[arg-type]
        control_plane,  # type: ignore[arg-type]
    )


_LEAD = SimpleNamespace(is_board_lead=True)
_RENDERED = {"AGENTS.md": "agents", "HEARTBEAT.md": "1. Check in."}


@pytest.mark.asyncio
async def test_keyed_layout_writes_heartbeat_to_scratch_after_physical_files() -> None:
    control_plane = _ControlPlaneStub(unsupported=frozenset({"HEARTBEAT.md"}))

    warnings = await _lead_manager(control_plane)._set_agent_files(
        agent=_LEAD,  # type: ignore[arg-type]
        agent_id="lead-x",
        rendered=dict(_RENDERED),
        desired_file_names=set(_RENDERED),
        existing_files={"HEARTBEAT.md": {"name": "HEARTBEAT.md"}, "TOOLS.md": {"name": "TOOLS.md"}},
        action="update",
        heartbeat_in_scratch=True,
    )

    assert warnings == []
    assert control_plane.events == [("set", "AGENTS.md"), ("delete", "TOOLS.md"), ("scratch", "lead-x")]
    assert control_plane.scratch_writes == [("lead-x", "1. Check in.")]


@pytest.mark.asyncio
async def test_keyed_layout_returns_scratch_warning_without_raising() -> None:
    control_plane = _ControlPlaneStub(scratch_warning="heartbeat_scratch.job_missing")

    warnings = await _lead_manager(control_plane)._set_agent_files(
        agent=_LEAD,  # type: ignore[arg-type]
        agent_id="lead-x",
        rendered=dict(_RENDERED),
        existing_files={},
        action="provision",
        heartbeat_in_scratch=True,
    )

    assert warnings == ["heartbeat_scratch.job_missing"]


@pytest.mark.asyncio
async def test_keyed_layout_skips_scratch_for_empty_heartbeat() -> None:
    control_plane = _ControlPlaneStub()

    await _lead_manager(control_plane)._set_agent_files(
        agent=_LEAD,  # type: ignore[arg-type]
        agent_id="lead-x",
        rendered={"AGENTS.md": "agents", "HEARTBEAT.md": ""},
        existing_files={},
        action="provision",
        heartbeat_in_scratch=True,
    )

    assert ("scratch", "lead-x") not in control_plane.events


@pytest.mark.asyncio
async def test_legacy_layout_still_writes_heartbeat_md_and_lead_raises_when_rejected() -> None:
    control_plane = _ControlPlaneStub(keyed=False, unsupported=frozenset({"HEARTBEAT.md"}))

    with pytest.raises(RuntimeError, match="HEARTBEAT.md"):
        await _lead_manager(control_plane)._set_agent_files(
            agent=_LEAD,  # type: ignore[arg-type]
            agent_id="lead-x",
            rendered=dict(_RENDERED),
            existing_files={},
            action="provision",
        )

    assert ("set", "HEARTBEAT.md") in control_plane.events
    assert control_plane.scratch_writes == []


@dataclass
class _AgentStub:
    name: str = "Worker"
    openclaw_session_id: str | None = None
    heartbeat_config: dict[str, Any] | None = None
    is_board_lead: bool = False
    id: UUID = field(default_factory=uuid4)
    identity_template: str | None = None
    soul_template: str | None = None


class _Manager(agent_provisioning.BaseAgentLifecycleManager):
    def _agent_id(self, agent: Any) -> str:
        return "agent-x"

    def _build_context(self, *, agent: Any, auth_token: str, user: Any, board: Any) -> dict[str, str]:
        return {"agent_name": agent.name}


@pytest.mark.parametrize("keyed", [True, False], ids=["keyed", "legacy"])
@pytest.mark.asyncio
async def test_provision_passes_layout_to_templates_and_file_routing(
    monkeypatch: pytest.MonkeyPatch,
    keyed: bool,
) -> None:
    seen: dict[str, Any] = {}

    def _fake_render(context: dict[str, str], agent: Any, file_names: set[str], **kwargs: Any) -> dict[str, str]:
        seen["context"] = dict(context)
        return {"AGENTS.md": "agents", "HEARTBEAT.md": "1. Check in."}

    async def _fake_set_agent_files(self: Any, **kwargs: Any) -> list[str]:
        seen["set_kwargs"] = kwargs
        return ["heartbeat_scratch.conflict"]

    monkeypatch.setattr(agent_provisioning, "_render_agent_files", _fake_render)
    monkeypatch.setattr(agent_provisioning.BaseAgentLifecycleManager, "_set_agent_files", _fake_set_agent_files)
    manager = _Manager(
        SimpleNamespace(workspace_root="/w"),  # type: ignore[arg-type]
        _ControlPlaneStub(keyed=keyed),  # type: ignore[arg-type]
    )

    warnings = await manager.provision(
        agent=_AgentStub(),  # type: ignore[arg-type]
        session_key="agent:agent-x:main",
        auth_token="token",
        user=None,
        options=agent_provisioning.ProvisionOptions(action="update"),
    )

    assert warnings == ["heartbeat_scratch.conflict"]
    assert seen["context"]["heartbeat_in_scratch"] == ("true" if keyed else "false")
    assert seen["set_kwargs"]["heartbeat_in_scratch"] is keyed


@pytest.mark.asyncio
async def test_apply_agent_lifecycle_returns_provision_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_openclaw_call(*args: Any, **kwargs: Any) -> object:
        return {"ok": True}

    async def _fake_provision(self: Any, **kwargs: Any) -> list[str]:
        return ["heartbeat_scratch.too_large"]

    monkeypatch.setattr(agent_provisioning, "openclaw_call", _fake_openclaw_call)
    monkeypatch.setattr(agent_provisioning.BaseAgentLifecycleManager, "provision", _fake_provision)
    gateway = SimpleNamespace(
        id=uuid4(),
        url="ws://gateway.example/ws",
        token=None,
        workspace_root="/w",
        allow_insecure_tls=False,
        disable_device_pairing=False,
    )
    agent = SimpleNamespace(name="Gateway Agent", openclaw_session_id="agent:main:main", board_id=None)

    result = await agent_provisioning.OpenClawGatewayProvisioner().apply_agent_lifecycle(
        agent=agent,  # type: ignore[arg-type]
        gateway=gateway,  # type: ignore[arg-type]
        board=None,
        auth_token="token",
        user=None,
        action="update",
        wake=False,
    )

    assert result.warnings == ("heartbeat_scratch.too_large",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_heartbeat_scratch_routing.py -q`
Expected: failures with `TypeError: ... got an unexpected keyword argument 'heartbeat_in_scratch'`, `KeyError: 'heartbeat_in_scratch'`, and `AttributeError: 'LifecycleResult' object has no attribute 'warnings'`; the legacy test passes.

- [ ] **Step 3: Write the implementation**

In `LifecycleResult`, after `wake_skip_reason: str | None = None` add:

```python
    # Heartbeat-scratch warning codes; they never fail the lifecycle (see heartbeat_scratch).
    warnings: tuple[str, ...] = ()
```

Replace the signature and the lines before the write loop of `_set_agent_files`:

```python
    async def _set_agent_files(
        self,
        *,
        agent: Agent | None = None,
        agent_id: str,
        rendered: dict[str, str],
        desired_file_names: set[str] | None = None,
        existing_files: dict[str, dict[str, Any]],
        action: str,
        overwrite: bool = False,
        heartbeat_in_scratch: bool = False,
    ) -> list[str]:
        heartbeat_instructions = ""
        if heartbeat_in_scratch:
            # 2026.8+ gateways never read HEARTBEAT.md and reject writing it; the checklist goes
            # to heartbeat monitor scratch once the physical files are in place.
            rendered = dict(rendered)
            heartbeat_instructions = rendered.pop("HEARTBEAT.md", "")
            if desired_file_names is not None:
                desired_file_names = desired_file_names - {"HEARTBEAT.md"}
        preserve_files = (
            self._preserve_files(agent) if agent is not None else set(PRESERVE_AGENT_EDITABLE_FILES)
        )
        target_file_names = desired_file_names or set(rendered.keys())
        unsupported_names: list[str] = []
```

(The `for name, content in rendered.items():` loop and the lead `RuntimeError` block stay unchanged.)

Replace everything from `if agent is None or not self._allow_stale_file_deletion(agent):` to the end of `_set_agent_files` with:

```python
        if agent is not None and self._allow_stale_file_deletion(agent):
            stale_names = (
                set(existing_files.keys()) & self._stale_file_candidates(agent)
            ) - target_file_names
            if heartbeat_in_scratch:
                stale_names.discard("HEARTBEAT.md")
            for name in sorted(stale_names):
                try:
                    await self._control_plane.delete_agent_file(agent_id=agent_id, name=name)
                except OpenClawGatewayError as exc:
                    message = str(exc).lower()
                    if any(
                        marker in message
                        for marker in (
                            "unsupported",
                            "unknown method",
                            "not found",
                            "no such file",
                        )
                    ):
                        continue
                    raise

        if not heartbeat_instructions:
            return []
        warning = await self._control_plane.write_heartbeat_scratch(
            agent_id=agent_id,
            instructions=heartbeat_instructions,
        )
        return [] if warning is None else [warning]
```

In `provision`, change the return annotation `) -> None:` to `) -> list[str]:`. Replace

```python
        context = await self._augment_context(agent=agent, context=context)
```

with

```python
        context = await self._augment_context(agent=agent, context=context)
        # Known from the config read in upsert_agent (patch_agent_heartbeats).
        heartbeat_in_scratch = await self._control_plane.uses_keyed_agent_entries()
        context["heartbeat_in_scratch"] = "true" if heartbeat_in_scratch else "false"
```

and replace the final `await self._set_agent_files(` call with:

```python
        return await self._set_agent_files(
            agent=agent,
            agent_id=agent_id,
            rendered=rendered,
            desired_file_names=set(rendered.keys()),
            existing_files=existing_files,
            action=options.action,
            overwrite=options.overwrite,
            heartbeat_in_scratch=heartbeat_in_scratch,
        )
```

In `apply_agent_lifecycle`, replace `await manager.provision(` with `provision_warnings = tuple(await manager.provision(` and close the call with `))` instead of `)`. Then add `warnings=provision_warnings` to all three `LifecycleResult(...)` returns:

```python
            return LifecycleResult(wake_delivered=False, wake_skip_reason=None, warnings=provision_warnings)
```
```python
            return LifecycleResult(
                wake_delivered=False,
                wake_skip_reason=WAKE_SKIP_CREDENTIALS_NOT_VISIBLE,
                warnings=provision_warnings,
            )
```
```python
        return LifecycleResult(wake_delivered=True, wake_skip_reason=None, warnings=provision_warnings)
```

Update the existing fakes whose return value now flows into `tuple(...)`:
- `backend/tests/test_agent_provisioning_utils.py`: in the two fakes whose body is `call_log.append("set_agent_files")`, add `return []` as the next line; in the three fakes whose body is `return None`, change it to `return []`. Verify with `grep -n "_fake_set_agent_files" -A 2 tests/test_agent_provisioning_utils.py`.
- `backend/tests/test_global_heartbeats_reconciliation.py` lines ~307 and ~382: `_fake_provision` bodies `return None` → `return []`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_heartbeat_scratch_routing.py tests/test_agent_provisioning_utils.py tests/test_global_heartbeats_reconciliation.py tests/test_lifecycle_services.py tests/test_gateway_config_layout.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/openclaw/provisioning.py backend/tests/test_heartbeat_scratch_routing.py backend/tests/test_agent_provisioning_utils.py backend/tests/test_global_heartbeats_reconciliation.py
git -c user.name=foxsky -c user.email=miguel@viapersonal.com.br commit -m "feat(openclaw): deliver heartbeat checklist via monitor scratch on 2026.8+ gateways

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0153jb2YEniuYF1icYb5wPXK"
```

---

### Task 6: Surface warnings in template sync

**Files:**
- Modify: `backend/app/services/openclaw/lifecycle_orchestrator.py` (`__init__` ~84; `run_lifecycle` ~114 and after the try/except ~246)
- Modify: `backend/app/schemas/gateways.py` (`GatewayTemplatesSyncResult` ~84-91)
- Modify: `backend/app/services/openclaw/provisioning_db.py` (`_append_sync_error` ~507; `_sync_one_agent` ~650-697; `_sync_main_agent` ~742-806)
- Modify: `backend/scripts/sync_gateway_templates.py` (~115)
- Modify: `frontend/src/api/generated/model/gatewayTemplatesSyncResult.ts`
- Test: `backend/tests/test_lifecycle_services.py` (append), `backend/tests/test_heartbeat_scratch_routing.py` (append)

**Interfaces:**
- Consumes: `LifecycleResult.warnings` (Task 5).
- Produces: `AgentLifecycleOrchestrator.last_lifecycle_warnings: tuple[str, ...]`; `GatewayTemplatesSyncResult.warnings: list[GatewayTemplatesSyncError]`; `_append_sync_warnings(result, warnings, *, agent, board=None) -> None` in `provisioning_db`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_lifecycle_services.py`:

```python
@pytest.mark.asyncio
async def test_run_lifecycle_exposes_lifecycle_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_orchestrator_stub_agent()
    agent.agent_token_hash = "existing"
    board = SimpleNamespace(id=agent.board_id, gateway_id=agent.gateway_id, name="Dev Squad")
    gateway = SimpleNamespace(
        id=agent.gateway_id,
        url="ws://gw.example/ws",
        token=None,
        workspace_root="/tmp/openclaw",
        organization_id=uuid4(),
        allow_insecure_tls=False,
        disable_device_pairing=False,
    )

    async def _fake_lock_agent(self, *, agent_id):
        return agent

    async def _fake_get_existing_auth_token(*, agent_gateway_id, control_plane):
        return "existing-raw-token"

    async def _fake_apply_agent_lifecycle(self, **kwargs):
        return LifecycleResult(wake_delivered=False, warnings=("heartbeat_scratch.conflict",))

    import app.core.agent_tokens as agent_tokens_module
    import app.services.openclaw.gateway_resolver as gateway_resolver_module
    import app.services.openclaw.provisioning_db as provisioning_db_module

    monkeypatch.setattr(
        lifecycle_orchestrator_module.AgentLifecycleOrchestrator,
        "_lock_agent",
        _fake_lock_agent,
    )
    monkeypatch.setattr(
        provisioning_db_module,
        "_get_existing_auth_token",
        _fake_get_existing_auth_token,
    )
    monkeypatch.setattr(agent_tokens_module, "verify_agent_token", lambda raw, hashed: True)
    monkeypatch.setattr(
        gateway_resolver_module,
        "optional_gateway_client_config",
        lambda gw: GatewayClientConfig(url="ws://gw.example/ws", token=None),
    )
    monkeypatch.setattr(
        provisioning_module.OpenClawGatewayProvisioner,
        "apply_agent_lifecycle",
        _fake_apply_agent_lifecycle,
    )
    monkeypatch.setattr(lifecycle_orchestrator_module, "enqueue_lifecycle_reconcile", lambda task: None)
    orchestrator = lifecycle_orchestrator_module.AgentLifecycleOrchestrator(
        _OrchestratorFakeSession(),  # type: ignore[arg-type]
    )

    await orchestrator.run_lifecycle(
        gateway=gateway,  # type: ignore[arg-type]
        agent_id=agent.id,
        board=board,  # type: ignore[arg-type]
        user=None,
        action="update",
        wake=False,
    )

    assert orchestrator.last_lifecycle_warnings == ("heartbeat_scratch.conflict",)
    assert agent.last_provision_error is None
```

Append to `backend/tests/test_heartbeat_scratch_routing.py`:

```python
def test_sync_warnings_are_recorded_apart_from_errors() -> None:
    import app.services.openclaw.provisioning_db as provisioning_db
    from app.schemas.gateways import GatewayTemplatesSyncResult

    result = GatewayTemplatesSyncResult(
        gateway_id=uuid4(),
        include_main=True,
        reset_sessions=False,
        agents_updated=1,
        agents_skipped=0,
        main_updated=False,
    )
    agent = SimpleNamespace(id=uuid4(), name="Supervisor")
    board = SimpleNamespace(id=uuid4())

    provisioning_db._append_sync_warnings(
        result,
        ("heartbeat_scratch.job_missing",),
        agent=agent,  # type: ignore[arg-type]
        board=board,  # type: ignore[arg-type]
    )

    assert result.errors == []
    assert [(w.agent_name, w.board_id, w.message) for w in result.warnings] == [
        ("Supervisor", board.id, "heartbeat_scratch.job_missing"),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lifecycle_services.py::test_run_lifecycle_exposes_lifecycle_warnings tests/test_heartbeat_scratch_routing.py::test_sync_warnings_are_recorded_apart_from_errors -q`
Expected: `AttributeError: 'AgentLifecycleOrchestrator' object has no attribute 'last_lifecycle_warnings'` and `AttributeError: module ... has no attribute '_append_sync_warnings'`.

- [ ] **Step 3: Write the implementation**

`lifecycle_orchestrator.py` — replace `__init__` with:

```python
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        # Warning codes from the most recent run_lifecycle (e.g. heartbeat scratch problems).
        self.last_lifecycle_warnings: tuple[str, ...] = ()
```

In `run_lifecycle`, make the first statement after the docstring:

```python
        self.last_lifecycle_warnings = ()
```

Directly after the `try: ... except (OSError, RuntimeError, ValueError) as exc: ... return locked` block (before the `# Branch on the wake-delivery outcome.` comment) add:

```python
        self.last_lifecycle_warnings = lifecycle_result.warnings
```

`backend/app/schemas/gateways.py` — in `GatewayTemplatesSyncResult`, after `errors: ...` add:

```python
    # Non-fatal per-agent problems (e.g. heartbeat scratch not written); never counted as errors.
    warnings: list[GatewayTemplatesSyncError] = Field(default_factory=list)
```

`provisioning_db.py` — after `_append_sync_error` add:

```python
def _append_sync_warnings(
    result: GatewayTemplatesSyncResult,
    warnings: tuple[str, ...],
    *,
    agent: Agent,
    board: Board | None = None,
) -> None:
    for warning in warnings:
        result.warnings.append(
            GatewayTemplatesSyncError(
                agent_id=agent.id,
                agent_name=agent.name,
                board_id=board.id if board else None,
                message=warning,
            ),
        )
```

In `_sync_one_agent`, directly after `try:` (before `async def _do_provision() -> bool:`) add `orchestrator = AgentLifecycleOrchestrator(ctx.session)`, change `await AgentLifecycleOrchestrator(ctx.session).run_lifecycle(` to `await orchestrator.run_lifecycle(`, and after `result.agents_updated += 1` add:

```python
        _append_sync_warnings(result, orchestrator.last_lifecycle_warnings, agent=agent, board=board)
```

In `_sync_main_agent`, directly after `try:` (before `async def _do_provision_main() -> bool:`) add `orchestrator = AgentLifecycleOrchestrator(ctx.session)`, change `await AgentLifecycleOrchestrator(ctx.session).run_lifecycle(` to `await orchestrator.run_lifecycle(`, and replace the final

```python
    else:
        result.main_updated = True
```

with

```python
    else:
        result.main_updated = True
        _append_sync_warnings(result, orchestrator.last_lifecycle_warnings, agent=main_agent)
```

`backend/scripts/sync_gateway_templates.py` — directly before `if result.errors:` add:

```python
    if result.warnings:
        sys.stdout.write("warnings:\n")
        for warning in result.warnings:
            agent = f"{warning.agent_name} ({warning.agent_id})" if warning.agent_id else "n/a"
            sys.stdout.write(
                f"- agent={agent} board_id={warning.board_id} message={warning.message}\n",
            )
```

`frontend/src/api/generated/model/gatewayTemplatesSyncResult.ts` — after `reset_sessions: boolean;` add `  warnings?: GatewayTemplatesSyncError[];` (keeps the generated model in sync with the schema; orval sorts keys alphabetically).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lifecycle_services.py tests/test_heartbeat_scratch_routing.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/openclaw/lifecycle_orchestrator.py backend/app/schemas/gateways.py backend/app/services/openclaw/provisioning_db.py backend/scripts/sync_gateway_templates.py frontend/src/api/generated/model/gatewayTemplatesSyncResult.ts backend/tests/test_lifecycle_services.py backend/tests/test_heartbeat_scratch_routing.py
git -c user.name=foxsky -c user.email=miguel@viapersonal.com.br commit -m "feat(openclaw): report heartbeat scratch warnings in template sync

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0153jb2YEniuYF1icYb5wPXK"
```

---

### Task 7: Templates stop pointing at HEARTBEAT.md on keyed layouts

**Files:**
- Modify: `backend/templates/BOARD_HEARTBEAT.md.j2` (titles at lines 15, 42, 186; "this file is" ×3; end of file)
- Modify: `backend/templates/BOARD_AGENTS.md.j2` (lines 1-3, 141, 312, 340, 590, 593, 785, 849)
- Modify: `backend/templates/BOARD_BOOTSTRAP.md.j2` (line 36)
- Modify: `backend/templates/README.md` (section "HEARTBEAT.md selection logic", ~96-104)
- Test: `backend/tests/test_heartbeat_scratch_templates.py` (create)

**Interfaces:**
- Consumes: context key `heartbeat_in_scratch` (`"true"`/`"false"`, Task 5).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_heartbeat_scratch_templates.py`:

```python
# ruff: noqa: INP001
"""Templates must not send agents to HEARTBEAT.md when the checklist lives in scratch."""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import FileSystemLoader, Undefined

from app.services.openclaw.provisioning import _template_env

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
_CONTEXT = {
    "agent_name": "Worker-Agent-Sample",
    "agent_id": "00000000-0000-4000-8000-000000000001",
    "board_id": "00000000-0000-4000-8000-000000000002",
    "base_url": "http://192.168.2.64:8000",
    "auth_token": "sample-agent-token-000000000000000000000",
    "workspace_root": "/root/.openclaw/workspace",
    "workspace_path": "/root/.openclaw/workspace/workspace-mc-sample",
}
_ROLES = {
    "main": {"is_main_agent": "true", "is_board_lead": "false"},
    "lead": {"is_main_agent": "false", "is_board_lead": "true"},
    "worker": {"is_main_agent": "false", "is_board_lead": "false"},
}
_TEMPLATES = ["BOARD_AGENTS.md.j2", "BOARD_BOOTSTRAP.md.j2", "BOARD_HEARTBEAT.md.j2"]


def _render(template: str, role: str, heartbeat_in_scratch: str) -> str:
    env = _template_env()
    env.loader = FileSystemLoader(str(TEMPLATES_DIR))
    env.undefined = Undefined  # optional template variables are omitted here
    context = {**_CONTEXT, **_ROLES[role], "heartbeat_in_scratch": heartbeat_in_scratch}
    return env.get_template(template).render(**context)


@pytest.mark.parametrize("role", sorted(_ROLES))
@pytest.mark.parametrize("template", _TEMPLATES)
def test_keyed_layout_renders_never_mention_heartbeat_md(template: str, role: str) -> None:
    assert "HEARTBEAT.md" not in _render(template, role, "true")


@pytest.mark.parametrize("role", ["lead", "worker"])  # main AGENTS.md has no Heartbeats section
def test_legacy_layout_agents_md_still_reads_heartbeat_md(role: str) -> None:
    rendered = _render("BOARD_AGENTS.md.j2", role, "false")
    assert "Read `HEARTBEAT.md` first." in rendered


@pytest.mark.parametrize("role", sorted(_ROLES))
def test_keyed_agents_md_stays_under_bootstrap_cap(role: str) -> None:
    # Lead/worker AGENTS.md already render at ~19,200 chars; OpenClaw truncates past 20,000.
    assert len(_render("BOARD_AGENTS.md.j2", role, "true")) <= 20_000


@pytest.mark.parametrize("role", sorted(_ROLES))
def test_heartbeat_checklist_titles_are_destination_neutral(role: str) -> None:
    for layout in ("true", "false"):
        rendered = _render("BOARD_HEARTBEAT.md.j2", role, layout)
        assert "# Heartbeat checklist" in rendered
        assert "# HEARTBEAT.md" not in rendered


@pytest.mark.parametrize("role", sorted(_ROLES))
def test_keyed_heartbeat_checklist_explains_scratch_ownership(role: str) -> None:
    assert "## Agent notes" in _render("BOARD_HEARTBEAT.md.j2", role, "true")
    assert "## Agent notes" not in _render("BOARD_HEARTBEAT.md.j2", role, "false")


def test_keyed_heartbeat_checklist_fits_scratch_with_room_for_notes() -> None:
    for role in _ROLES:
        size = len(_render("BOARD_HEARTBEAT.md.j2", role, "true").encode("utf-8"))
        # OpenClaw scratch limit is 262,144 UTF-8 bytes; keep most of it for agent notes.
        assert size <= 64_000, f"{role} checklist is {size} bytes"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_heartbeat_scratch_templates.py -q`
Expected: `keyed_layout_renders_never_mention_heartbeat_md`, `titles_are_destination_neutral`, and `explains_scratch_ownership` fail; the legacy AGENTS test and both size tests pass.

- [ ] **Step 3: Edit the templates**

`BOARD_HEARTBEAT.md.j2`:
- Replace each of the three lines `# HEARTBEAT.md` (lines 15, 42, 186) with `# Heartbeat checklist`.
- Replace each `— this file is` (3 occurrences, lines 17, 44, 188) with `— this is`. Check: `grep -c "— this file is" templates/BOARD_HEARTBEAT.md.j2` → `0` afterwards.
- Append after the final line of the file:

```jinja
{% if (heartbeat_in_scratch | default("false") | string | lower) == "true" %}

## Scratch ownership
This checklist is heartbeat monitor scratch managed by Mission Control. If you update scratch
with `heartbeat_respond`, keep the Mission Control marker lines and the text between them
unchanged, and put your own notes under `## Agent notes`.
{% endif %}
```

`BOARD_AGENTS.md.j2`:
- After line 3 (`{% set role_tag = ... %}`) add:

```jinja
{% set heartbeat_in_scratch_enabled = (heartbeat_in_scratch | default("false") | string | lower) == "true" %}
```

- Replace line `6) \`HEARTBEAT.md\`` with:

```jinja
{% if not heartbeat_in_scratch_enabled %}
6) `HEARTBEAT.md`
{% endif %}
```

- Replace both occurrences of `do not exceed the cap from \`HEARTBEAT.md\` and \`worker-parallel-scheduler\`` with `do not exceed the cap from the heartbeat checklist and \`worker-parallel-scheduler\``.
- Replace `Authoritative worker playbook. HEARTBEAT.md references this section by step number — do not duplicate content there.` with `Authoritative worker playbook. The heartbeat checklist references this section by step number — do not duplicate content there.`
- Replace `Source Setup vars from HEARTBEAT.md, then execute steps 1-9. Do NOT just reply "OK".` with `Use the \`## Tools\` values at the top of this file, then execute steps 1-9. Do NOT just reply "OK".` (the checklist has no Setup section; the values live in `## Tools`).
- Replace `SOUL.md and HEARTBEAT.md reference this section.` with `SOUL.md and the heartbeat checklist reference this section.`
- Replace the line `Read \`HEARTBEAT.md\` first. Edit memory only for material changes; never for clean startup/check-in/no-op. Update/escalate only on real progress/blocker; otherwise return \`HEARTBEAT_OK\`.` with:

```jinja
{% if heartbeat_in_scratch_enabled %}
Follow the heartbeat checklist ("Heartbeat monitor scratch" in the heartbeat prompt) first; when rewriting scratch keep Mission Control's marked block and add notes under `## Agent notes`. Edit memory only for material changes; never for clean startup/check-in/no-op. Update/escalate only on real progress/blocker; otherwise return `HEARTBEAT_OK`.
{% else %}
Read `HEARTBEAT.md` first. Edit memory only for material changes; never for clean startup/check-in/no-op. Update/escalate only on real progress/blocker; otherwise return `HEARTBEAT_OK`.
{% endif %}
```

`BOARD_BOOTSTRAP.md.j2` — replace line 36 `- \`AGENTS.md\`, \`IDENTITY.md\`, \`SOUL.md\`, \`USER.md\`, \`MEMORY.md\`, \`HEARTBEAT.md\`, \`BOOTSTRAP.md\`` with:

```jinja
{% if (heartbeat_in_scratch | default("false") | string | lower) == "true" %}
- `AGENTS.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, `MEMORY.md`, `BOOTSTRAP.md`
{% else %}
- `AGENTS.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md`, `BOOTSTRAP.md`
{% endif %}
```

`templates/README.md` — at the end of the "HEARTBEAT.md selection logic" section (after the `is_board_lead` bullet list) add:

```markdown
On OpenClaw 2026.8+ gateways (keyed `agents.entries` config) the gateway never reads
`HEARTBEAT.md`. Mission Control writes the rendered checklist into the agent's heartbeat
monitor scratch instead, inside `mission-control:heartbeat` marker lines, and keeps any other
scratch text under `## Agent notes` (`app/services/openclaw/heartbeat_scratch.py`). Templates
receive `heartbeat_in_scratch` (`"true"`/`"false"`) to pick matching wording.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_heartbeat_scratch_templates.py tests/test_template_size_budget.py tests/test_tools_md_parsing.py -q`
Expected: all pass (size-budget tests may skip where they already skip).

- [ ] **Step 5: Commit**

```bash
git add backend/templates/BOARD_HEARTBEAT.md.j2 backend/templates/BOARD_AGENTS.md.j2 backend/templates/BOARD_BOOTSTRAP.md.j2 backend/templates/README.md backend/tests/test_heartbeat_scratch_templates.py
git -c user.name=foxsky -c user.email=miguel@viapersonal.com.br commit -m "feat(templates): point agents at the heartbeat checklist in scratch on 2026.8+

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0153jb2YEniuYF1icYb5wPXK"
```

---

### Task 8: Full gate, spec touch-up, read-only production validation

**Files:**
- Modify: `docs/plans/2026-09-14-heartbeat-scratch-design.md` (Architecture item 6, last bullet)
- Scratch (not committed): `$S=/tmp/claude-0/-root-openclaw/d3bd8486-2b75-403e-b180-e30498976df9/scratchpad`

- [ ] **Step 1: Full backend gate**

Run from `backend/`:

```bash
uv run isort . && uv run black . && uv run flake8 --config .flake8 && uv run mypy && uv run pytest -q
```

Expected: formatters leave no diff after the first run; flake8, mypy, and pytest pass. Fix any finding in the task's own files, then re-run.

- [ ] **Step 2: Spec touch-up**

In the design spec, replace the bullet `` - `scripts/check_agent_workspace_drift.py`: skip `HEARTBEAT.md` on keyed layouts. `` with `` - `scripts/check_agent_workspace_drift.py` needs no change: it only compares template/workspace pairs the operator passes. ``. Commit both the spec and any formatter changes:

```bash
git add -A docs backend
git -c user.name=foxsky -c user.email=miguel@viapersonal.com.br commit -m "chore: format and align heartbeat scratch spec with implementation

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0153jb2YEniuYF1icYb5wPXK"
```

- [ ] **Step 3: Read MC agents' real heartbeat config from production (read-only)**

```bash
S=/tmp/claude-0/-root-openclaw/d3bd8486-2b75-403e-b180-e30498976df9/scratchpad
ssh root@192.168.2.64 'bash -s' > "$S/mc-agents-heartbeats.json" <<'EOF'
set -euo pipefail
cd /home/mcontrol/openclaw-mission-control/backend
/root/.local/bin/uv run --no-sync python - <<'PY'
import asyncio, json
from sqlmodel import col, select
from app.db.session import async_session_maker
from app.models.agents import Agent

async def main() -> None:
    async with async_session_maker() as session:
        rows = (await session.exec(
            select(Agent).where(col(Agent.gateway_id) == "3821a85a-984c-412a-9340-cda50eaf174e")
        )).all()
    print(json.dumps([
        {"name": a.name, "session": a.openclaw_session_id, "is_board_lead": a.is_board_lead,
         "board_id": str(a.board_id) if a.board_id else None, "heartbeat_config": a.heartbeat_config}
        for a in rows
    ]))

asyncio.run(main())
PY
EOF
python3 -c "import json; print(len(json.load(open('$S/mc-agents-heartbeats.json'))), 'agents')"
```

Expected: `8 agents`.

- [ ] **Step 4: Build MC's config patch from real data and validate it with OpenClaw's own code**

Write `$S/mc-scratch-prompt-harness.py`:

```python
import asyncio, json, sys
from types import SimpleNamespace

import app.services.openclaw.provisioning as p

S = sys.argv[1]
cfg = json.load(open("/root/.openclaw/openclaw.json"))
rows = json.load(open(f"{S}/mc-agents-heartbeats.json"))
entries_cfg = cfg["agents"]["entries"]


def desired(row):
    key = row["session"].split(":")[1]
    stub = SimpleNamespace(id=row["name"], name=row["name"], heartbeat_config=row["heartbeat_config"])
    heartbeat = p._heartbeat_config(stub)
    if p._is_disabled_heartbeat_every(heartbeat.get("every")):
        heartbeat = {**heartbeat, "every": "0m"}
    return key, entries_cfg[key]["workspace"], heartbeat


async def run_patch(config):
    captured = {}

    async def fake(method, params=None, config_=None, **kwargs):
        if method == "config.get":
            return {"hash": "live", "config": config}
        if method == "config.patch":
            captured.update(params)
            return {"ok": True}
        raise AssertionError(method)

    p.openclaw_call = fake
    plane = p.OpenClawGatewayControlPlane(p.GatewayClientConfig(url="ws://x", token=None))
    # One lifecycle per agent, as provisioning does.
    patches = []
    for entry in map(desired, rows):
        captured.clear()
        await plane.patch_agent_heartbeats([entry])
        if captured:
            patches.append(json.loads(captured["raw"]))
    return patches


patches = asyncio.run(run_patch(cfg))
merged = {}
for patch in patches:
    for agent_id, entry in patch.get("agents", {}).get("entries", {}).items():
        merged.setdefault("agents", {}).setdefault("entries", {})[agent_id] = entry
json.dump(merged, open(f"{S}/mc-scratch-prompt-patch.json", "w"))
sent = merged.get("agents", {}).get("entries", {})
print(f"patches={len(patches)} entries={len(sent)} prompt_nulls={sum(1 for e in sent.values() if (e.get('heartbeat') or {}).get('prompt', 'x') is None)}")
```

Run:

```bash
cd backend && uv run python "$S/mc-scratch-prompt-harness.py" "$S"
node "$S/validate-patch.mjs" "$S/mc-scratch-prompt-patch.json"
```

Expected: harness prints `prompt_nulls=8`; validator prints `{"ok":true,"issues":[],...}`.

Then confirm convergence: apply the patch to a copy and re-run the harness against it:

```bash
node -e '
const fs=require("fs");
import("/usr/lib/node_modules/openclaw/dist/merge-patch-CuIkmMlL.mjs").then(({t:apply})=>{
  const base=JSON.parse(fs.readFileSync("/root/.openclaw/openclaw.json","utf8"));
  const patch=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));
  fs.writeFileSync(process.argv[2], JSON.stringify(apply(base,patch,{mergeObjectArraysById:true})));
});' "$S/mc-scratch-prompt-patch.json" "$S/openclaw-after-patch.json"
sed 's#/root/.openclaw/openclaw.json#'"$S"'/openclaw-after-patch.json#' "$S/mc-scratch-prompt-harness.py" > "$S/mc-scratch-prompt-harness-after.py"
cd backend && uv run python "$S/mc-scratch-prompt-harness-after.py" "$S"
```

Expected: the second run prints `prompt_nulls=0`, and every remaining patch contains no `prompt` key (entries still patched only because of the pre-existing gateway-only-field comparison, which the spec lists as out of scope). Record the exact output.

- [ ] **Step 5: Resolve live monitor jobs and size real checklists (read-only)**

```bash
S=/tmp/claude-0/-root-openclaw/d3bd8486-2b75-403e-b180-e30498976df9/scratchpad
for id in $(python3 -c "import json;print(' '.join(r['session'].split(':')[1] for r in json.load(open('$S/mc-agents-heartbeats.json'))))"); do
  openclaw gateway call cron.list --json --params "{\"agentId\":\"$id\",\"includeDisabled\":true,\"limit\":200}" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);j=[x for x in d['jobs'] if x.get('declarationKey')=='heartbeat:$id' and (x.get('payload') or {}).get('kind')=='heartbeat'];print('$id', j[0]['id'] if j else 'MISSING', 'enabled=%s'%(j[0]['enabled'] if j else None))"
done
```

Expected: one job id per MC agent, none `MISSING`. Then render each role's keyed checklist and check bytes:

```bash
cd backend && uv run pytest tests/test_heartbeat_scratch_templates.py::test_keyed_heartbeat_checklist_fits_scratch_with_room_for_notes -q
```

Expected: pass.

- [ ] **Step 6: Report and stop**

Report to the operator: commits on `design/heartbeat-scratch`, gate results, harness outputs (prompt nulls, validator result, convergence output), live job resolution, and the rollout checklist from the spec (back up `openclaw.json` and all scratch, deploy, verify markers + prompt deletion + "Heartbeat monitor scratch:" in a heartbeat run + no sync warnings). Ask for approval before pushing the branch, opening the PR, merging, or deploying.
