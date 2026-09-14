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


def _lead_manager(
    control_plane: _ControlPlaneStub,
) -> agent_provisioning.BoardAgentLifecycleManager:
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
    assert control_plane.events == [
        ("set", "AGENTS.md"),
        ("delete", "TOOLS.md"),
        ("scratch", "lead-x"),
    ]
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

    def _build_context(
        self, *, agent: Any, auth_token: str, user: Any, board: Any
    ) -> dict[str, str]:
        return {"agent_name": agent.name}


@pytest.mark.parametrize("keyed", [True, False], ids=["keyed", "legacy"])
@pytest.mark.asyncio
async def test_provision_passes_layout_to_templates_and_file_routing(
    monkeypatch: pytest.MonkeyPatch,
    keyed: bool,
) -> None:
    seen: dict[str, Any] = {}

    def _fake_render(
        context: dict[str, str], agent: Any, file_names: set[str], **kwargs: Any
    ) -> dict[str, str]:
        seen["context"] = dict(context)
        return {"AGENTS.md": "agents", "HEARTBEAT.md": "1. Check in."}

    async def _fake_set_agent_files(self: Any, **kwargs: Any) -> list[str]:
        seen["set_kwargs"] = kwargs
        return ["heartbeat_scratch.conflict"]

    monkeypatch.setattr(agent_provisioning, "_render_agent_files", _fake_render)
    monkeypatch.setattr(
        agent_provisioning.BaseAgentLifecycleManager, "_set_agent_files", _fake_set_agent_files
    )
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
    agent = SimpleNamespace(
        name="Gateway Agent", openclaw_session_id="agent:main:main", board_id=None
    )

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
