# ruff: noqa: S101
"""Template size guardrails for injected bootstrap context.

The source .j2 files contain multiple branches (main/lead/worker) but only one
is rendered per agent. We check the RENDERED output for each variant with
realistic provisioning context (matching what ``_build_context`` injects at
runtime) because the gateway injects the rendered markdown into the model's
bootstrap context.

The hard cap aligns with the OpenClaw docs at
https://docs.openclaw.ai/concepts/context which declare
``agents.defaults.bootstrapMaxChars = 20000`` as the per-file cap and
``agents.defaults.bootstrapTotalMaxChars = 150000`` as the total-across-files
cap. Files over the per-file cap get truncated by the gateway, which would
silently drop instructions. The previous ``HEARTBEAT_CONTEXT_LIMIT = 10_500``
guard was fake — nothing in the runtime enforced that number, it was just
test hygiene from a prior era — and it rendered with minimal context so it
lied about headroom. This test now uses the documented runtime contract and
realistic context matching ``_build_context``.
"""

from __future__ import annotations

from jinja2 import FileSystemLoader
from pathlib import Path

import pytest

_SKIP_LOCAL_TEMPLATE_PHILOSOPHY = pytest.mark.skip(
    reason=(
        "Stale under Phase B sync: .64 is canonical for templates. "
        "These assertions encode local's template philosophy "
        "(23k per-file cap, Ralph loop framing in SOUL.md, AGENTS.md "
        "delegation references, 'Do not assume exec is blocked' in "
        "HEARTBEAT.md) which .64's templates do not match. Unskip if "
        "template discipline is ever re-applied to the .64 line."
    )
)

# Per-file injection cap. The OpenClaw docs default is 20,000
# (``agents.defaults.bootstrapMaxChars``). Raised to 30,000 to
# accommodate the deterministic lead playbook (memory intake,
# next-action gates, structured review gates, and evidence contracts)
# which is operationally critical content referenced by HEARTBEAT.md.
# The gateway config on .60 should match:
#   agents.defaults.bootstrapMaxChars = 30000
BOOTSTRAP_PER_FILE_MAX_CHARS = 30_000

# Soft budget for HEARTBEAT specifically — prompt-cost hygiene, not a
# runtime contract. Heartbeats fire on every cron tick so a bloated
# HEARTBEAT.md burns tokens on every tick, unlike AGENTS.md which is
# read once per session start.
HEARTBEAT_SOFT_BUDGET = 12_000

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

_BOARD_RULE_DEFAULTS = {
    "board_rule_require_review_before_done": "true",
    "board_rule_require_approval_for_done": "true",
    "board_rule_comment_required_for_review": "true",
    "board_rule_block_status_changes_with_pending_approval": "true",
    "board_rule_only_lead_can_change_status": "true",
    "board_rule_max_agents": "6",
}

# Realistic provisioning context matching what
# ``_build_context`` in ``backend/app/services/openclaw/provisioning.py``
# injects at runtime. Using minimal context in the old test produced a
# fake 10,486-char headroom when the realistic render was 10,512 chars
# — over the (also-fake) 10,500 limit.
_REALISTIC_RENDER_CONTEXT = {
    "agent_name": "Worker-Agent-Sample",
    "agent_id": "00000000-0000-4000-8000-000000000001",
    "board_id": "00000000-0000-4000-8000-000000000002",
    "base_url": "http://192.168.2.64:8000",
    "auth_token": "sample-agent-token-with-realistic-length-000000000000",
    "user_timezone": "America/Sao_Paulo",
    "shared_workspace": "/shared",
    "workspace_path": "/root/.openclaw/workspace/workspace-mc-sample",
    **_BOARD_RULE_DEFAULTS,
}


def _render_template(name: str, **context: object) -> str:
    # Reuse the production Jinja environment so test renders match what
    # the gateway receives (trim_blocks, lstrip_blocks, etc.). Override
    # undefined to lenient because tests intentionally omit optional
    # template variables to exercise default branches — production uses
    # StrictUndefined but _build_context always provides all fields.
    from jinja2 import Undefined

    from app.services.openclaw.provisioning import _template_env

    env = _template_env()
    env.loader = FileSystemLoader(str(TEMPLATES_DIR))
    env.undefined = Undefined  # lenient for tests
    return env.get_template(name).render(**context)


def test_heartbeat_templates_fit_in_bootstrap_per_file_cap() -> None:
    """Each rendered heartbeat variant must stay under the gateway's
    documented per-file bootstrap injection cap (20,000 chars). Files
    over the cap get silently truncated by the gateway which would drop
    instructions. This is the real runtime contract — the old
    ``HEARTBEAT_CONTEXT_LIMIT = 10_500`` was test-only hygiene with no
    runtime enforcement.

    Rendered with realistic context (matching ``_build_context``) because
    minimal-context rendering under-reports the real size.
    """
    variants = {
        "main": {"is_main_agent": True, "is_board_lead": False, **_REALISTIC_RENDER_CONTEXT},
        "lead": {"is_main_agent": False, "is_board_lead": True, **_REALISTIC_RENDER_CONTEXT},
        "worker": {"is_main_agent": False, "is_board_lead": False, **_REALISTIC_RENDER_CONTEXT},
    }
    for variant_name, ctx in variants.items():
        rendered = _render_template("BOARD_HEARTBEAT.md.j2", **ctx)
        size = len(rendered)
        assert size <= BOOTSTRAP_PER_FILE_MAX_CHARS, (
            f"BOARD_HEARTBEAT.md.j2 ({variant_name}) renders to {size} chars "
            f"(docs-backed per-file cap {BOOTSTRAP_PER_FILE_MAX_CHARS})"
        )


def test_heartbeat_templates_stay_within_soft_budget() -> None:
    """Prompt-cost hygiene: HEARTBEAT.md is injected on every cron tick,
    so its size directly impacts per-tick token cost. This test is NOT a
    runtime contract — the gateway only enforces the 20,000-char hard
    cap. It's a soft budget to catch bloat early. If it fires, either
    shrink the template or raise the budget deliberately.
    """
    variants = {
        "main": {"is_main_agent": True, "is_board_lead": False, **_REALISTIC_RENDER_CONTEXT},
        "lead": {"is_main_agent": False, "is_board_lead": True, **_REALISTIC_RENDER_CONTEXT},
        "worker": {"is_main_agent": False, "is_board_lead": False, **_REALISTIC_RENDER_CONTEXT},
    }
    for variant_name, ctx in variants.items():
        rendered = _render_template("BOARD_HEARTBEAT.md.j2", **ctx)
        size = len(rendered)
        assert size <= HEARTBEAT_SOFT_BUDGET, (
            f"BOARD_HEARTBEAT.md.j2 ({variant_name}) renders to {size} chars "
            f"(soft budget {HEARTBEAT_SOFT_BUDGET}) — consider shrinking "
            "or raising the budget deliberately"
        )


@_SKIP_LOCAL_TEMPLATE_PHILOSOPHY
def test_agents_md_fits_in_bootstrap_per_file_cap() -> None:
    """AGENTS.md is injected at session start (not every tick) but it
    still has to fit under the gateway's per-file bootstrap cap to avoid
    silent truncation of operating instructions. Since the ACP delegation
    refactor moved playbook content into AGENTS.md, this file is now the
    biggest single contributor to the bootstrap budget.
    """
    variants = {
        "main": {"is_main_agent": True, "is_board_lead": False, **_REALISTIC_RENDER_CONTEXT},
        "lead": {
            "is_main_agent": False,
            "is_board_lead": True,
            "agent_name": "Supervisor",
            **_REALISTIC_RENDER_CONTEXT,
        },
        "worker": {
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "Worker-Agent-Sample",
            **_REALISTIC_RENDER_CONTEXT,
        },
    }
    for variant_name, ctx in variants.items():
        rendered = _render_template("BOARD_AGENTS.md.j2", **ctx)
        size = len(rendered)
        assert size <= BOOTSTRAP_PER_FILE_MAX_CHARS, (
            f"BOARD_AGENTS.md.j2 ({variant_name}) renders to {size} chars "
            f"(docs-backed per-file cap {BOOTSTRAP_PER_FILE_MAX_CHARS})"
        )


def test_lead_bootstrap_requires_fresh_exec_attempt_before_declaring_blocked() -> None:
    rendered = _render_template(
        "BOARD_BOOTSTRAP.md.j2",
        is_board_lead=True,
        base_url="http://example.test",
        auth_token="token",
        board_id="board-id",
        agent_name="Supervisor",
    )

    assert "Do not assume exec is blocked based on an earlier session." in rendered
    assert "Attempt the required command once in this session before saying you are blocked." in rendered
    assert "Only say exec is blocked after a fresh tool result in this session" in rendered


@_SKIP_LOCAL_TEMPLATE_PHILOSOPHY
def test_lead_heartbeat_requires_fresh_exec_attempt_before_declaring_blocked() -> None:
    rendered = _render_template(
        "BOARD_HEARTBEAT.md.j2",
        is_main_agent=False,
        is_board_lead=True,
        **_BOARD_RULE_DEFAULTS,
    )

    assert "Do not assume exec is blocked" in rendered
    assert "Try the command first" in rendered


def test_lead_board_playbook_includes_recovery_and_health_scan() -> None:
    """Phase B2 moved the lead health scan / route / recover / nudge
    content out of BOARD_HEARTBEAT.md.j2 and into BOARD_AGENTS.md.j2's
    ``## Lead Board Playbook`` section. The heartbeat now references
    the playbook by step number; the actual curl commands and the
    Python health-scan script live in AGENTS.md.
    """
    agents_rendered = _render_template(
        "BOARD_AGENTS.md.j2",
        is_main_agent=False,
        is_board_lead=True,
        agent_name="Supervisor",
        agent_id="lead-id",
        base_url="http://example.test",
        auth_token="token",
        board_id="board-id",
        **_BOARD_RULE_DEFAULTS,
    )

    # Health-scan endpoints must still be present somewhere the agent
    # can read them — they just live in AGENTS.md now, not HEARTBEAT.md.
    assert "/api/v1/agent/agents?board_id=$BOARD_ID" in agents_rendered, (
        "Lead Board Playbook must contain the health-scan agents endpoint"
    )
    assert "agent_status=" in agents_rendered
    assert "recover" in agents_rendered.lower()
    assert "nudge" in agents_rendered.lower()

    # The HEARTBEAT heartbeat should reference the playbook by name so
    # the model knows where to find the actual curl commands.
    heartbeat_rendered = _render_template(
        "BOARD_HEARTBEAT.md.j2",
        is_main_agent=False,
        is_board_lead=True,
        **_BOARD_RULE_DEFAULTS,
    )
    assert "Lead Board Playbook" in heartbeat_rendered, (
        "HEARTBEAT must point workers at AGENTS.md § Lead Board Playbook"
    )


def test_acp_delegation_lives_in_agents_md_not_in_soul_identity_or_heartbeat() -> None:
    """Architectural guard: per the OpenClaw agent-workspace docs
    (https://docs.openclaw.ai/concepts/agent-workspace), AGENTS.md is
    the canonical home for cross-cutting operating instructions and
    tool-use patterns. SOUL.md is persona/tone/boundaries, IDENTITY.md
    is name/vibe/emoji, and HEARTBEAT.md must stay "tiny" to avoid
    token burn. The ACP `sessions_spawn` JSON payloads therefore belong
    in AGENTS.md only — duplicating them into SOUL, IDENTITY, or
    HEARTBEAT creates drift between three sources of truth.
    """
    worker_soul = _render_template(
        "BOARD_SOUL.md.j2",
        agent_name="Programmer-Backend",
        is_board_lead=False,
    )
    # Stricter guard (Codex round-2 feedback): forbid the literal
    # ``sessions_spawn`` mechanism name, not just the JSON payload.
    # Allowing the mechanism name still leaves drift risk — if the
    # delegation tool ever changes name (e.g. from sessions_spawn to a
    # different spawn API), every template mentioning it would need to
    # be updated. AGENTS.md is the single source of truth for BOTH the
    # payload shape AND the mechanism name.
    assert "sessions_spawn" not in worker_soul, (
        "SOUL.md must not reference the `sessions_spawn` mechanism by "
        "name — delegate per AGENTS.md § Code Delegation (ACP) only"
    )
    assert '"agentId"' not in worker_soul, (
        "SOUL.md must not embed the ACP sessions_spawn JSON payload "
        "(no `\"agentId\"` field) — reference AGENTS.md instead"
    )
    assert "## ACP Delegation" not in worker_soul, (
        "SOUL.md must not have a dedicated ACP Delegation section — "
        "that belongs in AGENTS.md"
    )

    worker_identity = _render_template(
        "BOARD_IDENTITY.md.j2",
        agent_name="Programmer-Backend",
        agent_id="pb-id",
        is_board_lead=False,
        identity_role="Backend Programmer",
        identity_communication_style="direct",
        identity_emoji=":gear:",
    )
    assert "sessions_spawn" not in worker_identity, (
        "IDENTITY.md must not reference `sessions_spawn` — identity is "
        "name/vibe/emoji, not tool-use mechanics"
    )
    assert '"agentId"' not in worker_identity, (
        "IDENTITY.md is for name/vibe/emoji only — no ACP JSON payload"
    )
    assert "ACP Delegation" not in worker_identity, (
        "IDENTITY.md must not have an ACP Delegation section — that "
        "pollutes identity with operational mechanics"
    )

    worker_heartbeat = _render_template(
        "BOARD_HEARTBEAT.md.j2",
        is_main_agent=False,
        is_board_lead=False,
        **_REALISTIC_RENDER_CONTEXT,
    )
    assert "sessions_spawn" not in worker_heartbeat, (
        "HEARTBEAT.md must not reference `sessions_spawn` — heartbeat "
        "should be a tiny checklist referencing AGENTS.md for the "
        "delegation mechanism"
    )
    assert '"agentId"' not in worker_heartbeat, (
        "HEARTBEAT.md must stay small — the delegation JSON payload "
        "lives in AGENTS.md and is referenced here, not duplicated"
    )


def test_agents_md_code_delegation_references_skill() -> None:
    """The Code Delegation section must explicitly tell agents to use the
    acp-delegation skill. ACP payloads live in the skill, not inline.
    Leads and main agents must NOT have this section.
    """
    for flow in ["claude_with_skills", "review_only", "codex_then_claude_review", ""]:
        agents = _render_template(
            "BOARD_AGENTS.md.j2",
            is_main_agent=False,
            is_board_lead=False,
            agent_name="Worker",
            agent_id="w-id",
            identity_dev_acp_flow=flow,
        )
        assert "## Code Delegation (ACP)" in agents, (
            f"AGENTS.md must have Code Delegation section (flow={flow!r})"
        )
        assert "acp-delegation" in agents, (
            f"AGENTS.md must tell agents to use the acp-delegation skill (flow={flow!r})"
        )
        assert "acp-post-review" in agents, (
            f"AGENTS.md must tell agents to use the acp-post-review skill after child completion (flow={flow!r})"
        )
        assert "use" in agents.lower() and "skill" in agents.lower(), (
            f"AGENTS.md must explicitly say 'use ... skill' (flow={flow!r})"
        )
        # Inline payloads must NOT be in the template
        assert '"agentId": "claude"' not in agents, (
            f"ACP payloads must be in the skill, not inline (flow={flow!r})"
        )
        assert "The ACP prompt must include this Board API boundary verbatim" not in agents
        assert "ACP retry budget is per task" not in agents
        assert "Large/shared files: plan first" not in agents
        assert "After the Codex child returns" not in agents
        assert "After the Claude Code child returns" not in agents

    lead_agents = _render_template(
        "BOARD_AGENTS.md.j2",
        is_main_agent=False,
        is_board_lead=True,
        agent_name="Supervisor",
        agent_id="lead-id",
    )
    assert "## Code Delegation (ACP)" not in lead_agents

    main_agents = _render_template(
        "BOARD_AGENTS.md.j2",
        is_main_agent=True,
        is_board_lead=False,
        agent_name="Main Agent",
        agent_id="main-id",
    )
    assert "## Code Delegation (ACP)" not in main_agents


@_SKIP_LOCAL_TEMPLATE_PHILOSOPHY
def test_soul_is_values_only_no_operational_steps() -> None:
    """Worker SOUL.md must contain only identity/values content (Ralph
    loop framing, Core Principles, Boundaries, Continuity). Operational
    steps (10-step loop, QA handling, delegation instructions) are in
    AGENTS.md. Same principle as the Supervisor SOUL migration.
    """
    worker_soul = _render_template(
        "BOARD_SOUL.md.j2",
        agent_name="Architect",
        is_board_lead=False,
    )
    assert "Ralph loop" in worker_soul, "SOUL must keep Ralph loop identity framing"
    assert "Core Principles" in worker_soul, "SOUL must keep Core Principles"
    assert "AGENTS.md" in worker_soul, "SOUL must reference AGENTS.md"
    assert "Delegate per" not in worker_soul, "No delegation steps in SOUL"
    assert "nudge" not in worker_soul.lower(), "No nudge API in SOUL"
    assert "Responding to QA" not in worker_soul, "No QA handling in SOUL"


@_SKIP_LOCAL_TEMPLATE_PHILOSOPHY
def test_soul_skips_oversized_directory_persona_preamble() -> None:
    """Souls Directory can return huge persona preambles (75+ lines of
    generic philosophy). SOUL.md must cap the injection to avoid wasting
    bootstrap tokens on content that has no operational value. Preambles
    over 2000 chars are skipped.
    """
    large_persona = "# Role Persona\n\n" + "Philosophy line.\n" * 200  # ~3400 chars
    soul_with_large = _render_template(
        "BOARD_SOUL.md.j2",
        agent_name="DevOps",
        is_board_lead=False,
        is_main_agent=False,
        directory_role_soul_markdown=large_persona,
    )
    assert "Philosophy line" not in soul_with_large, (
        "SOUL.md must skip Souls Directory persona over 2000 chars"
    )
    assert "Ralph Loop" in soul_with_large, (
        "Ralph Loop section must still render even when persona is skipped"
    )
    assert "skipped" in soul_with_large.lower(), (
        "SOUL.md must include an operator-visible warning when persona is "
        "skipped due to size — silent drops are hard to debug"
    )

    small_persona = "# DevOps Engineer\n\nShort useful guidance."  # ~45 chars
    soul_with_small = _render_template(
        "BOARD_SOUL.md.j2",
        agent_name="DevOps",
        is_board_lead=False,
        is_main_agent=False,
        directory_role_soul_markdown=small_persona,
    )
    assert "Short useful guidance" in soul_with_small, (
        "Small Souls Directory personas (<= 2000 chars) must still render"
    )


def test_qa_agents_get_validation_specific_checklist_not_developer_checklist() -> None:
    """QA agents (QA-Unit, QA-E2E) should get a validation-focused
    VALIDATING checklist, not the developer build/deploy/lint checklist.
    """
    qa_agents = _render_template(
        "BOARD_AGENTS.md.j2",
        **{
            **_REALISTIC_RENDER_CONTEXT,
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "QA-Unit",
            "agent_id": "qa-id",
            "identity_role": "Quality Assurance and Reverse-Mode Validator",
            "identity_validation_flow": "qa_validation",
        },
    )
    assert "QA VALIDATION" in qa_agents or "validation" in qa_agents.lower(), (
        "QA VALIDATING section must exist"
    )
    assert "npm run build" not in qa_agents, (
        "QA agents must NOT get developer build checks (npm run build)"
    )

    # Non-QA worker keeps developer checklist
    dev_agents = _render_template(
        "BOARD_AGENTS.md.j2",
        **{
            **_REALISTIC_RENDER_CONTEXT,
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "Programmer-Frontend",
            "agent_id": "pf-id",
            "identity_role": "Frontend Developer",
        },
    )
    assert "typecheck" in dev_agents.lower() or "build" in dev_agents.lower(), (
        "Developer workers must include build/typecheck validation"
    )


def test_hard_rules_in_agents_not_identity() -> None:
    """HARD RULES moved from IDENTITY.md to AGENTS.md per workspace
    architecture docs (IDENTITY = name/vibe/emoji, AGENTS = operating
    instructions). Verify IDENTITY is clean and AGENTS has the rules.
    """
    # IDENTITY must NOT have hard rules anymore
    worker_identity = _render_template(
        "BOARD_IDENTITY.md.j2",
        agent_name="QA-Unit",
        agent_id="qa-id",
        is_board_lead=False,
        identity_role="Quality Assurance",
        identity_communication_style="methodical",
        identity_emoji=":gear:",
    )
    assert "HARD RULES" not in worker_identity, (
        "IDENTITY.md must not have HARD RULES — they belong in AGENTS.md"
    )
    assert "MANDATORY" not in worker_identity, (
        "IDENTITY.md must not have nudge mandate — it belongs in AGENTS.md"
    )

    # AGENTS.md must have QA-specific hard rules
    qa_agents = _render_template(
        "BOARD_AGENTS.md.j2",
        **{
            **_REALISTIC_RENDER_CONTEXT,
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "QA-Unit",
            "agent_id": "qa-id",
            "identity_validation_flow": "qa_validation",
        },
    )
    assert "validation" in qa_agents.lower(), (
        "QA workers must have validation instructions"
    )
    assert "Fabricating evidence" in qa_agents, (
        "Fabrication rule must apply to ALL agents"
    )
    assert "nudge" in qa_agents.lower() or "board memory" in qa_agents.lower() or "@lead" in qa_agents, (
        "Supervisor notification mechanism must be in AGENTS.md for workers"
    )

    # Developer AGENTS.md must have developer hard rules
    dev_agents = _render_template(
        "BOARD_AGENTS.md.j2",
        **{
            **_REALISTIC_RENDER_CONTEXT,
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "PB",
            "agent_id": "pb-id",
        },
    )
    assert "fabricating evidence" in dev_agents.lower(), (
        "Developer workers must have fabrication rule"
    )


def test_qa_discriminator_does_not_false_positive_on_name_containing_qa() -> None:
    """Regression: an agent named 'QA-Security' with role 'Security
    Auditor' must NOT get the QA validation checklist. The previous
    heuristic (``'QA' in agent_name``) would false-positive on this.
    The stable ``identity_validation_flow`` flag prevents that.
    """
    qa_security = _render_template(
        "BOARD_AGENTS.md.j2",
        **{
            **_REALISTIC_RENDER_CONTEXT,
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "QA-Security",
            "agent_id": "sec-id",
            "identity_role": "Security Auditor",
            # No identity_validation_flow → should get developer checklist
        },
    )
    assert "QA VALIDATION" not in qa_security, (
        "QA-Security with Security Auditor role must NOT get QA validation section — "
        "the discriminator must key on identity_validation_flow, not agent_name"
    )
    assert "implement" in qa_security.lower() or "delegate" in qa_security.lower(), (
        "QA-Security should get the default implementation workflow"
    )


def test_phase1_review_loop_guardrails_render_for_frontend_architect_and_lead() -> None:
    frontend_agents = _render_template(
        "BOARD_AGENTS.md.j2",
        **{
            **_REALISTIC_RENDER_CONTEXT,
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "Programmer-Frontend",
            "agent_id": "pf-id",
            "identity_role": "Frontend Developer",
        },
    )
    assert "one or more active blockers" in frontend_agents
    assert "Do not replace requested live/browser evidence with grep, build, or source-only proof." in frontend_agents
    assert "If multiple reviewers identified blockers, address each one explicitly." in frontend_agents
    assert "Do not reopen a resolved product decision unless `@lead` or Architect changes it." in frontend_agents
    assert "Treat `review_packet_type` and `validation_target*` as authoritative unless `@lead` changes them." in frontend_agents
    assert "do not claim deploy blockage without outage or runtime/source mismatch evidence." in frontend_agents
    assert "Active blocker cleared:" in frontend_agents

    architect_agents = _render_template(
        "BOARD_AGENTS.md.j2",
        **{
            **_REALISTIC_RENDER_CONTEXT,
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "Architect",
            "agent_id": "arch-id",
            "identity_role": "System Architect and Code Reviewer",
            "identity_dev_acp_flow": "review_only",
        },
    )
    assert "Review against the declared `review_packet_type` and `validation_target*` fields." in architect_agents
    assert "and no newer commits or evidence reopen the issue" in architect_agents

    lead_agents = _render_template(
        "BOARD_AGENTS.md.j2",
        **{
            **_REALISTIC_RENDER_CONTEXT,
            "is_main_agent": False,
            "is_board_lead": True,
            "agent_name": "Supervisor",
            "agent_id": "lead-id",
        },
    )
    assert "After 2 failed resubmits on one blocker" in lead_agents
    assert "Post one blocker summary" in lead_agents
    assert "Keep failed review in `rework`" in lead_agents
    assert "worker escalation still starts at `3+ rejections`." in lead_agents
    assert "set `review_packet_type`, known `validation_target*`, and `operator_decision_required` when needed" in lead_agents
    assert "route around operator-gated work" in lead_agents


def test_architect_templates_are_review_only_without_worker_leakage() -> None:
    ctx = {
        **_REALISTIC_RENDER_CONTEXT,
        "is_main_agent": False,
        "is_board_lead": False,
        "agent_name": "Architect",
        "agent_id": "arch-id",
        "identity_role": "System Architect and Code Reviewer",
        "identity_dev_acp_flow": "review_only",
    }

    agents = _render_template("BOARD_AGENTS.md.j2", **ctx)
    assert "Work ONLY in review mode" in agents
    assert "ARCHITECT RECHECK for $TASK_ID" in agents
    assert "frontend browser evidence packet" in agents
    assert "backend runtime evidence packet" in agents
    assert "Review gate applies" in agents
    assert "planned_child_task_ids" in agents
    assert "no_child_tasks_required" in agents
    assert "Fix ALL bugs in one pass" not in agents
    assert "Deploy target comes from the **task description**" not in agents
    assert "**Deploy verification**" not in agents
    assert "**Browser validation REQUIRED for frontend/UI tasks** before moving to review." not in agents
    assert "Worker gate applies" not in agents
    assert "When you need to delegate coding work" not in agents
    assert "begin the normal role workflow" not in agents
    assert "Review-only agents do not claim `rework`" in agents
    assert "When a specialist claims an eligible `rework` task" not in agents

    heartbeat = _render_template("BOARD_HEARTBEAT.md.j2", **ctx)
    assert 'order = ["review", "inbox"]' in heartbeat
    assert 'order = ["in_progress"' not in heartbeat
    assert "## Inbox Pickup Gate" not in heartbeat
    assert "## Rework Pickup Gate" not in heartbeat
    assert "move it to `in_progress`" not in heartbeat
    assert "Do not continue `in_progress`, claim `rework`" in heartbeat


def test_qa_unit_templates_are_validation_only_without_worker_leakage() -> None:
    ctx = {
        **_REALISTIC_RENDER_CONTEXT,
        "is_main_agent": False,
        "is_board_lead": False,
        "agent_name": "QA-Unit",
        "agent_id": "qa-id",
        "identity_role": "Quality Assurance and Reverse-Mode Validator",
        "identity_validation_flow": "qa_validation",
    }

    agents = _render_template("BOARD_AGENTS.md.j2", **ctx)
    assert "Work ONLY in validation mode" in agents
    assert "Do not move a failed task from `review` to `rework`" in agents
    assert "Suggested routing: lead move to rework" in agents
    assert "Commit/source parity" in agents
    assert "AC-to-check mapping" in agents
    assert "Changed-code coverage" in agents
    assert "backend runtime evidence packet" in agents
    assert "QA gate applies" in agents
    assert "Worker gate applies" not in agents
    assert "When you finish a slice" not in agents
    assert "**Deploy verification**" not in agents
    assert "**Browser validation REQUIRED for frontend/UI tasks** before moving to review." not in agents
    assert "When a specialist claims an eligible `rework` task" not in agents

    heartbeat = _render_template("BOARD_HEARTBEAT.md.j2", **ctx)
    assert 'order = ["review", "inbox"]' in heartbeat
    assert 'order = ["in_progress"' not in heartbeat
    assert "Do not move `review` to `rework`" in heartbeat
    assert "Suggested routing: lead move to rework" in heartbeat
    assert "move it to `in_progress`" not in heartbeat
    assert "## Inbox Pickup Gate" not in heartbeat
    assert "## Rework Pickup Gate" not in heartbeat


def test_devops_templates_have_dedicated_deploy_evidence_lane() -> None:
    ctx = {
        **_REALISTIC_RENDER_CONTEXT,
        "is_main_agent": False,
        "is_board_lead": False,
        "agent_name": "DevOps",
        "agent_id": "devops-id",
        "identity_role": "DevOps Engineer",
        "identity_dev_acp_flow": "codex_with_optional_claude_review",
    }

    agents = _render_template("BOARD_AGENTS.md.j2", **ctx)
    assert "DevOps deploy evidence packet" in agents
    assert "Classify the task before acting" in agents
    assert "source host/path" in agents
    assert "acp-delegation" in agents
    assert "acp-post-review" in agents
    assert "approved deploy script" not in agents
    assert "Artifact parity" not in agents
    assert "Service/process state" not in agents
    assert "Risk/rollback" not in agents
    assert "If the selected task was already in `review`, run **deploy/infra validation only**" in agents
    assert "DEVOPS DIAGNOSIS for $TASK_ID rejection" in agents
    assert "Deploy verification packet REQUIRED" in agents
    assert "Browser validation REQUIRED for frontend/UI tasks" not in agents
    assert "ALL acceptance criteria PASS with DevOps deploy evidence packet posted" in agents

    heartbeat = _render_template("BOARD_HEARTBEAT.md.j2", **ctx)
    assert 'order = ["in_progress", "rework", "inbox", "review"]' in heartbeat
    assert "Work this one DevOps task end-to-end" in heartbeat
    assert "If the selected task is already in review, validate deployed" in heartbeat
    assert "never edit production" in heartbeat
    assert "rollback command/path" in heartbeat
    assert "Classify rework before fixing" in heartbeat


def test_supervisor_template_enforces_review_gates_and_lead_routing() -> None:
    ctx = {
        **_REALISTIC_RENDER_CONTEXT,
        "is_main_agent": False,
        "is_board_lead": True,
        "agent_name": "Supervisor",
        "agent_id": "lead-id",
        "identity_role": "Board Lead",
    }

    agents = _render_template("BOARD_AGENTS.md.j2", **ctx)
    assert "Step 1 — Board Memory Intake" in agents
    assert "/memory?limit=50" in agents
    assert "MEMORY_INTAKE_CLEAR" in agents
    assert "MEMORY_INTAKE_CREATE_REQUIRED" in agents
    assert "MEMORY_INTAKE_FAILED" in agents
    assert "Lead Next Action Gate" in agents
    assert "/lead/next-action" in agents
    assert "LEAD_NEXT_ACTION_REQUIRED" in agents
    assert "/review-readiness" in agents
    assert "/review-events" in agents
    assert "Structured review verdicts are authoritative" in agents
    assert "artifact_issues" in agents
    assert "`review_only` Architect PASS" in agents
    assert "planned_child_task_ids" in agents
    assert "no_child_tasks_required:true" in agents
    assert "item.get('content')" in agents
    assert "item.get('id')" in agents
    assert "for item in memory_items:" in agents
    assert "item.get('title')" not in agents
    assert "source_memory_id" in agents
    assert "marketing_site_review" in agents
    assert "no existing task references it" in agents
    assert "if s not in ('in_progress','review','rework','inbox')" in agents
    assert "('Architect','ARCHITECT')" in agents
    assert '"assigned_agent_id":"ARCHITECT_ID","status":"review"' in agents
    assert "Required Review Gates before approval/done" in agents
    assert "PF→frontend browser evidence packet" in agents
    assert "PB→backend runtime evidence packet" in agents
    assert "DevOps→deploy evidence packet" in agents
    assert "QA-Unit PASS required" in agents
    assert "QA-E2E PASS required" in agents
    assert "Follow the reviewer `Suggested routing`" in agents
    assert '"status": "rework", "assigned_agent_id": "DEV_AGENT_UUID"' in agents
    assert '"lead_reasoning": "Required review gates passed"' in agents
    assert '"qa_evidence": "SUMMARY"' not in agents
    assert '"status":"in_progress"` + nudge: `"DECOMPOSE' not in agents

    heartbeat = _render_template("BOARD_HEARTBEAT.md.j2", **ctx)
    assert "Memory Intake Gate" in heartbeat
    assert "Lead Next Action Gate" in heartbeat
    assert "Do not manually scan the task list before this gate" in heartbeat
    assert "/lead/next-action is authoritative for the next heartbeat action" in heartbeat
    assert "/tmp/mc-board-tasks" not in heartbeat
    assert "Before health scan" in heartbeat
    assert "HEARTBEAT_OK is forbidden" in heartbeat
    assert "zero unlinked actionable operator memories" in heartbeat
    assert "review routing and gate enforcement" in heartbeat
    assert "one closest-to-done task was advanced" in heartbeat


def test_supervisor_heartbeat_has_failure_and_drift_guardrails() -> None:
    ctx = {
        **_REALISTIC_RENDER_CONTEXT,
        "is_main_agent": False,
        "is_board_lead": True,
        "agent_name": "Supervisor",
        "agent_id": "lead-id",
        "identity_role": "Board Lead",
    }

    heartbeat = _render_template("BOARD_HEARTBEAT.md.j2", **ctx)
    assert "4xx/5xx/network" in heartbeat
    assert "401/403/404/429" in heartbeat
    assert "HEARTBEAT_FAILED" in heartbeat
    assert "Do not assume exec is blocked" in heartbeat

    agents = _render_template("BOARD_AGENTS.md.j2", **ctx)
    assert "mktemp" in agents
    assert "LEAD_TASKS_JSON" in agents
    assert "Closest-to-done order" in agents
    assert "approved review tasks" in agents
    assert "assigned rework" in agents
    assert "refresh OpenAPI per `TOOLS.md`" in agents
    assert "newer than the latest worker packet" in agents
    assert "newer than the latest blocking review verdict" in agents


def test_agents_md_variants_fit_current_local_bootstrap_cap() -> None:
    variants = {
        "main": {"is_main_agent": True, "is_board_lead": False, **_REALISTIC_RENDER_CONTEXT},
        "lead": {
            "is_main_agent": False,
            "is_board_lead": True,
            "agent_name": "Supervisor",
            "identity_role": "Board Lead",
            **_REALISTIC_RENDER_CONTEXT,
        },
        "worker": {
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "Programmer-Frontend",
            "identity_role": "Frontend Developer",
            **_REALISTIC_RENDER_CONTEXT,
        },
        "architect": {
            "is_main_agent": False,
            "is_board_lead": False,
            "agent_name": "Architect",
            "identity_role": "System Architect and Code Reviewer",
            "identity_dev_acp_flow": "review_only",
            **_REALISTIC_RENDER_CONTEXT,
        },
    }
    for variant_name, ctx in variants.items():
        rendered = _render_template("BOARD_AGENTS.md.j2", **ctx)
        size = len(rendered)
        assert size <= BOOTSTRAP_PER_FILE_MAX_CHARS, (
            f"BOARD_AGENTS.md.j2 ({variant_name}) renders to {size} chars "
            f"(local per-file cap {BOOTSTRAP_PER_FILE_MAX_CHARS})"
        )


@_SKIP_LOCAL_TEMPLATE_PHILOSOPHY
def test_soul_and_heartbeat_reference_agents_md_for_delegation() -> None:
    """SOUL.md's Ralph loop and HEARTBEAT.md's IMPLEMENTING state must
    reference AGENTS.md as the source of delegation instructions,
    rather than embedding the JSON payloads themselves. This keeps
    the three files aligned on a single source of truth.
    """
    worker_soul = _render_template(
        "BOARD_SOUL.md.j2",
        agent_name="Programmer-Backend",
        is_board_lead=False,
    )
    assert "AGENTS.md" in worker_soul, (
        "SOUL.md must reference AGENTS.md for the execution loop"
    )

    worker_heartbeat = _render_template(
        "BOARD_HEARTBEAT.md.j2",
        is_main_agent=False,
        is_board_lead=False,
        **_BOARD_RULE_DEFAULTS,
    )
    assert "AGENTS.md" in worker_heartbeat and "Code Delegation" in worker_heartbeat, (
        "HEARTBEAT.md IMPLEMENTING state must point at AGENTS.md § Code Delegation"
    )
