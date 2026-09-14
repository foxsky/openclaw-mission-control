"""Mission Control heartbeat instructions in OpenClaw heartbeat monitor scratch.

OpenClaw 2026.8+ (keyed ``agents.entries`` layout) never reads ``HEARTBEAT.md``. Ordinary
heartbeat polls append the agent's monitor-job scratch to the heartbeat prompt instead.
Mission Control owns one marked block at the top of that scratch; every other line is kept
as agent notes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.services.openclaw.gateway_rpc import OpenClawGatewayError

logger = get_logger(__name__)

MC_BLOCK_BEGIN = (
    "<!-- mission-control:heartbeat:begin "
    "(managed by Mission Control; edits here are overwritten) -->"
)
MC_BLOCK_END = "<!-- mission-control:heartbeat:end -->"
AGENT_NOTES_HEADING = "## Agent notes"

# OpenClaw's CRON_JOB_SCRATCH_MAX_BYTES (UTF-8 bytes); used only if a gateway omits maxBytes.
DEFAULT_SCRATCH_MAX_BYTES = 262_144

WARNING_JOB_MISSING = "heartbeat_scratch.job_missing"
WARNING_CONFLICT = "heartbeat_scratch.conflict"
WARNING_TOO_LARGE = "heartbeat_scratch.too_large"
WARNING_GATEWAY_ERROR = "heartbeat_scratch.gateway_error"

GatewayCall = Callable[[str, dict[str, Any]], Awaitable[object]]


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
