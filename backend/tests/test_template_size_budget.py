# ruff: noqa: S101
"""Template size guardrails for injected heartbeat context.

The source .j2 file contains multiple branches (main/lead/worker) but only one
is rendered per agent.  We check the RENDERED output for each variant, not the
raw source, because the gateway injects the rendered markdown into context.
"""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader
from pathlib import Path

HEARTBEAT_CONTEXT_LIMIT = 20_000
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

_BOARD_RULE_DEFAULTS = {
    "board_rule_require_review_before_done": "true",
    "board_rule_require_approval_for_done": "true",
    "board_rule_comment_required_for_review": "true",
    "board_rule_block_status_changes_with_pending_approval": "true",
    "board_rule_only_lead_can_change_status": "true",
    "board_rule_max_agents": "6",
}


def test_heartbeat_templates_fit_in_injected_context_limit() -> None:
    """Each rendered heartbeat variant must stay under gateway injected-context truncation limit."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("BOARD_HEARTBEAT.md.j2")

    variants = {
        "main": {"is_main_agent": True, "is_board_lead": False},
        "lead": {"is_main_agent": False, "is_board_lead": True, **_BOARD_RULE_DEFAULTS},
        "worker": {"is_main_agent": False, "is_board_lead": False, **_BOARD_RULE_DEFAULTS},
    }
    for variant_name, ctx in variants.items():
        rendered = template.render(**ctx)
        size = len(rendered)
        assert size <= HEARTBEAT_CONTEXT_LIMIT, (
            f"BOARD_HEARTBEAT.md.j2 ({variant_name}) renders to {size} chars "
            f"(limit {HEARTBEAT_CONTEXT_LIMIT})"
        )
