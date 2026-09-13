# ruff: noqa: INP001
"""Regression tests for reading agent credential values from the workspace."""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import FileSystemLoader, Undefined

from app.services.openclaw.constants import (
    BOARD_SHARED_TEMPLATE_MAP,
    DEFAULT_GATEWAY_FILES,
    LEAD_GATEWAY_FILES,
    MAIN_TEMPLATE_MAP,
)
from app.services.openclaw.gateway_rpc import OpenClawGatewayError
from app.services.openclaw.provisioning import _template_env
from app.services.openclaw.provisioning_db import (
    _get_existing_auth_token,
    _parse_agents_md_tools_section,
    _parse_tools_md,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def test_parse_tools_md_reads_bullet_backtick_assignments() -> None:
    content = """
# TOOLS.md

- `BASE_URL=http://192.168.2.64:8000`
- `AUTH_TOKEN=abc123`
- `AGENT_ID=worker-1`
"""

    values = _parse_tools_md(content)

    assert values["BASE_URL"] == "http://192.168.2.64:8000"
    assert values["AUTH_TOKEN"] == "abc123"
    assert values["AGENT_ID"] == "worker-1"


def test_retired_tools_md_is_not_synced() -> None:
    # OpenClaw 2026.9 rejects TOOLS.md in agents.files.set; writing it would
    # fail lead syncs outright and silently drop worker credentials.
    assert "TOOLS.md" not in DEFAULT_GATEWAY_FILES
    assert "TOOLS.md" not in LEAD_GATEWAY_FILES
    assert "TOOLS.md" not in MAIN_TEMPLATE_MAP
    assert "TOOLS.md" not in BOARD_SHARED_TEMPLATE_MAP


@pytest.mark.parametrize(
    "role_flags",
    [{"is_main_agent": "true"}, {"is_board_lead": "true"}, {}],
    ids=["main", "lead", "worker"],
)
def test_rendered_agents_md_carries_credentials_in_top_tools_section(
    role_flags: dict[str, str],
) -> None:
    env = _template_env()
    env.loader = FileSystemLoader(str(TEMPLATES_DIR))
    env.undefined = Undefined  # optional template variables are omitted here
    context = {
        "agent_name": "Worker-Agent-Sample",
        "agent_id": "00000000-0000-4000-8000-000000000001",
        "board_id": "00000000-0000-4000-8000-000000000002",
        "base_url": "http://192.168.2.64:8000",
        "auth_token": "sample-agent-token-000000000000000000000",
        "workspace_root": "/root/.openclaw/workspace",
        **role_flags,
    }

    rendered = env.get_template("BOARD_AGENTS.md.j2").render(**context)
    values = _parse_agents_md_tools_section(rendered)

    assert values["AUTH_TOKEN"] == context["auth_token"]
    assert values["BASE_URL"] == context["base_url"]
    assert rendered.count("\n## Tools\n") == 1
    # OpenClaw truncates oversized AGENTS.md from the middle and keeps the head.
    assert rendered.index("\n## Tools\n") < 500
    assert "TOOLS.md" not in rendered


def test_parse_agents_md_tools_section_reads_openclaw_migrated_notes() -> None:
    # Shape written by `openclaw doctor --fix` when it retires TOOLS.md:
    # the old file is merged under a subsection of `## Tools` in AGENTS.md.
    content = """
# AGENTS.md

## Tools and Markdown
- `AUTH_TOKEN=not-this-one`

## Tools

### Local notes (migrated from TOOLS.md)

- `BASE_URL=http://192.168.2.64:8000`
- `AUTH_TOKEN=migrated-token`
- `BOARD_ID=board-1`

## Every Session
- `AUTH_TOKEN=example-in-prose`
"""

    values = _parse_agents_md_tools_section(content)

    assert values["AUTH_TOKEN"] == "migrated-token"
    assert values["BASE_URL"] == "http://192.168.2.64:8000"
    assert values["BOARD_ID"] == "board-1"


def test_parse_agents_md_tools_section_ignores_file_without_tools_section() -> None:
    content = """
# AGENTS.md

## Every Session
- `AUTH_TOKEN=example-in-prose`
"""

    assert _parse_agents_md_tools_section(content) == {}


class _FakeControlPlane:
    def __init__(self, files: dict[str, str | Exception]) -> None:
        self._files = files
        self.requested: list[str] = []

    async def get_agent_file_payload(self, *, agent_id: str, name: str) -> object:
        _ = agent_id
        self.requested.append(name)
        entry = self._files.get(name)
        if isinstance(entry, Exception):
            raise entry
        if entry is None:
            raise OpenClawGatewayError(f"file not found: {name}")
        return {"file": {"name": name, "content": entry}}


@pytest.mark.asyncio
async def test_get_existing_auth_token_prefers_agents_md_tools_section() -> None:
    plane = _FakeControlPlane(
        {
            "AGENTS.md": "# AGENTS.md\n\n## Tools\n- `AUTH_TOKEN=from-agents`\n",
            "TOOLS.md": "- `AUTH_TOKEN=from-legacy-tools`\n",
        },
    )

    token = await _get_existing_auth_token(
        agent_gateway_id="agent-1",
        control_plane=plane,  # type: ignore[arg-type]
    )

    assert token == "from-agents"
    assert plane.requested == ["AGENTS.md"]


@pytest.mark.asyncio
async def test_get_existing_auth_token_falls_back_to_legacy_tools_md() -> None:
    plane = _FakeControlPlane(
        {
            "AGENTS.md": "# AGENTS.md\n\n## Every Session\n- read files\n",
            "TOOLS.md": "# TOOLS.md\n\n- `AUTH_TOKEN=from-legacy-tools`\n",
        },
    )

    token = await _get_existing_auth_token(
        agent_gateway_id="agent-1",
        control_plane=plane,  # type: ignore[arg-type]
    )

    assert token == "from-legacy-tools"
    assert plane.requested == ["AGENTS.md", "TOOLS.md"]


@pytest.mark.asyncio
async def test_get_existing_auth_token_returns_none_when_gateway_rejects_tools_md() -> None:
    # OpenClaw 2026.9+ rejects the retired file in agents.files.get.
    plane = _FakeControlPlane(
        {
            "AGENTS.md": "# AGENTS.md\n",
            "TOOLS.md": OpenClawGatewayError('unsupported file "TOOLS.md"'),
        },
    )

    token = await _get_existing_auth_token(
        agent_gateway_id="agent-1",
        control_plane=plane,  # type: ignore[arg-type]
    )

    assert token is None
