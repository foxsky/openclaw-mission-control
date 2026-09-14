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
