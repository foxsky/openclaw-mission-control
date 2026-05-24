# Gateway pairings page — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` to implement this plan task-by-task. Each task is TDD-shaped: failing test → run-to-fail → minimal impl → run-to-pass → commit.

**Goal:** Add a read-only operator page at `/gateways/[id]/pairings` plus DELETE action that lists devices paired to a gateway and lets an org-admin revoke stale ones, with belt-and-suspenders self-protect so MC cannot remove its own backend device.

**Architecture:** New thin FastAPI handlers in `backend/app/api/gateway.py` proxy two gateway WS RPCs (`device.pair.list`, `device.pair.remove`) through `openclaw_call`. Self-protect anchors on `load_or_create_device_identity().device_id` (Ed25519 keypair persisted on the MC backend's disk). Frontend page uses orval-generated React Query hooks; ConfirmActionDialog gates the destructive action. Structured audit logging on every DELETE outcome.

**Tech Stack:** FastAPI · SQLModel · Pydantic v2 · OpenClaw WS RPC client (`gateway_rpc.openclaw_call`) · `cryptography.hazmat` (Ed25519, already a backend dep) · Next.js App Router (client component) · React Query · orval · Tailwind · pytest (httpx ASGITransport) · vitest

**Design doc:** `docs/plans/2026-05-23-gateway-pairings-page-design.md`

**Branch / worktree:** `feat/gateway-pairings` at `.worktrees/gateway-pairings/` (set up via `superpowers:using-git-worktrees` before Task 1)

---

## Pre-flight (run once before Task 1)

```bash
cd .worktrees/gateway-pairings
# Backend deps (uv-managed venv)
cd backend && uv sync --extra dev && cd ..
# Frontend deps
cd frontend && npm install && cd ..
# Baseline backend tests (a sanity subset)
cd backend && uv run pytest tests/test_gateway_config_lookup_api.py tests/test_config_lookup_cache.py -q
```
Expected: 17 + 5 passed. If anything is red, stop and ask — don't paper over pre-existing failures.

---

## Task 1: Pre-implementation RPC probe (verify `device.pair.remove` shape)

> **Why first:** the design explicitly gates Task 5 on this. We must verify the gateway's actual param shape and error messages before coding the DELETE handler — same lesson from the config-lookup hint-shape hotfix.

**Files:**
- Create: `backend/scripts/probe_device_pair_remove.py` (one-shot, NOT committed — for inspection only)

**Step 1 — Write the probe script:**

```python
"""ONE-SHOT probe — DO NOT COMMIT. Captures the device.pair.remove call shape
against a deliberately invalid deviceId, so the DELETE handler tests use the
verified error message verbatim."""

from __future__ import annotations
import asyncio, json, sys
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.config import settings
from app.models.gateways import Gateway
from app.services.openclaw.gateway_rpc import openclaw_call, OpenClawGatewayError
from app.services.openclaw.gateway_resolver import gateway_client_config

BOGUS_DEVICE_ID = "00000000000000000000000000000000000000000000000000000000deadbeef"


async def main() -> None:
    engine = create_async_engine(settings.database_url)
    async with AsyncSession(engine) as session:
        gateway = (await session.exec(select(Gateway))).first()
        if gateway is None:
            print("no gateway in DB"); sys.exit(1)
        cfg = gateway_client_config(gateway)

        # Probe A: param shape with deviceId key
        for params in [{"deviceId": BOGUS_DEVICE_ID}, {"id": BOGUS_DEVICE_ID}, {"device": BOGUS_DEVICE_ID}]:
            try:
                r = await openclaw_call("device.pair.remove", params, config=cfg)
                print(f"PARAMS={params!r} → OK r={json.dumps(r, default=str)[:200]}")
            except OpenClawGatewayError as exc:
                print(f"PARAMS={params!r} → ERR code={exc.details and exc.details.get('code')!r} "
                      f"message={exc.details and exc.details.get('message')!r}")
            except Exception as exc:
                print(f"PARAMS={params!r} → EXC {type(exc).__name__} {exc}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2 — Run on `.64` (NOT locally; the gateway is on `.60` and MC's auth comes from `.64`):**

```bash
ssh mcontrol@192.168.2.64 'cp /tmp/probe_device_pair_remove.py /home/mcontrol/openclaw-mission-control/backend/scripts/ 2>/dev/null || true'
# Easier: scp it up:
scp backend/scripts/probe_device_pair_remove.py \
    mcontrol@192.168.2.64:/home/mcontrol/openclaw-mission-control/backend/scripts/

ssh mcontrol@192.168.2.64 \
  'cd /home/mcontrol/openclaw-mission-control/backend && \
   /root/.local/bin/uv run python scripts/probe_device_pair_remove.py 2>/dev/null'
```

Expected output: ONE of the three param shapes returns either OK (200 with empty/ack body) or an `INVALID_REQUEST` with a "device not found"-style message. The other two fail with `INVALID_REQUEST` + "missing parameter" or similar.

**Step 3 — Record findings in this plan:**

Edit this file to append a `## Verified shapes (from Task 1 probe)` section at the very bottom with:

```markdown
- Accepted params: `{"deviceId": "..."}` (or whatever the probe revealed)
- Not-found error: `code=INVALID_REQUEST, message="device not found: <id>"` (verbatim)
- Pairing-scope denied: not observed in probe (MC has the scope); document as "unverified" so the DELETE handler test mocks the canonical INVALID_REQUEST + "insufficient scope" pattern.
```

Commit the plan amendment so Tasks 5 and 6 can cite the verified shape:

```bash
rm backend/scripts/probe_device_pair_remove.py  # never commit the probe itself
git add docs/plans/2026-05-23-gateway-pairings-page-plan.md
git commit -m "docs(plans): record device.pair.remove verified call shape from probe"
```

**Step 4 — DO NOT** commit the probe script itself. It is a one-shot inspection tool.

---

## Task 2: Refactor `_map_gateway_error` into `_common` + `_config_lookup`

**Why second:** Task 5 needs to add a third specialization (`_map_pairing_error`). Refactor first so the existing config-lookup tests pass unchanged, then build on top.

**Files:**
- Modify: `backend/app/api/gateway.py` (split `_map_gateway_error`; rename the existing one to `_map_config_lookup_error`)
- Test: `backend/tests/test_gateway_error_mapper.py` (new)

**Step 1 — Failing tests** (the new `_map_gateway_error_common` doesn't exist yet):

```python
# ruff: noqa: INP001
"""Unit tests for the factored gateway error mapper."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.gateway import _map_gateway_error_common
from app.services.openclaw.gateway_rpc import OpenClawGatewayError


def test_common_extracts_canonical_fields_from_details() -> None:
    exc = OpenClawGatewayError(
        "down",
        details={"code": "UNAVAILABLE", "message": "gateway is down"},
    )
    out = _map_gateway_error_common(exc)
    assert out["code"] == "UNAVAILABLE"
    assert out["message"] == "gateway is down"
    assert out["lowered"] == "gateway is down"


def test_common_falls_back_to_str_when_details_missing() -> None:
    exc = OpenClawGatewayError("transport boom")
    out = _map_gateway_error_common(exc)
    assert out["code"] == ""
    assert out["message"] == "transport boom"
    assert out["request_id"] is None


def test_common_handles_method_not_supported_codes() -> None:
    for code in ("METHOD_NOT_FOUND", "METHOD_NOT_REGISTERED", "NOT_IMPLEMENTED"):
        exc = OpenClawGatewayError("nope", details={"code": code, "message": "nope"})
        assert _map_gateway_error_common(exc)["is_method_unsupported"] is True


def test_common_detects_method_unsupported_via_message_substring() -> None:
    exc = OpenClawGatewayError(
        "boom",
        details={"code": "INVALID_REQUEST", "message": "unknown method: device.pair.remove"},
    )
    assert _map_gateway_error_common(exc)["is_method_unsupported"] is True


def test_common_does_not_mark_random_invalid_request_as_unsupported() -> None:
    exc = OpenClawGatewayError(
        "boom", details={"code": "INVALID_REQUEST", "message": "bad input"},
    )
    assert _map_gateway_error_common(exc)["is_method_unsupported"] is False
```

**Step 2 — Run to fail:**

```bash
cd backend && uv run pytest tests/test_gateway_error_mapper.py -v
```
Expected: `ImportError: cannot import name '_map_gateway_error_common'`.

**Step 3 — Implement the refactor** in `backend/app/api/gateway.py`:

Replace the entire current `_map_gateway_error` block (around lines 396-445 — read it first to get the exact byte range) with:

```python
_GATEWAY_METHOD_REQUIRED_VERSION = "2026.5.19"


def _map_gateway_error_common(exc: OpenClawGatewayError) -> dict[str, Any]:
    """Extract canonical fields used by every endpoint-specific mapper.

    Returned dict has keys: ``code`` (uppercased), ``message``, ``lowered``
    (message.lower()), ``request_id``, ``is_method_unsupported``.
    """

    details: dict[str, Any] = exc.details if isinstance(exc.details, dict) else {}
    code = str(details.get("code") or "").upper()
    message = str(details.get("message") or str(exc) or "")
    lowered = message.lower()
    is_method_unsupported = (
        code in {"METHOD_NOT_FOUND", "METHOD_NOT_REGISTERED", "NOT_IMPLEMENTED"}
        or "method not found" in lowered
        or "unknown method" in lowered
    )
    return {
        "code": code,
        "message": message,
        "lowered": lowered,
        "request_id": exc.request_id,
        "is_method_unsupported": is_method_unsupported,
    }


def _map_config_lookup_error(
    exc: OpenClawGatewayError, path: str,
) -> HTTPException:
    """Translate an OpenClawGatewayError from config.schema.lookup into HTTP."""

    fields = _map_gateway_error_common(exc)
    code = fields["code"]
    message = fields["message"]

    logger.warning(
        "gateway.config_lookup.failed path=%r code=%s request_id=%s message=%s",
        path, code or "<none>", fields["request_id"] or "<none>", message or "<none>",
    )

    if code == "INVALID_REQUEST":
        if message == "config schema path not found":
            return HTTPException(
                status_code=404,
                detail={"error": "path_not_found", "path": path},
            )
        return HTTPException(
            status_code=422,
            detail={"error": "gateway_rejected_request", "detail": message},
        )
    if fields["is_method_unsupported"]:
        return HTTPException(
            status_code=501,
            detail={
                "error": "method_unsupported",
                "requires_gateway_version": _GATEWAY_METHOD_REQUIRED_VERSION,
            },
        )
    if code == "UNAVAILABLE":
        return HTTPException(
            status_code=503,
            detail={"error": "gateway_unavailable", "detail": message},
        )
    return HTTPException(
        status_code=503,
        detail={"error": "gateway_unreachable", "detail": message},
    )
```

Then update the existing call site in `gateway_config_lookup`:

```python
# was: raise _map_gateway_error(exc, trimmed_path) from exc
raise _map_config_lookup_error(exc, trimmed_path) from exc
```

**Step 4 — Verify it passes + existing tests still pass:**

```bash
cd backend && uv run pytest tests/test_gateway_error_mapper.py tests/test_gateway_config_lookup_api.py -v
```
Expected: 5 new + 17 existing = 22 passed, no regressions.

**Step 5 — Commit:**

```bash
git add backend/app/api/gateway.py backend/tests/test_gateway_error_mapper.py
git commit -m "refactor(api): factor _map_gateway_error into common + config-lookup variants"
```

---

## Task 3: Response schemas + projection helper

**Files:**
- Modify: `backend/app/schemas/gateway_api.py` (append at end)
- Test: `backend/tests/test_gateway_devices_schema.py` (new)

**Step 1 — Failing test:**

```python
# ruff: noqa: INP001
"""Schema + projection unit tests for the gateway devices endpoint."""

from __future__ import annotations

from uuid import uuid4

from app.api.gateway import _project_gateway_device
from app.schemas.gateway_api import GatewayDevice, GatewayDeviceListResponse


def test_project_flattens_tokens_into_scopes_union_and_last_used() -> None:
    raw = {
        "deviceId": "abc123",
        "publicKey": "PUBKEYBASE64URL",
        "platform": "linux",
        "clientId": "cli",
        "clientMode": "cli",
        "role": "operator",
        "scopes": ["operator.admin"],
        "remoteIp": "192.168.2.64",
        "approvedAtMs": 1000,
        "tokens": [
            {"role": "operator", "scopes": ["operator.read", "operator.admin"],
             "createdAtMs": 1000, "lastUsedAtMs": 1500},
            {"role": "operator", "scopes": ["operator.write"],
             "createdAtMs": 1100, "lastUsedAtMs": 1200},
        ],
    }
    out = _project_gateway_device(raw, local_device_id="other-device")
    assert out.device_id == "abc123"
    assert out.token_count == 2
    assert sorted(out.scopes) == ["operator.admin", "operator.read", "operator.write"]
    assert out.last_used_at_ms == 1500
    assert out.is_self is False


def test_project_marks_is_self_when_device_id_matches_local() -> None:
    raw = {"deviceId": "ME", "publicKey": "K", "tokens": []}
    out = _project_gateway_device(raw, local_device_id="ME")
    assert out.is_self is True


def test_project_handles_missing_tokens_and_missing_last_used() -> None:
    raw = {"deviceId": "x", "publicKey": "K"}
    out = _project_gateway_device(raw, local_device_id=None)
    assert out.token_count == 0
    assert out.scopes == []
    assert out.last_used_at_ms is None
    assert out.is_self is False


def test_response_round_trips_alias_keys() -> None:
    payload = {
        "gateway_id": uuid4(),
        "devices": [
            {
                "deviceId": "x",
                "publicKey": "K",
                "tokenCount": 1,
                "lastUsedAtMs": 100,
                "isSelf": False,
            }
        ],
    }
    resp = GatewayDeviceListResponse.model_validate(payload)
    assert resp.devices[0].token_count == 1
    assert resp.devices[0].is_self is False
```

**Step 2 — Run to fail:**

```bash
cd backend && uv run pytest tests/test_gateway_devices_schema.py -v
```
Expected: `ImportError` on `_project_gateway_device` and `GatewayDevice`.

**Step 3 — Implement.**

Append to `backend/app/schemas/gateway_api.py`:

```python
class GatewayDevice(SQLModel):
    """One device paired with the gateway (server-flattened from the wire shape)."""

    device_id: str = Field(alias="deviceId")
    public_key: str = Field(alias="publicKey")
    platform: str | None = None
    client_id: str | None = Field(default=None, alias="clientId")
    client_mode: str | None = Field(default=None, alias="clientMode")
    role: str | None = None
    scopes: list[str] = Field(default_factory=list)
    token_count: int = Field(default=0, alias="tokenCount")
    last_used_at_ms: int | None = Field(default=None, alias="lastUsedAtMs")
    remote_ip: str | None = Field(default=None, alias="remoteIp")
    approved_at_ms: int | None = Field(default=None, alias="approvedAtMs")
    is_self: bool = Field(default=False, alias="isSelf")

    model_config = SQLModelConfig(validate_by_name=True)


class GatewayDeviceListResponse(SQLModel):
    gateway_id: UUID
    devices: list[GatewayDevice] = Field(default_factory=list)
    is_self_resolved: bool = Field(default=True, alias="isSelfResolved")

    model_config = SQLModelConfig(validate_by_name=True)
```

(`Any`, `UUID`, `Field`, `SQLModelConfig` are already imported by Task 1's hotfix; verify they are still present.)

Add the projection helper to `backend/app/api/gateway.py`, near `_validate_config_lookup_path`:

```python
def _project_gateway_device(
    raw: dict[str, Any], *, local_device_id: str | None,
) -> GatewayDevice:
    """Flatten the gateway's per-device dict into the wire shape MC exposes.

    Combines all token-level ``scopes`` into a single union, computes the
    most-recent ``lastUsedAtMs`` across tokens, and marks ``is_self`` when
    ``raw.deviceId`` matches the locally persisted Ed25519 identity.
    """

    tokens = raw.get("tokens") or []
    if not isinstance(tokens, list):
        tokens = []

    scopes_union: set[str] = set()
    for s in (raw.get("scopes") or []):
        if isinstance(s, str):
            scopes_union.add(s)
    last_used: int | None = None
    for t in tokens:
        if not isinstance(t, dict):
            continue
        for s in (t.get("scopes") or []):
            if isinstance(s, str):
                scopes_union.add(s)
        ts = t.get("lastUsedAtMs")
        if isinstance(ts, int) and (last_used is None or ts > last_used):
            last_used = ts

    device_id = str(raw.get("deviceId") or "")
    return GatewayDevice.model_validate(
        {
            **raw,
            "scopes": sorted(scopes_union),
            "tokenCount": len(tokens),
            "lastUsedAtMs": last_used,
            "isSelf": bool(local_device_id) and device_id == local_device_id,
            # tokens is not in GatewayDevice; ignored by Pydantic due to extra='ignore'
        }
    )
```

Add `GatewayDevice`, `GatewayDeviceListResponse` to the existing `from app.schemas.gateway_api import (...)` import block in `gateway.py`.

**Step 4 — Verify it passes:**

```bash
cd backend && uv run pytest tests/test_gateway_devices_schema.py -v
```
Expected: 4 passed.

**Step 5 — Commit:**

```bash
git add backend/app/api/gateway.py backend/app/schemas/gateway_api.py backend/tests/test_gateway_devices_schema.py
git commit -m "feat(api): add GatewayDevice schemas and projection helper"
```

---

## Task 4: GET handler — happy path, empty, malformed, cross-org, self-identity-unavailable

**Files:**
- Modify: `backend/app/api/gateway.py` (add handler + self-identity loader integration)
- Test: `backend/tests/test_gateway_devices_api.py` (new — also reused by Tasks 5-6)

**Step 1 — Failing tests** (test file shared across Tasks 4-6; append in each):

```python
# ruff: noqa: INP001
"""Integration tests for /api/v1/gateways/{id}/devices."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import gateway as gateway_api
from app.api.deps import require_org_admin
from app.api.gateway import router as gateway_router
from app.core.auth import AuthContext, get_auth_context
from app.db.session import get_session
from app.models.gateways import Gateway
from app.models.organization_members import OrganizationMember
from app.models.organizations import Organization
from app.models.users import User
from app.services.openclaw.gateway_rpc import OpenClawGatewayError
from app.services.organizations import OrganizationContext


@dataclass
class _FakeIdentity:
    device_id: str
    public_key_pem: str = ""
    private_key_pem: str = ""


_LOCAL_DEVICE_ID = "abc-mc-self-device-id"


def _build_app(
    session_maker: async_sessionmaker[AsyncSession], *, organization: Organization,
) -> FastAPI:
    app = FastAPI()
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(gateway_router)
    app.include_router(api_v1)

    async def _override_get_session() -> AsyncSession:
        async with session_maker() as session:
            yield session

    async def _override_require_org_admin() -> OrganizationContext:
        return OrganizationContext(
            organization=organization,
            member=OrganizationMember(
                organization_id=organization.id, user_id=uuid4(),
                role="owner", all_boards_read=True, all_boards_write=True,
            ),
        )

    async def _override_get_auth_context() -> AuthContext:
        return AuthContext(
            actor_type="user",
            user=User(id=uuid4(), email="op@example.local"),
        )

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[require_org_admin] = _override_require_org_admin
    app.dependency_overrides[get_auth_context] = _override_get_auth_context
    return app


@pytest_asyncio.fixture
async def setup() -> AsyncIterator[tuple[FastAPI, Organization, Gateway]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    org = Organization(id=uuid4(), name="Org One")
    gateway = Gateway(
        id=uuid4(), organization_id=org.id, name="GW",
        url="https://gateway.example.local", workspace_root="/workspace/openclaw",
    )
    async with sm() as session:
        session.add(org); session.add(gateway); await session.commit()
    app = _build_app(sm, organization=org)
    try:
        yield app, org, gateway
    finally:
        await engine.dispose()


def _identity_self() -> _FakeIdentity:
    return _FakeIdentity(device_id=_LOCAL_DEVICE_ID)


def _identity_raises() -> _FakeIdentity:
    raise RuntimeError("identity file corrupted")


@pytest.mark.asyncio
async def test_list_happy_marks_one_device_as_self(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        assert method == "device.pair.list"
        return {
            "pending": [],
            "paired": [
                {
                    "deviceId": _LOCAL_DEVICE_ID, "publicKey": "K1",
                    "clientId": "gateway-client", "clientMode": "backend",
                    "tokens": [{"role": "operator", "scopes": ["operator.read"],
                                "lastUsedAtMs": 1500}],
                },
                {
                    "deviceId": "other", "publicKey": "K2",
                    "clientId": "cli", "clientMode": "cli",
                    "tokens": [{"role": "operator", "scopes": ["operator.admin"]}],
                },
            ],
        }

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["isSelfResolved"] is True
    assert len(body["devices"]) == 2
    selfs = [d for d in body["devices"] if d["isSelf"]]
    assert len(selfs) == 1
    assert selfs[0]["deviceId"] == _LOCAL_DEVICE_ID


@pytest.mark.asyncio
async def test_list_empty(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"pending": [], "paired": []}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 200
    assert resp.json()["devices"] == []


@pytest.mark.asyncio
async def test_list_self_identity_unavailable(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"pending": [], "paired": [{"deviceId": "x", "publicKey": "K", "tokens": []}]}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_raises)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["isSelfResolved"] is False
    assert all(d["isSelf"] is False for d in body["devices"])


@pytest.mark.asyncio
async def test_list_malformed_payload_returns_502(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"paired": "not-a-list"}  # malformed

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{gateway.id}/devices")
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "gateway_invalid_payload"


@pytest.mark.asyncio
async def test_list_cross_org_returns_404(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _ = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {"pending": [], "paired": []}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/gateways/{uuid4()}/devices")
    assert resp.status_code == 404
```

**Step 2 — Run to fail:**

```bash
cd backend && uv run pytest tests/test_gateway_devices_api.py -v
```
Expected: 404 (route doesn't exist) or import error on `load_or_create_device_identity` attribute on the module.

**Step 3 — Implement** in `backend/app/api/gateway.py`:

Add to the imports block:

```python
from pydantic import ValidationError
from app.services.openclaw.device_identity import (
    DeviceIdentity,
    load_or_create_device_identity,
)
```

(`load_or_create_device_identity` MUST be importable at module level — the test monkeypatches it on `gateway_api`.)

Add a helper near `_validate_config_lookup_path`:

```python
def _resolve_local_device_id() -> str | None:
    """Return the locally persisted Ed25519 device_id, or None if unreadable.

    Failures (missing identity file, corrupted PEM, key derivation error) MUST
    NOT block list reads; the response signals ``isSelfResolved=False`` and the
    UI disables every Remove button. Writes (DELETE) MUST refuse — see Task 5.
    """
    try:
        identity = load_or_create_device_identity()
    except Exception:  # noqa: BLE001 — opaque to caller; logged at error level
        logger.error(
            "gateway.pairing.identity.load_failed",
            exc_info=True,
        )
        return None
    return identity.device_id
```

Add the handler at the end of the file:

```python
_PAIRING_RPC_TIMEOUT_SECONDS = 5.0


@router.get(
    "/{gateway_id}/devices",
    response_model=GatewayDeviceListResponse,
    response_model_by_alias=True,
    operation_id="list_gateway_devices",
)
async def list_gateway_devices(
    gateway_id: UUID,
    session: AsyncSession = SESSION_DEP,
    _auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewayDeviceListResponse:
    """List devices paired with the gateway, with self-protect marking."""

    gateway = await GatewayAdminLifecycleService(session).require_gateway(
        gateway_id=gateway_id, organization_id=ctx.organization.id,
    )
    cfg = gateway_client_config(gateway)
    local_device_id = _resolve_local_device_id()

    payload = await asyncio.wait_for(
        openclaw_call("device.pair.list", config=cfg),
        timeout=_PAIRING_RPC_TIMEOUT_SECONDS,
    )
    if not isinstance(payload, dict):
        logger.error(
            "gateway.pairing.list.invalid_payload gateway_id=%s type=%s",
            gateway_id, type(payload).__name__,
        )
        raise HTTPException(
            status_code=502, detail={"error": "gateway_invalid_payload"},
        )
    paired = payload.get("paired")
    if not isinstance(paired, list):
        logger.error(
            "gateway.pairing.list.invalid_payload gateway_id=%s paired_type=%s",
            gateway_id, type(paired).__name__,
        )
        raise HTTPException(
            status_code=502, detail={"error": "gateway_invalid_payload"},
        )

    try:
        devices = [
            _project_gateway_device(raw, local_device_id=local_device_id)
            for raw in paired
            if isinstance(raw, dict)
        ]
    except ValidationError as exc:
        logger.error(
            "gateway.pairing.list.projection_failed gateway_id=%s errors=%s",
            gateway_id,
            exc.errors(include_url=False, include_input=False),
        )
        raise HTTPException(
            status_code=502, detail={"error": "gateway_invalid_payload"},
        ) from exc

    return GatewayDeviceListResponse.model_validate(
        {
            "gateway_id": gateway_id,
            "devices": [d.model_dump(by_alias=True) for d in devices],
            "isSelfResolved": local_device_id is not None,
        }
    )
```

(The `model_dump(by_alias=True)` round-trip inside the response is needed so the outer model emits aliased keys. If this proves clunky, switch to `GatewayDeviceListResponse(...)` direct construction in the implementer's judgement.)

**Step 4 — Verify it passes:**

```bash
cd backend && uv run pytest tests/test_gateway_devices_api.py -v -k "list_"
```
Expected: 5 passed.

**Step 5 — Commit:**

```bash
git add backend/app/api/gateway.py backend/tests/test_gateway_devices_api.py
git commit -m "feat(api): add GET /gateways/{id}/devices with self-identity marking"
```

---

## Task 5: DELETE handler + pairing-error mapper

**Files:**
- Modify: `backend/app/api/gateway.py` (add `_map_pairing_error` + DELETE handler)
- Modify: `backend/tests/test_gateway_devices_api.py` (append 7 tests)

**Step 1 — Failing tests** (append to the test file):

```python
@pytest.mark.asyncio
async def test_remove_happy(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    captured: list[tuple[str, Any]] = []

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        captured.append((method, params))
        return {"ok": True}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/some-other-device")
    assert resp.status_code == 200
    assert captured == [("device.pair.remove", {"deviceId": "some-other-device"})]


@pytest.mark.asyncio
async def test_remove_self_protect(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    called = False

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/{_LOCAL_DEVICE_ID}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "cannot_remove_self"
    assert called is False


@pytest.mark.asyncio
async def test_remove_self_identity_unavailable_refuses(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    called = False

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal called; called = True; return {}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_raises)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/anything")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "self_identity_unavailable"
    assert called is False


@pytest.mark.asyncio
async def test_remove_device_not_found_returns_404(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError(
            "device not found",
            details={"code": "INVALID_REQUEST", "message": "device not found: xx"},
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "device_not_found"


@pytest.mark.asyncio
async def test_remove_pairing_scope_denied_returns_403(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError(
            "insufficient scope",
            details={"code": "INVALID_REQUEST", "message": "insufficient scope: operator.pairing"},
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "gateway_pairing_scope_denied"


@pytest.mark.asyncio
async def test_remove_gateway_unavailable_returns_503(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError("down", details={"code": "UNAVAILABLE"})

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_remove_timeout_returns_504(
    setup: tuple[FastAPI, Organization, Gateway], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio as _asyncio
    app, _, gateway = setup

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        await _asyncio.sleep(10)

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)
    monkeypatch.setattr(gateway_api, "_PAIRING_RPC_TIMEOUT_SECONDS", 0.05)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/xx")
    assert resp.status_code == 504
```

**Step 2 — Run to fail:**

```bash
cd backend && uv run pytest tests/test_gateway_devices_api.py -v -k "remove_"
```
Expected: 405 (no DELETE route) or attribute errors.

**Step 3 — Implement** in `backend/app/api/gateway.py`:

Add the pairing-specific mapper near `_map_config_lookup_error`:

```python
def _map_pairing_error(
    exc: OpenClawGatewayError, *, device_id: str,
) -> HTTPException:
    fields = _map_gateway_error_common(exc)
    code = fields["code"]
    message = fields["message"]
    lowered = fields["lowered"]

    logger.warning(
        "gateway.pairing.failed device_id=%s code=%s request_id=%s message=%s",
        device_id, code or "<none>",
        fields["request_id"] or "<none>", message or "<none>",
    )

    if code == "INVALID_REQUEST":
        if "device not found" in lowered or "unknown device" in lowered:
            return HTTPException(
                status_code=404,
                detail={"error": "device_not_found", "device_id": device_id},
            )
        if "insufficient scope" in lowered or "missing scope" in lowered:
            return HTTPException(
                status_code=403,
                detail={"error": "gateway_pairing_scope_denied"},
            )
        return HTTPException(
            status_code=422,
            detail={"error": "gateway_rejected_request", "detail": message},
        )
    if fields["is_method_unsupported"]:
        return HTTPException(
            status_code=501,
            detail={"error": "method_unsupported",
                    "requires_gateway_version": _GATEWAY_METHOD_REQUIRED_VERSION},
        )
    if code == "UNAVAILABLE":
        return HTTPException(
            status_code=503,
            detail={"error": "gateway_unavailable", "detail": message},
        )
    return HTTPException(
        status_code=503,
        detail={"error": "gateway_unreachable", "detail": message},
    )
```

Add the DELETE handler at the end of the file:

```python
@router.delete(
    "/{gateway_id}/devices/{device_id}",
    operation_id="remove_gateway_device",
)
async def remove_gateway_device(
    gateway_id: UUID,
    device_id: str,
    session: AsyncSession = SESSION_DEP,
    _auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> dict[str, Any]:
    """Revoke a paired device from the gateway, with self-protect."""

    gateway = await GatewayAdminLifecycleService(session).require_gateway(
        gateway_id=gateway_id, organization_id=ctx.organization.id,
    )

    local_device_id = _resolve_local_device_id()
    if local_device_id is None:
        # Refuse writes when self-protect cannot be evaluated.
        raise HTTPException(
            status_code=503, detail={"error": "self_identity_unavailable"},
        )
    if device_id == local_device_id:
        raise HTTPException(
            status_code=409,
            detail={"error": "cannot_remove_self", "device_id": device_id},
        )

    cfg = gateway_client_config(gateway)
    try:
        await asyncio.wait_for(
            openclaw_call("device.pair.remove", {"deviceId": device_id}, config=cfg),
            timeout=_PAIRING_RPC_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504, detail={"error": "gateway_timeout"},
        ) from exc
    except OpenClawGatewayError as exc:
        raise _map_pairing_error(exc, device_id=device_id) from exc

    return {"ok": True, "device_id": device_id}
```

**Step 4 — Verify all 12 tests pass (5 from Task 4 + 7 here):**

```bash
cd backend && uv run pytest tests/test_gateway_devices_api.py -v
```
Expected: 12 passed.

**Step 5 — Commit:**

```bash
git add backend/app/api/gateway.py backend/tests/test_gateway_devices_api.py
git commit -m "feat(api): add DELETE /gateways/{id}/devices/{device_id} with self-protect"
```

---

## Task 6: Audit logging on every DELETE outcome

**Files:**
- Modify: `backend/app/api/gateway.py` (add structured `logger.info` lines around the DELETE flow)
- Modify: `backend/tests/test_gateway_devices_api.py` (append a parametrized outcome-log test)

**Step 1 — Failing test** (append):

```python
import logging


@pytest.mark.parametrize(
    "scenario,expected_outcome,expected_status",
    [
        ("happy", "success", 200),
        ("not_found", "device_not_found", 404),
        ("self", "cannot_remove_self", 409),
        ("unavailable", "gateway_unavailable", 503),
    ],
)
@pytest.mark.asyncio
async def test_remove_emits_audit_log_per_outcome(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
    expected_outcome: str,
    expected_status: int,
) -> None:
    app, _, gateway = setup

    if scenario == "happy":
        async def _fake(m: str, p: Any = None, *, config: Any) -> object: return {"ok": True}
        target_id = "other-id"
    elif scenario == "not_found":
        async def _fake(m: str, p: Any = None, *, config: Any) -> object:
            raise OpenClawGatewayError(
                "device not found",
                details={"code": "INVALID_REQUEST", "message": "device not found: x"},
            )
        target_id = "x"
    elif scenario == "self":
        async def _fake(m: str, p: Any = None, *, config: Any) -> object: return {"ok": True}
        target_id = _LOCAL_DEVICE_ID
    else:  # unavailable
        async def _fake(m: str, p: Any = None, *, config: Any) -> object:
            raise OpenClawGatewayError("down", details={"code": "UNAVAILABLE"})
        target_id = "x"

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "load_or_create_device_identity", _identity_self)

    caplog.set_level(logging.INFO, logger="app.api.gateway")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/gateways/{gateway.id}/devices/{target_id}")
    assert resp.status_code == expected_status

    audit_lines = [r for r in caplog.records if "gateway.pairing.remove.outcome" in r.getMessage()]
    assert len(audit_lines) == 1
    assert f"outcome={expected_outcome}" in audit_lines[0].getMessage()
    assert f"device_id={target_id}" in audit_lines[0].getMessage()
```

**Step 2 — Run to fail:**

```bash
cd backend && uv run pytest tests/test_gateway_devices_api.py::test_remove_emits_audit_log_per_outcome -v
```
Expected: 0/4 passed (no log lines emitted yet).

**Step 3 — Implement.**

Restructure `remove_gateway_device` to centralize the outcome log. Replace the body with:

```python
@router.delete(
    "/{gateway_id}/devices/{device_id}",
    operation_id="remove_gateway_device",
)
async def remove_gateway_device(
    gateway_id: UUID,
    device_id: str,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> dict[str, Any]:
    user_id = auth.user.id if auth.user else None

    logger.info(
        "gateway.pairing.remove.attempt user_id=%s org_id=%s gateway_id=%s device_id=%s",
        user_id, ctx.organization.id, gateway_id, device_id,
    )

    def _audit(outcome: str, request_id: str | None = None) -> None:
        logger.info(
            "gateway.pairing.remove.outcome user_id=%s org_id=%s gateway_id=%s "
            "device_id=%s outcome=%s request_id=%s",
            user_id, ctx.organization.id, gateway_id, device_id,
            outcome, request_id or "<none>",
        )

    gateway = await GatewayAdminLifecycleService(session).require_gateway(
        gateway_id=gateway_id, organization_id=ctx.organization.id,
    )

    local_device_id = _resolve_local_device_id()
    if local_device_id is None:
        _audit("self_identity_unavailable")
        raise HTTPException(
            status_code=503, detail={"error": "self_identity_unavailable"},
        )
    if device_id == local_device_id:
        _audit("cannot_remove_self")
        raise HTTPException(
            status_code=409,
            detail={"error": "cannot_remove_self", "device_id": device_id},
        )

    cfg = gateway_client_config(gateway)
    try:
        await asyncio.wait_for(
            openclaw_call("device.pair.remove", {"deviceId": device_id}, config=cfg),
            timeout=_PAIRING_RPC_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        _audit("gateway_timeout")
        raise HTTPException(
            status_code=504, detail={"error": "gateway_timeout"},
        ) from exc
    except OpenClawGatewayError as exc:
        http_exc = _map_pairing_error(exc, device_id=device_id)
        outcome_map = {
            "device_not_found": "device_not_found",
            "gateway_pairing_scope_denied": "scope_denied",
            "gateway_unavailable": "gateway_unavailable",
            "gateway_unreachable": "gateway_unreachable",
            "gateway_rejected_request": "gateway_rejected_request",
            "method_unsupported": "method_unsupported",
        }
        detail_error = (
            http_exc.detail.get("error") if isinstance(http_exc.detail, dict) else None
        )
        _audit(outcome_map.get(detail_error, "other"), request_id=exc.request_id)
        raise http_exc from exc

    _audit("success")
    return {"ok": True, "device_id": device_id}
```

Note `_auth` was renamed to `auth` (we need `auth.user.id`); the unused-arg lint is no longer applicable because we use the value.

**Step 4 — Verify the parametrized test + all other tests pass:**

```bash
cd backend && uv run pytest tests/test_gateway_devices_api.py -v
```
Expected: 16 passed (12 + 4 parametrized cases).

**Step 5 — Commit:**

```bash
git add backend/app/api/gateway.py backend/tests/test_gateway_devices_api.py
git commit -m "feat(api): emit structured audit log on every pairing remove outcome"
```

---

## Task 7: Regenerate orval API client

**Files:**
- Run: `make api-gen` against the worktree's backend (export openapi via `scripts/export_openapi.py`, point orval at the file)
- Modify: nothing manually

**Step 1 — Export the openapi spec from the new backend:**

```bash
cd backend
AUTH_MODE=local LOCAL_AUTH_TOKEN=test-local-token-0123456789-0123456789-0123456789x \
DATABASE_URL="sqlite+aiosqlite:///:memory:" BASE_URL=http://localhost:8000 \
  uv run python scripts/export_openapi.py
cd ..
```

Verify the new operations exist:

```bash
python3 -c "import json; d=json.load(open('backend/openapi.json')); \
  print([p for p in d['paths'] if '/devices' in p])"
```
Expected output: `['/api/v1/gateways/{gateway_id}/devices', '/api/v1/gateways/{gateway_id}/devices/{device_id}']`.

**Step 2 — Build a DRIFT-only openapi (no new operations) so the regen split mirrors the config-lookup PR's two-commit pattern:**

```bash
python3 -c "
import json
d = json.load(open('backend/openapi.json'))
d['paths'] = {k: v for k, v in d['paths'].items() if '/devices' not in k}
for k in list(d.get('components', {}).get('schemas', {}).keys()):
    if 'GatewayDevice' in k:
        del d['components']['schemas'][k]
json.dump(d, open('backend/openapi.drift.json', 'w'), indent=2, sort_keys=True)
"
```

**Step 3 — Reset frontend/generated tree to master baseline:**

```bash
git checkout -- frontend/src/api/generated/
# Remove any new orval files that may have been created in this branch's history
```

**Step 4 — Regen against the drift-only spec:**

```bash
cd frontend && ORVAL_INPUT=../backend/openapi.drift.json npm run api:gen
cd ..
git status -s frontend/src/api/generated | head
```

If the diff is non-empty (catching backend drift since the last regen), commit it:

```bash
git add frontend/src/api/generated
git commit -m "chore(api-client): catch up incidental drift from regen"
```

If the diff is empty, skip the drift commit and proceed to step 5.

**Step 5 — Regen against the full spec (incl. the new endpoints):**

```bash
cd frontend && ORVAL_INPUT=../backend/openapi.json npm run api:gen
cd ..
grep -l "ListGatewayDevices\|RemoveGatewayDevice\|GatewayDevice " frontend/src/api/generated -r | head -5
```
Expected: at least 3 files (the new model files + `gateways.ts` for the hooks).

**Step 6 — Typecheck:**

```bash
cd frontend && npx tsc --noEmit
```
Expected: clean.

**Step 7 — Commit:**

```bash
cd ..
rm -f backend/openapi.json backend/openapi.drift.json
git add frontend/src/api/generated
git commit -m "feat(api-client): regenerate after gateway devices endpoints"
```

---

## Task 8: ConfirmActionDialog wiring + alias hook

**Files:**
- Modify: nothing (verify-only)

**Step 1 — Verify `ConfirmActionDialog` exists and read its props:**

```bash
cat frontend/src/components/ui/confirm-action-dialog.tsx | head -40
```

If the API surface differs from what Task 9 assumes (`open`, `onOpenChange`, `title`, `body`, `confirmLabel`, `onConfirm`), pause and adjust Task 9. If it matches, no work needed — proceed to Task 9.

No commit for this task.

---

## Task 9: Frontend page `/gateways/[id]/pairings`

**Files:**
- Create: `frontend/src/app/gateways/[gatewayId]/pairings/page.tsx`
- Create: `frontend/src/app/gateways/[gatewayId]/pairings/page.test.tsx`

**Step 1 — Failing vitest** (uses heavy mocks; pattern from `gateways/[gatewayId]/config/page.test.tsx`):

```tsx
import React from "react";
import { describe, expect, it, vi, type Mock } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const baseEnvelope = (overrides: object = {}) => ({
  data: {
    status: 200,
    data: {
      gateway_id: "gw-1",
      isSelfResolved: true,
      devices: [
        {
          deviceId: "self-id",
          publicKey: "K1",
          clientId: "gateway-client",
          clientMode: "backend",
          remoteIp: "192.168.2.64",
          scopes: ["operator.admin", "operator.pairing"],
          tokenCount: 1,
          lastUsedAtMs: 1700000000000,
          isSelf: true,
        },
        {
          deviceId: "stale-cli-id",
          publicKey: "K2",
          clientId: "cli",
          clientMode: "cli",
          remoteIp: null,
          scopes: ["operator.admin"],
          tokenCount: 1,
          lastUsedAtMs: 1500000000000,
          isSelf: false,
        },
      ],
      ...overrides,
    },
    headers: new Headers(),
  },
  isLoading: false,
  error: null,
});

const useListMock = vi.fn(() => baseEnvelope());
const removeMutateMock = vi.fn();
vi.mock("@/api/generated/gateways/gateways", () => ({
  useListGatewayDevices: (...args: unknown[]) => useListMock(...args),
  useRemoveGatewayDevice: () => ({
    mutate: removeMutateMock,
    mutateAsync: removeMutateMock,
    isPending: false,
  }),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ gatewayId: "gw-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/gateways/gw-1/pairings",
}));
vi.mock("@/auth/clerk", () => ({ useAuth: () => ({ isSignedIn: true }) }));
vi.mock("@/lib/use-organization-membership", () => ({
  useOrganizationMembership: () => ({ isAdmin: true }),
}));
vi.mock("@/components/templates/DashboardPageLayout", () => ({
  // Stub: render headerActions + children so the page renders bare.
  DashboardPageLayout: ({ headerActions, children }: { headerActions?: React.ReactNode; children: React.ReactNode }) => (
    <div>{headerActions}{children}</div>
  ),
}));

import GatewayPairingsPage from "./page";

function renderPage() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <GatewayPairingsPage />
    </QueryClientProvider>,
  );
}

describe("GatewayPairingsPage", () => {
  it("renders one row per device with client and remoteIp", () => {
    renderPage();
    expect(screen.getByText(/gateway-client/i)).toBeInTheDocument();
    expect(screen.getByText("cli")).toBeInTheDocument();
    expect(screen.getByText("192.168.2.64")).toBeInTheDocument();
  });

  it("disables Remove and labels the isSelf row", () => {
    renderPage();
    const buttons = screen.getAllByRole("button", { name: /remove/i });
    expect(buttons.length).toBe(2);
    const selfRowButton = buttons.find(
      (b) => b.getAttribute("title")?.includes("MC's own backend device"),
    );
    expect(selfRowButton).toBeDefined();
    expect(selfRowButton).toBeDisabled();
    expect(screen.getByText(/this is MC/i)).toBeInTheDocument();
  });

  it("clicking Remove on non-self opens the confirm dialog with truncated id", async () => {
    renderPage();
    const buttons = screen.getAllByRole("button", { name: /remove/i });
    const staleButton = buttons.find((b) => !b.hasAttribute("disabled"))!;
    await userEvent.click(staleButton);
    expect(screen.getByText(/stale-cli-id/)).toBeInTheDocument();
  });

  it("confirming fires the delete mutation", async () => {
    renderPage();
    const staleButton = screen.getAllByRole("button", { name: /remove/i })
      .find((b) => !b.hasAttribute("disabled"))!;
    await userEvent.click(staleButton);
    await userEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
    await waitFor(() => {
      expect(removeMutateMock).toHaveBeenCalled();
    });
  });

  it("isSelfResolved=false disables every Remove button and shows the banner", () => {
    (useListMock as Mock).mockReturnValueOnce(
      baseEnvelope({ isSelfResolved: false, devices: [
        { deviceId: "x", publicKey: "K", clientId: "cli", clientMode: "cli",
          scopes: [], tokenCount: 0, lastUsedAtMs: null, isSelf: false },
      ] }),
    );
    renderPage();
    expect(screen.getByText(/could not verify its own device identity/i)).toBeInTheDocument();
    for (const b of screen.getAllByRole("button", { name: /remove/i })) {
      expect(b).toBeDisabled();
    }
  });

  it("after a 404 delete the list refetches", async () => {
    removeMutateMock.mockImplementationOnce((_arg: unknown, opts: { onError?: (e: unknown) => void } = {}) => {
      opts.onError?.({ status: 404, response: { status: 404 } });
    });
    renderPage();
    const staleButton = screen.getAllByRole("button", { name: /remove/i })
      .find((b) => !b.hasAttribute("disabled"))!;
    await userEvent.click(staleButton);
    await userEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
    // The page should toast and invalidate; useListMock will be called again on refetch.
    await waitFor(() => {
      expect(useListMock).toHaveBeenCalled();
    });
  });
});
```

**Step 2 — Run to fail:**

```bash
cd frontend && npm run test -- "gateways/\\[gatewayId\\]/pairings"
```
Expected: `Cannot find module './page'`.

**Step 3 — Implement** `frontend/src/app/gateways/[gatewayId]/pairings/page.tsx`:

```tsx
"use client";

import { Suspense, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/auth/clerk";
import { useOrganizationMembership } from "@/lib/use-organization-membership";
import { DashboardPageLayout } from "@/components/templates/DashboardPageLayout";
import { Button } from "@/components/ui/button";
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog";
import { cn } from "@/lib/utils";
import { formatTimestamp } from "@/lib/formatters";
import {
  useListGatewayDevices,
  useRemoveGatewayDevice,
} from "@/api/generated/gateways/gateways";

export const dynamic = "force-dynamic";

type Device = {
  deviceId: string;
  publicKey: string;
  clientId?: string | null;
  clientMode?: string | null;
  remoteIp?: string | null;
  scopes: string[];
  tokenCount: number;
  lastUsedAtMs: number | null;
  isSelf: boolean;
};

type ListResponse = {
  gateway_id: string;
  isSelfResolved: boolean;
  devices: Device[];
};

function unwrap(data: unknown): ListResponse | null {
  if (!data || typeof data !== "object") return null;
  const env = data as { status?: number; data?: unknown };
  if (typeof env.status === "number" && env.data) {
    return env.data as ListResponse;
  }
  return null;
}

function Inner({ gatewayId }: { gatewayId: string }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { data, isLoading, error, queryKey } = useListGatewayDevices(gatewayId) as {
    data: unknown; isLoading: boolean; error: unknown; queryKey?: unknown;
  };
  const { mutate: removeDevice, isPending } = useRemoveGatewayDevice();
  const [target, setTarget] = useState<Device | null>(null);

  const lookup = unwrap(data);
  if (isLoading) return <div className="text-sm text-muted">Loading…</div>;
  if (error) return <div className="text-sm text-red-700">Failed to load.</div>;
  if (!lookup) return <div className="text-sm text-muted">No data.</div>;

  const onConfirm = () => {
    if (!target) return;
    removeDevice(
      { gatewayId, deviceId: target.deviceId },
      {
        onSettled: () => {
          // Refetch the list whether delete succeeded or 404'd (already-removed race).
          queryClient.invalidateQueries({ queryKey });
          setTarget(null);
        },
      },
    );
  };

  const writeBlocked = !lookup.isSelfResolved;

  return (
    <div className="flex flex-col gap-4">
      {writeBlocked && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          MC could not verify its own device identity. Remove actions are
          disabled until this is resolved.
        </div>
      )}
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-2">Client</th>
            <th>Remote IP</th>
            <th>Last used</th>
            <th>Scopes</th>
            <th>Device</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {lookup.devices.map((d) => (
            <tr key={d.deviceId} className="border-t border-[color:var(--border)]">
              <td className="py-2">
                {d.clientId ?? "—"} / {d.clientMode ?? "—"}
                {d.isSelf && (
                  <span className="ml-2 inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                    this is MC
                  </span>
                )}
              </td>
              <td>{d.remoteIp ?? "—"}</td>
              <td>{d.lastUsedAtMs ? formatTimestamp(new Date(d.lastUsedAtMs)) : "never"}</td>
              <td>
                <div className="flex flex-wrap gap-1">
                  {d.scopes.slice(0, 3).map((s) => (
                    <span key={s} className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                      {s}
                    </span>
                  ))}
                  {d.scopes.length > 3 && (
                    <span className="text-xs text-muted" title={d.scopes.slice(3).join(", ")}>
                      +{d.scopes.length - 3} more
                    </span>
                  )}
                </div>
              </td>
              <td className="font-mono text-xs">{d.deviceId.slice(0, 12)}…</td>
              <td>
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={d.isSelf || writeBlocked || isPending}
                  title={d.isSelf
                    ? "This is MC's own backend device — removing would lock MC out of the gateway."
                    : undefined}
                  onClick={() => setTarget(d)}
                >
                  Remove
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <ConfirmActionDialog
        open={Boolean(target)}
        onOpenChange={(open) => !open && setTarget(null)}
        title="Remove paired device?"
        body={
          target
            ? `Remove paired device ${target.deviceId.slice(0, 12)}…? The device will lose gateway access immediately. This cannot be undone.`
            : ""
        }
        confirmLabel="Confirm"
        onConfirm={onConfirm}
      />
    </div>
  );
}

export default function GatewayPairingsPage() {
  const { isSignedIn } = useAuth();
  const { isAdmin } = useOrganizationMembership(isSignedIn);
  const params = useParams();
  const gatewayIdParam = params?.gatewayId;
  const gatewayId = Array.isArray(gatewayIdParam)
    ? gatewayIdParam[0]
    : (gatewayIdParam ?? "");

  return (
    <DashboardPageLayout
      title="Gateway pairings"
      description="Inspect and revoke devices paired with this gateway."
      isAdmin={isAdmin}
      adminOnlyMessage="Only organization owners and admins can manage gateway pairings."
      signedOut={{
        message: "Sign in to manage gateway pairings.",
        forceRedirectUrl: `/gateways/${gatewayId}/pairings`,
      }}
    >
      <Suspense fallback={<div className="text-sm text-muted">Loading…</div>}>
        {gatewayId
          ? <Inner gatewayId={gatewayId} />
          : <div className="text-sm text-muted">Missing gateway id.</div>}
      </Suspense>
    </DashboardPageLayout>
  );
}
```

If `ConfirmActionDialog`'s actual prop names differ (verified in Task 8), adjust the JSX accordingly.

**Step 4 — Verify it passes + lint + typecheck:**

```bash
cd frontend && npm run test -- "gateways/\\[gatewayId\\]/pairings" && \
  npx tsc --noEmit && \
  npm run lint -- "src/app/gateways/[gatewayId]/pairings/"
```
Expected: 6 tests pass, tsc clean, lint clean.

**Step 5 — Commit:**

```bash
git add "frontend/src/app/gateways/[gatewayId]/pairings/"
git commit -m "feat(ui): add gateway pairings page with self-protect and confirm dialog"
```

---

## Task 10: Pairings button on gateway detail page

**Files:**
- Modify: `frontend/src/app/gateways/[gatewayId]/page.tsx` (insert a 3rd button in `headerActions`)
- Modify: `frontend/src/app/gateways/[gatewayId]/page.test.tsx` (append one assertion)

**Step 1 — Failing test** (append):

```tsx
it("renders a Pairings button that navigates to /gateways/<id>/pairings", () => {
  renderPage();
  const link = screen.getByRole("button", { name: /^pairings$/i });
  expect(link).toBeInTheDocument();
});
```

**Step 2 — Run to fail:**

```bash
cd frontend && npm run test -- "gateways/\\[gatewayId\\]/page"
```
Expected: `Unable to find role="button" with name /^pairings$/i`.

**Step 3 — Implement:**

In `frontend/src/app/gateways/[gatewayId]/page.tsx`, add a third button to `headerActions`, between the existing Config and "Edit gateway" buttons:

```tsx
{isAdmin && gatewayId ? (
  <Button
    variant="outline"
    onClick={() => router.push(`/gateways/${gatewayId}/pairings`)}
  >
    Pairings
  </Button>
) : null}
```

**Step 4 — Verify:**

```bash
cd frontend && npm run test -- "gateways/\\[gatewayId\\]/page" && \
  npx tsc --noEmit && \
  npm run lint -- "src/app/gateways/[gatewayId]/page.tsx"
```
Expected: existing tests + new one pass; tsc clean; lint clean.

**Step 5 — Commit:**

```bash
git add "frontend/src/app/gateways/[gatewayId]/page.tsx" \
        "frontend/src/app/gateways/[gatewayId]/page.test.tsx"
git commit -m "feat(ui): add Pairings link on gateway detail page"
```

---

## Task 11: Smoke + memory hygiene (post-deploy)

**Step 1 — Push backend PR (Tasks 1–6) and frontend PR (Tasks 7–10) separately:**

Per the config-lookup precedent — open the backend PR first, merge after CI/CD deploys to `.64`, then open the frontend PR (stacked on master after backend merges).

**Step 2 — Smoke against live `.60` gateway from `.64`:**

Get a known-stale CLI device's deviceId from the existing `device.pair.list` output (see session memory for the 17-device list). Pick one whose `lastUsedAtMs` is the OLDEST (likely a probe or one-shot CLI from months ago).

```bash
LOCAL_TOKEN=$(ssh mcontrol@192.168.2.64 \
  'grep ^LOCAL_AUTH_TOKEN /home/mcontrol/openclaw-mission-control/backend/.env | cut -d= -f2')
GATEWAY_ID="3821a85a-984c-412a-9340-cda50eaf174e"
STALE_ID="<pick from device.pair.list output>"

ssh mcontrol@192.168.2.64 \
  "curl -sS -H 'Authorization: Bearer $LOCAL_TOKEN' \
    http://127.0.0.1:8000/api/v1/gateways/$GATEWAY_ID/devices | \
   python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d[\"devices\"]),\"devices, isSelfResolved=\",d[\"isSelfResolved\"])'"

ssh mcontrol@192.168.2.64 \
  "curl -sS -X DELETE -H 'Authorization: Bearer $LOCAL_TOKEN' \
    -o /dev/null -w '%{http_code}\\n' \
    http://127.0.0.1:8000/api/v1/gateways/$GATEWAY_ID/devices/$STALE_ID"
```
Expected: list returns devices (count > 0, isSelfResolved=true); DELETE returns 200.

**Step 3 — Verify the audit log line landed:**

```bash
ssh mcontrol@192.168.2.64 \
  "sudo journalctl -u mc-backend --since '2 minutes ago' --no-pager | \
   grep gateway.pairing.remove.outcome | tail -3"
```
Expected: at least one line with `outcome=success`, `user_id=<uuid>`, `device_id=<STALE_ID>`.

**Step 4 — Smoke self-protect via API:**

```bash
# Get MC's own deviceId
ssh mcontrol@192.168.2.64 \
  "cd /home/mcontrol/openclaw-mission-control/backend && \
   /root/.local/bin/uv run python -c '
from app.services.openclaw.device_identity import load_or_create_device_identity
print(load_or_create_device_identity().device_id)' 2>/dev/null"

# Then try to delete it (should return 409)
ssh mcontrol@192.168.2.64 \
  "curl -sS -X DELETE -H 'Authorization: Bearer $LOCAL_TOKEN' \
    -o /dev/null -w '%{http_code}\\n' \
    http://127.0.0.1:8000/api/v1/gateways/$GATEWAY_ID/devices/<MC_DEVICE_ID>"
```
Expected: 409.

**Step 5 — Browser smoke (Chrome):**

Operator opens `https://mc.example.local/gateways/<id>/pairings`, sees the table populated, sees the "this is MC" pill on the row matching MC's deviceId, sees the Remove button on that row disabled with the tooltip. Click Remove on a different (stale) row, confirm the dialog body shows the truncated deviceId, click Confirm. Toast shows success; list refreshes; the removed row is gone.

**Step 6 — Memory hygiene:**

Add a new memory `project_mc_pairings_page.md` recording:
- Page route: `/gateways/<id>/pairings`
- Backend endpoints: GET/DELETE `/api/v1/gateways/<id>/devices[/<device_id>]`
- Self-protect mechanism (load_or_create_device_identity().device_id)
- Audit log line format: `gateway.pairing.remove.outcome ... outcome=<x>`
- v2 follow-ups: pending-approval flow, DB-persisted audit table, cross-gateway view

Update `MEMORY.md` index with one bullet line that markdown-links the new `project_mc_pairings_page.md`. Body (after the link): "MC pairings page at /gateways/&lt;id&gt;/pairings; calls device.pair.list/remove, self-protect via local Ed25519 identity, audit logs in journal." Match the style of the existing supersede entries in the index so the line reads as a peer of the surrounding bullets.

(Memory file lives at `/Users/macmini/.claude/projects/-Users-macmini-Workspace-Agent-openclaw-mission-control/memory/` — the auto-memory system persists it.)

---

## Dependencies between tasks

```
Task 1 (probe) ──────────────┐
                             │
Task 2 (mapper refactor) ────┤
                             ▼
                    Task 3 (schemas + projection)
                             │
                             ▼
                    Task 4 (GET handler)
                             │
                             ▼
                    Task 5 (DELETE handler)
                             │
                             ▼
                    Task 6 (audit logging)
                             │
                             ▼
                    Backend PR (Tasks 1–6) → CI/CD → .64
                             │
                             ▼
                    Task 7 (regen API client)
                             │
                             ▼
                    Task 8 (verify ConfirmActionDialog)
                             │
                             ▼
                    Task 9 (frontend page)
                             │
                             ▼
                    Task 10 (Pairings button)
                             │
                             ▼
                    Frontend PR (Tasks 7–10) → CI/CD → .64
                             │
                             ▼
                    Task 11 (smoke + memory)
```

## Verification rollup (run after every backend task)

```bash
cd backend && uv run pytest tests/test_gateway_devices_api.py tests/test_gateway_devices_schema.py tests/test_gateway_error_mapper.py tests/test_gateway_config_lookup_api.py -q
```

## Verification rollup (run after every frontend task)

```bash
cd frontend && npx tsc --noEmit && \
  npm run lint -- "src/app/gateways/[gatewayId]/pairings/" "src/app/gateways/[gatewayId]/page.tsx" && \
  npx vitest run src/app/gateways src/components/ConfigReloadKindBadge
```

## Out of scope (reaffirmed from design)

- Pending-approval flow (`device.pair.approve` / `.reject`)
- DB-persisted revoke audit table (subscribe to `device.pair.resolved` events)
- Cross-gateway view at `/admin/pairings`
- Bulk-revoke / age-filtered auto-purge
- Token rotation
- Public-key fingerprint display

## Verified shapes (from Task 1 probe — 2026-05-23)

- **Accepted params:** `{"deviceId": "<hex>"}` (schema-accepted; returned the not-found path for the bogus id)
- **Not-found error:** `code='INVALID_REQUEST', message='unknown deviceId'` (exact strings from the probe output)
- **Other shapes:** `{"id": ...}` and `{"device": ...}` both returned `code='INVALID_REQUEST', message="invalid device.pair.remove params: must have required property 'deviceId'; at root: unexpected property '<id|device>'"` — schema-validation failures before any lookup
- **Pairing-scope denied:** NOT observed in probe (MC has operator.pairing). DELETE handler tests use the canonical INVALID_REQUEST + "insufficient scope" pattern; if the live shape ever differs, a real-prod failure will surface it.

These verified strings are used by Task 5's substring matchers in `_map_pairing_error` (`"device not found"` / `"unknown device"`) — update those matchers if the real strings don't match either substring. **Note:** the live message is `"unknown deviceId"` (camelCase, not `"unknown device"`), so the `"unknown device"` substring matcher will still match (substring-contains), but the more precise check is `"unknown deviceid"` (case-insensitive) or just `"unknown device"`.
