# ruff: noqa: INP001
"""Gateway config patches must match the OpenClaw config layout of the target gateway.

OpenClaw 2026.8.1 moved ``agents.list`` to keyed ``agents.entries``, renamed
``channels.defaults.heartbeat`` to ``heartbeatVisibility`` and retired
``agents.defaults.compaction.truncateAfterCompaction``. A gateway rejects the other
layout's keys (``INVALID_REQUEST invalid config: ... Unrecognized key``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import app.services.openclaw.provisioning as agent_provisioning

_VISIBILITY = {"showOk": False, "showAlerts": True, "useIndicator": True}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"config": {"agents": {"entries": {}}}}, True),
        ({"config": {"agents": {"list": []}}}, False),
        ({"config": {"channels": {"defaults": {"heartbeat": {}}}}}, False),
        (
            {"config": {"agents": {"defaults": {"compaction": {"truncateAfterCompaction": True}}}}},
            False,
        ),
        ({"config": {"meta": {"lastTouchedVersion": "2026.7.1"}}}, False),
        ({"config": {"meta": {"lastTouchedVersion": "2026.9.4"}}}, True),
        ({"config": {}}, True),
    ],
    ids=[
        "keyed-entries",
        "legacy-list",
        "legacy-channel-heartbeat",
        "legacy-compaction",
        "old-version-no-roster",
        "new-version-no-roster",
        "empty",
    ],
)
def test_uses_keyed_agent_entries(payload: dict[str, Any], expected: bool) -> None:
    assert agent_provisioning._uses_keyed_agent_entries(payload, payload["config"]) is expected


def _canonical_config(entries: dict[str, Any]) -> dict[str, Any]:
    return {
        "hash": "h-canonical",
        "config": {
            "meta": {"lastTouchedVersion": "2026.9.4"},
            "agents": {
                # Already carries MC's runtime guardrails, so only agent entries or
                # channel visibility can make a patch necessary.
                "defaults": {
                    "compaction": {"maxActiveTranscriptBytes": "20mb"},
                    "subagents": {
                        "allowAgents": ["claude", "codex"],
                        "requireAgentId": True,
                        "runTimeoutSeconds": 3600,
                        "archiveAfterMinutes": 120,
                    },
                },
                "entries": entries,
            },
            "acp": {"dispatch": {"enabled": False}},
            "channels": {"defaults": {"heartbeatVisibility": dict(_VISIBILITY)}},
            "tools": {"exec": {"host": "gateway"}},
        },
    }


def _control_plane_with(
    monkeypatch: pytest.MonkeyPatch,
    config_payload: dict[str, Any],
) -> tuple[agent_provisioning.OpenClawGatewayControlPlane, list[tuple[str, Any]]]:
    calls: list[tuple[str, Any]] = []

    async def _fake_openclaw_call(
        method: str,
        params: dict[str, Any] | None = None,
        config: object = None,
    ) -> object:
        _ = config
        calls.append((method, params))
        if method == "config.get":
            return config_payload
        if method == "config.patch":
            return {"ok": True}
        raise AssertionError(f"Unexpected method: {method}")

    monkeypatch.setattr(agent_provisioning, "openclaw_call", _fake_openclaw_call)
    control_plane = agent_provisioning.OpenClawGatewayControlPlane(
        agent_provisioning.GatewayClientConfig(url="ws://gateway.example/ws", token=None),
    )
    return control_plane, calls


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_patches_only_changed_keyed_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config(
            {
                "tutor": {"workspace": "/w/tutor", "heartbeat": {"every": "30m"}},
                "mc-agent-x": {
                    "workspace": "/w/agent-x",
                    "heartbeat": {"every": "10m", "model": "ollama/qwen3.5:cloud"},
                },
            },
        ),
    )

    await control_plane.patch_agent_heartbeats([("mc-agent-x", "/w/agent-x", {"every": "20m"})])

    assert [method for method, _ in calls] == ["config.get", "config.patch"]
    params = calls[1][1]
    assert params["baseHash"] == "h-canonical"
    patch = json.loads(params["raw"])
    assert patch["agents"]["entries"] == {
        "mc-agent-x": {
            "workspace": "/w/agent-x",
            "heartbeat": {"every": "20m", "model": "ollama/qwen3.5:cloud"},
        },
    }
    assert "list" not in patch["agents"]
    assert "truncateAfterCompaction" not in patch["agents"].get("defaults", {}).get(
        "compaction", {}
    )
    assert "heartbeat" not in patch.get("channels", {}).get("defaults", {})


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_adds_new_lead_as_keyed_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id = "lead-00000000-0000-4000-8000-000000000009"
    control_plane, calls = _control_plane_with(monkeypatch, _canonical_config({}))

    await control_plane.patch_agent_heartbeats([(lead_id, "/w/lead", {"every": "10m"})])

    patch = json.loads(calls[1][1]["raw"])
    assert patch["agents"]["entries"] == {
        lead_id: {
            "workspace": "/w/lead",
            "heartbeat": {"every": "10m"},
            "tools": {"alsoAllow": ["message"]},
            "subagents": {"delegationMode": "prefer"},
        },
    }


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_fills_canonical_heartbeat_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _canonical_config({"mc-agent-x": {"workspace": "/w/x", "heartbeat": {"every": "1m"}}})
    del payload["config"]["channels"]
    control_plane, calls = _control_plane_with(monkeypatch, payload)

    await control_plane.patch_agent_heartbeats([("mc-agent-x", "/w/x", {"every": "1m"})])

    patch = json.loads(calls[1][1]["raw"])
    assert patch["channels"] == {"defaults": {"heartbeatVisibility": _VISIBILITY}}
    assert "agents" not in patch or "entries" not in patch["agents"]


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_skips_patch_when_keyed_entry_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config({"mc-agent-x": {"workspace": "/w/x", "heartbeat": {"every": "10m"}}}),
    )

    await control_plane.patch_agent_heartbeats([("mc-agent-x", "/w/x", {"every": "10m"})])

    assert [method for method, _ in calls] == ["config.get"]


# MC's DEFAULT_HEARTBEAT_CONFIG and stored agent overrides carry these; the strict
# 2026.8+ heartbeat schema rejects them (`Unrecognized keys: "includeReasoning",
# "skipWhenBusy"`), which failed every wake after the layout fix.
_RETIRED_HEARTBEAT_FIELDS = {"includeReasoning": False, "skipWhenBusy": True}


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_drops_retired_heartbeat_keys_on_keyed_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config({"mc-agent-x": {"workspace": "/w/x", "heartbeat": {"every": "10m"}}}),
    )

    await control_plane.patch_agent_heartbeats(
        [("mc-agent-x", "/w/x", {"every": "20m", "target": "last", **_RETIRED_HEARTBEAT_FIELDS})],
    )

    patch = json.loads(calls[1][1]["raw"])
    assert patch["agents"]["entries"]["mc-agent-x"]["heartbeat"] == {
        "every": "20m",
        "target": "last",
    }


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_ignores_retired_keys_when_comparing_keyed_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config({"mc-agent-x": {"workspace": "/w/x", "heartbeat": {"every": "10m"}}}),
    )

    await control_plane.patch_agent_heartbeats(
        [("mc-agent-x", "/w/x", {"every": "10m", **_RETIRED_HEARTBEAT_FIELDS})],
    )

    assert [method for method, _ in calls] == ["config.get"]


@pytest.mark.asyncio
async def test_patch_agent_heartbeats_keeps_heartbeat_keys_on_legacy_layout(
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
        [("mc-agent-x", "/w/x", {"every": "10m", **_RETIRED_HEARTBEAT_FIELDS})],
    )

    patch = json.loads(calls[1][1]["raw"])
    (entry,) = patch["agents"]["list"]
    assert entry["heartbeat"] == {"every": "10m", **_RETIRED_HEARTBEAT_FIELDS}


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
    job = {
        "id": "job-1",
        "declarationKey": "heartbeat:mc-agent-x",
        "payload": {"kind": "heartbeat"},
    }

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
@pytest.mark.parametrize("every", ["10m", "0m"], ids=["enabled", "disabled"])
async def test_patch_agent_heartbeats_deletes_heartbeat_md_prompt_on_keyed_layout(
    monkeypatch: pytest.MonkeyPatch,
    every: str,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config(
            {
                "mc-agent-x": {
                    "workspace": "/w/x",
                    "heartbeat": {"every": every, "prompt": _LEGACY_PROMPT},
                }
            },
        ),
    )

    await control_plane.patch_agent_heartbeats([("mc-agent-x", "/w/x", {"every": every})])

    patch = json.loads(calls[1][1]["raw"])
    assert patch["agents"]["entries"]["mc-agent-x"]["heartbeat"] == {"every": every, "prompt": None}


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
async def test_patch_agent_heartbeats_replaces_heartbeat_md_prompt_with_custom_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane, calls = _control_plane_with(
        monkeypatch,
        _canonical_config(
            {
                "mc-agent-x": {
                    "workspace": "/w/x",
                    "heartbeat": {"every": "10m", "prompt": _LEGACY_PROMPT},
                }
            },
        ),
    )

    await control_plane.patch_agent_heartbeats(
        [("mc-agent-x", "/w/x", {"every": "10m", "prompt": "Check the deploy queue."})],
    )

    patch = json.loads(calls[1][1]["raw"])
    assert patch["agents"]["entries"]["mc-agent-x"]["heartbeat"] == {
        "every": "10m",
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
            "mc-disabled": {
                "workspace": "/w/d",
                "heartbeat": {"every": "0m", "prompt": _LEGACY_PROMPT},
            },
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
