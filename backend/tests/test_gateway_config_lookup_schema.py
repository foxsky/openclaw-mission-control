# ruff: noqa: INP001
"""Schema round-trip tests for config schema lookup response."""

from __future__ import annotations

from uuid import uuid4

from app.schemas.gateway_api import (
    ConfigSchemaLookupChild,
    ConfigSchemaLookupResponse,
)


def test_response_accepts_gateway_camel_case_aliases() -> None:
    gateway_id = uuid4()
    payload = {
        "gateway_id": gateway_id,
        "path": "agents.defaults.models",
        "schema": {"type": "object"},
        "reloadKind": "restart",
        "hint": "Restart required.",
        "hintPath": "agents.defaults.models",
        "children": [
            {"path": "agents.defaults.models.foo", "reloadKind": "hot"},
            {"path": "agents.defaults.models.bar", "reloadKind": None},
        ],
    }

    resp = ConfigSchemaLookupResponse.model_validate(payload)

    assert resp.gateway_id == gateway_id
    assert resp.path == "agents.defaults.models"
    assert resp.schema_ == {"type": "object"}
    assert resp.reload_kind == "restart"
    assert resp.hint_path == "agents.defaults.models"
    assert [c.reload_kind for c in resp.children] == ["hot", None]


def test_response_passes_through_unknown_reload_kind() -> None:
    """Regression guard: don't tighten to Literal[...]."""

    payload = {
        "gateway_id": uuid4(),
        "path": ".",
        "schema": {},
        "reloadKind": "warm-restart-future",
        "children": [],
    }

    resp = ConfigSchemaLookupResponse.model_validate(payload)

    assert resp.reload_kind == "warm-restart-future"


def test_child_defaults() -> None:
    child = ConfigSchemaLookupChild.model_validate({"path": "x"})
    assert child.reload_kind is None
    assert child.hint is None
