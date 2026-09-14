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
    assert not _openclaw_effectively_empty(
        heartbeat_scratch.splice_mc_block(existing, INSTRUCTIONS)
    )


def test_markers_with_empty_notes_alone_would_be_empty() -> None:
    # Guards the port: markers + heading only is empty in OpenClaw, so MC must always
    # ship real instructions inside the block.
    assert _openclaw_effectively_empty(f"{BEGIN}\n{END}\n\n## Agent notes\n")
