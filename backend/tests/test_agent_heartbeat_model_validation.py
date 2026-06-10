# ruff: noqa: S101
"""Tests for heartbeat_config.model retired-provider validation.

OpenClaw 2026.6.5 renamed the gateway provider ``openai-codex`` -> ``openai``;
the old provider key no longer exists on the gateway, so a heartbeat model ref
like ``openai-codex/gpt-5.5`` written through the agents API would be pushed
gateway-ward by the next heartbeat sync and fail closed (silent dead heartbeat).
The schema must reject such refs at the write path (FastAPI maps the
ValidationError to HTTP 422 on the PATCH/POST body).
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

import app.services.openclaw.provisioning as agent_provisioning
from app.schemas.agents import AgentCreate, AgentUpdate


def test_agent_update_rejects_retired_provider_model() -> None:
    with pytest.raises(ValidationError) as excinfo:
        AgentUpdate(heartbeat_config={"every": "5m", "model": "openai-codex/gpt-5.5"})
    message = str(excinfo.value)
    assert "openai-codex" in message
    assert "openai/" in message  # error names the replacement provider


def test_agent_create_rejects_retired_provider_model() -> None:
    with pytest.raises(ValidationError) as excinfo:
        AgentCreate(
            name="Worker Agent",
            heartbeat_config={"every": "10m", "model": "openai-codex/gpt-5.4"},
        )
    assert "openai-codex" in str(excinfo.value)


def test_agent_update_accepts_current_provider_models() -> None:
    update = AgentUpdate(heartbeat_config={"every": "5m", "model": "openai/gpt-5.5"})
    assert update.heartbeat_config == {"every": "5m", "model": "openai/gpt-5.5"}

    update = AgentUpdate(heartbeat_config={"model": "ollama/qwen3.5:cloud"})
    assert update.heartbeat_config == {"model": "ollama/qwen3.5:cloud"}


def test_agent_update_accepts_config_without_model_key() -> None:
    update = AgentUpdate(heartbeat_config={"every": "10m", "target": "last"})
    assert update.heartbeat_config == {"every": "10m", "target": "last"}


def test_agent_update_accepts_none_heartbeat_config() -> None:
    update = AgentUpdate(heartbeat_config=None)
    assert update.heartbeat_config is None


def test_agent_update_ignores_non_string_model_value() -> None:
    # Non-string model values are not retired-provider refs; other layers
    # (gateway schema) own their validation.
    update = AgentUpdate(heartbeat_config={"model": 42})
    assert update.heartbeat_config == {"model": 42}


def test_sync_warns_on_heartbeat_model_with_unconfigured_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    new_list = [
        {
            "id": "lead-x",
            "workspace": "/w",
            "heartbeat": {"every": "5m", "model": "openai-codex/gpt-5.5"},
        },
        {
            "id": "mc-y",
            "workspace": "/w",
            "heartbeat": {"every": "10m", "model": "ollama/qwen3.5:cloud"},
        },
    ]
    config_data = {"models": {"providers": {"openai": {}, "ollama": {}}}}

    with caplog.at_level(logging.WARNING):
        agent_provisioning._warn_unconfigured_heartbeat_model_providers(new_list, config_data)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "lead-x" in message
    assert "openai-codex/gpt-5.5" in message


def test_sync_no_warning_when_all_providers_configured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    new_list = [
        {
            "id": "lead-x",
            "workspace": "/w",
            "heartbeat": {"every": "5m", "model": "openai/gpt-5.5"},
        },
    ]
    config_data = {"models": {"providers": {"openai": {}, "ollama": {}}}}

    with caplog.at_level(logging.WARNING):
        agent_provisioning._warn_unconfigured_heartbeat_model_providers(new_list, config_data)

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_sync_no_warning_for_bare_model_names_or_missing_sections(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Bare model names (no provider/ prefix) may be gateway aliases; the
    # warning only fires on unambiguous provider/model refs. Missing or
    # malformed models.providers sections must not raise.
    new_list = [
        {"id": "mc-y", "workspace": "/w", "heartbeat": {"every": "10m", "model": "gpt"}},
        {"id": "mc-z", "workspace": "/w", "heartbeat": {"every": "10m"}},
        "not-a-dict",
    ]
    with caplog.at_level(logging.WARNING):
        agent_provisioning._warn_unconfigured_heartbeat_model_providers(new_list, {})
        agent_provisioning._warn_unconfigured_heartbeat_model_providers(
            new_list, {"models": {"providers": {"openai": {}}}}
        )

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
