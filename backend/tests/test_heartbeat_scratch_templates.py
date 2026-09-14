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

# Authoritative per-file bootstrap injection cap. Must match
# BOOTSTRAP_PER_FILE_MAX_CHARS in test_template_size_budget.py (and the live
# gateway's agents.defaults.bootstrapMaxChars=30000) — not the generic
# OpenClaw docs default of 20,000, which this codebase deliberately raised
# to fit the deterministic lead playbook. Repo convention has no
# cross-test-module imports, so this is redefined here rather than imported.
_BOOTSTRAP_PER_FILE_MAX_CHARS = 30_000

# Realistic provisioning context matching what ``_build_context`` in
# ``app/services/openclaw/provisioning.py`` injects at runtime — mirrors
# ``_REALISTIC_RENDER_CONTEXT`` in test_template_size_budget.py.
_REALISTIC_CONTEXT = {
    "agent_name": "Worker-Agent-Sample",
    "agent_id": "00000000-0000-4000-8000-000000000001",
    "board_id": "00000000-0000-4000-8000-000000000002",
    "base_url": "http://192.168.2.64:8000",
    "auth_token": "sample-agent-token-with-realistic-length-000000000000",
    "user_timezone": "America/Sao_Paulo",
    "shared_workspace": "/shared",
    "workspace_path": "/root/.openclaw/workspace/workspace-mc-sample",
    "board_rule_require_review_before_done": "true",
    "board_rule_require_approval_for_done": "true",
    "board_rule_comment_required_for_review": "true",
    "board_rule_block_status_changes_with_pending_approval": "true",
    "board_rule_only_lead_can_change_status": "true",
    "board_rule_max_agents": "6",
}

# Realistic role/profile shapes matching the variants exercised in
# test_template_size_budget.py (main/lead/plain worker, plus DevOps,
# review-only Architect, and worktree-parallel backend) so the keyed-layout
# checks below cover what the gateway actually provisions, not just the
# minimal role skeleton in `_ROLES`.
_REALISTIC_VARIANTS: dict[str, dict[str, object]] = {
    "main": {"is_main_agent": "true", "is_board_lead": "false"},
    "lead": {
        "is_main_agent": "false",
        "is_board_lead": "true",
        "agent_name": "Supervisor",
        "identity_role": "Board Lead",
    },
    "worker": {
        "is_main_agent": "false",
        "is_board_lead": "false",
        "agent_name": "Programmer-Frontend",
        "identity_role": "Frontend Developer",
    },
    "devops_worker": {
        "is_main_agent": "false",
        "is_board_lead": "false",
        "agent_name": "DevOps",
        "identity_role": "DevOps Engineer",
        "identity_dev_acp_flow": "codex_with_optional_claude_review",
    },
    "architect_review_only": {
        "is_main_agent": "false",
        "is_board_lead": "false",
        "agent_name": "Architect",
        "identity_role": "System Architect and Code Reviewer",
        "identity_dev_acp_flow": "review_only",
    },
    "backend_dev_worker": {
        "is_main_agent": "false",
        "is_board_lead": "false",
        "agent_name": "Programmer-Backend",
        "identity_role": "Backend Developer",
        "identity_dev_acp_flow": "codex_then_claude_review",
    },
    "backend_worktree_parallel_worker": {
        "is_main_agent": "false",
        "is_board_lead": "false",
        "agent_name": "Programmer-Backend",
        "identity_role": "Backend Developer",
        "identity_dev_acp_flow": "codex_then_claude_review",
        "identity_worker_parallel_mode": "worktree",
    },
}


def _render(template: str, role: str, heartbeat_in_scratch: str) -> str:
    env = _template_env()
    env.loader = FileSystemLoader(str(TEMPLATES_DIR))
    env.undefined = Undefined  # optional template variables are omitted here
    context = {**_CONTEXT, **_ROLES[role], "heartbeat_in_scratch": heartbeat_in_scratch}
    return env.get_template(template).render(**context)


def _render_variant(template: str, variant: str, heartbeat_in_scratch: str) -> str:
    env = _template_env()
    env.loader = FileSystemLoader(str(TEMPLATES_DIR))
    env.undefined = Undefined  # optional template variables are omitted here
    context = {
        **_REALISTIC_CONTEXT,
        **_REALISTIC_VARIANTS[variant],
        "heartbeat_in_scratch": heartbeat_in_scratch,
    }
    return env.get_template(template).render(**context)


@pytest.mark.parametrize("role", sorted(_ROLES))
@pytest.mark.parametrize("template", _TEMPLATES)
def test_keyed_layout_renders_never_mention_heartbeat_md(template: str, role: str) -> None:
    assert "HEARTBEAT.md" not in _render(template, role, "true")


@pytest.mark.parametrize("variant", sorted(_REALISTIC_VARIANTS))
def test_keyed_agents_md_realistic_variants_never_mention_heartbeat_md(variant: str) -> None:
    assert "HEARTBEAT.md" not in _render_variant("BOARD_AGENTS.md.j2", variant, "true")


@pytest.mark.parametrize("role", ["lead", "worker"])  # main AGENTS.md has no Heartbeats section
def test_legacy_layout_agents_md_still_reads_heartbeat_md(role: str) -> None:
    rendered = _render("BOARD_AGENTS.md.j2", role, "false")
    assert "Read `HEARTBEAT.md` first." in rendered


@pytest.mark.parametrize("variant", sorted(_REALISTIC_VARIANTS))
def test_keyed_agents_md_stays_under_bootstrap_cap(variant: str) -> None:
    # Realistic worker variants (DevOps, review-only Architect,
    # worktree-parallel backend) render 21k-23.4k chars in keyed layout —
    # well past the 20,000 placeholder this test used before it was
    # corrected to the project's authoritative 30,000 cap (see
    # BOOTSTRAP_PER_FILE_MAX_CHARS in test_template_size_budget.py).
    size = len(_render_variant("BOARD_AGENTS.md.j2", variant, "true"))
    assert size <= _BOOTSTRAP_PER_FILE_MAX_CHARS, f"{variant} AGENTS.md is {size} chars"


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


@pytest.mark.parametrize("variant", ["devops_worker", "backend_dev_worker"])
def test_keyed_heartbeat_checklist_has_single_blank_before_scratch_ownership(
    variant: str,
) -> None:
    # DevOps (frontend/backend parallel-mode block skipped) previously left
    # a double blank line before "## Scratch ownership"; backend (block
    # renders) is the control case that must stay at a single blank line.
    rendered = _render_variant("BOARD_HEARTBEAT.md.j2", variant, "true")
    assert "\n\n\n## Scratch ownership" not in rendered


def test_keyed_heartbeat_checklist_fits_scratch_with_room_for_notes() -> None:
    for role in _ROLES:
        size = len(_render("BOARD_HEARTBEAT.md.j2", role, "true").encode("utf-8"))
        # OpenClaw scratch limit is 262,144 UTF-8 bytes; keep most of it for agent notes.
        assert size <= 64_000, f"{role} checklist is {size} bytes"
