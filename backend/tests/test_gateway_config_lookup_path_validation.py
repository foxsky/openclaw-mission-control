# ruff: noqa: INP001
"""Unit tests for config lookup path pre-validation."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.gateway import _validate_config_lookup_path


def test_valid_dot_path_returned_verbatim() -> None:
    assert _validate_config_lookup_path("agents.defaults.models") == "agents.defaults.models"


def test_root_path_allowed() -> None:
    assert _validate_config_lookup_path(".") == "."


def test_bracket_quoted_keys_allowed() -> None:
    raw = 'agents.defaults.models["openai-codex/gpt-5.5"].params'
    assert _validate_config_lookup_path(raw) == raw


def test_whitespace_stripped() -> None:
    assert _validate_config_lookup_path("  agents.defaults  ") == "agents.defaults"


@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_empty_or_blank_rejected(bad: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_config_lookup_path(bad)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {"error": "invalid_path"}


def test_too_long_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_config_lookup_path("a" * 513)
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("bad", ["\x00a", "ag\x01ent", "agent\nfoo"])
def test_control_chars_rejected(bad: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_config_lookup_path(bad)
    assert exc_info.value.status_code == 400
