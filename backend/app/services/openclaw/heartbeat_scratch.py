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
