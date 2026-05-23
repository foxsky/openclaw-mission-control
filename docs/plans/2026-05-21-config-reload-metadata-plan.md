# Gateway config reload-metadata path inspector — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` to implement this plan task-by-task. Each task is TDD-shaped: failing test → run-to-fail → minimal impl → run-to-pass → commit.

**Goal:** Add a read-only "Config schema lookup" page to MC that surfaces OpenClaw's per-path `reloadKind` (`restart` / `hot` / `none`) so operators stop guessing whether `openclaw config set` needs a gateway restart.

**Architecture:** New thin FastAPI handler in `backend/app/api/gateway.py` proxies a single gateway WS RPC (`config.schema.lookup`) through `openclaw_call`, with 30s in-process singleflight TTL cache. Frontend uses orval-generated React Query hook to render schema, breadcrumbs, badges, and clickable children. No DB, no plugin, no `.60` changes.

**Tech Stack:** FastAPI · SQLModel · Pydantic v2 · OpenClaw WS RPC client (`gateway_rpc.openclaw_call`) · Next.js App Router (client component) · React Query · orval · Tailwind · pytest (httpx ASGITransport) · vitest

**Design doc:** `docs/plans/2026-05-21-config-reload-metadata-design.md`

**Branch / worktree:** `feat/config-reload-inspector` at `.worktrees/config-reload-inspector/`

---

## Pre-flight (run once before Task 1)

```bash
cd .worktrees/config-reload-inspector
# Backend deps
cd backend && uv sync --extra dev && cd ..
# Frontend deps
cd frontend && npm install && cd ..
# Baseline backend tests (should pass on master)
cd backend && uv run pytest -x -q
```
Expected: all pre-existing tests pass. If anything is red, stop and ask before continuing — don't paper over pre-existing failures.

---

## Task 1: Response schemas — `ConfigSchemaLookupResponse`, `ConfigSchemaLookupChild`

**Files:**
- Modify: `backend/app/schemas/gateway_api.py` (append at end of file)
- Test: `backend/tests/test_gateway_config_lookup_schema.py` (new)

**Why first:** Pure unit, no I/O, validates Pydantic alias round-trip before any handler depends on it.

**Step 1 — Failing test:**

Create `backend/tests/test_gateway_config_lookup_schema.py`:

```python
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
```

**Step 2 — Verify it fails:**

```bash
cd backend && uv run pytest tests/test_gateway_config_lookup_schema.py -v
```
Expected: `ImportError: cannot import name 'ConfigSchemaLookupChild' from 'app.schemas.gateway_api'`.

**Step 3 — Implement:**

Append to `backend/app/schemas/gateway_api.py`:

```python
class ConfigSchemaLookupChild(SQLModel):
    """One direct child of a config schema path (returned by config.schema.lookup)."""

    path: str
    reload_kind: str | None = Field(default=None, alias="reloadKind")
    hint: str | None = None

    model_config = SQLModelConfig(validate_by_name=True)


class ConfigSchemaLookupResponse(SQLModel):
    """Read-only gateway config schema lookup result.

    `reload_kind` is passed through unchanged from `resolveConfigReloadMetadata`
    so future gateway values land in the UI without a backend release.
    """

    gateway_id: UUID
    path: str
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    reload_kind: str | None = Field(default=None, alias="reloadKind")
    hint: str | None = None
    hint_path: str | None = Field(default=None, alias="hintPath")
    children: list[ConfigSchemaLookupChild] = Field(default_factory=list)

    model_config = SQLModelConfig(validate_by_name=True)
```

Add the imports at the top of the same file if missing:

```python
from typing import Any
from uuid import UUID
from pydantic import Field
from sqlmodel._compat import SQLModelConfig
```

(Note: `gateway_api.py` imports `Field` and `UUID` lazily today — check `head -15 backend/app/schemas/gateway_api.py` first and only add the imports that are not already there.)

**Step 4 — Verify it passes:**

```bash
cd backend && uv run pytest tests/test_gateway_config_lookup_schema.py -v
```
Expected: 3 passed.

**Step 5 — Commit:**

```bash
git add backend/app/schemas/gateway_api.py backend/tests/test_gateway_config_lookup_schema.py
git commit -m "feat(api): add ConfigSchemaLookupResponse schema for config.schema.lookup"
```

---

## Task 2: Path validation helper

**Files:**
- Modify: `backend/app/api/gateway.py` (add private helper near top, below `BOARD_ID_QUERY`)
- Test: `backend/tests/test_gateway_config_lookup_path_validation.py` (new)

**Why second:** Pure function, no gateway I/O, isolates the cheap-reject layer so later handler tests don't have to retest these cases.

**Step 1 — Failing test:**

```python
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
```

**Step 2 — Verify it fails:**

```bash
cd backend && uv run pytest tests/test_gateway_config_lookup_path_validation.py -v
```
Expected: `ImportError: cannot import name '_validate_config_lookup_path'`.

**Step 3 — Implement** in `backend/app/api/gateway.py` (place after `BOARD_ID_QUERY = Query(default=None)` near line 48):

```python
_MAX_CONFIG_LOOKUP_PATH_LEN = 512


def _validate_config_lookup_path(raw: str) -> str:
    """Cheap pre-validation; lets the gateway parser be authoritative on grammar.

    Rejects only empty/oversize/control-char input so the WS RPC never sees
    obviously-bad payloads. Bracket-quoted keys, dotted paths, and the root
    sentinel `.` all pass through unchanged.
    """
    trimmed = raw.strip()
    if not trimmed or len(trimmed) > _MAX_CONFIG_LOOKUP_PATH_LEN:
        raise HTTPException(status_code=400, detail={"error": "invalid_path"})
    if any(ord(ch) < 0x20 for ch in trimmed):
        raise HTTPException(status_code=400, detail={"error": "invalid_path"})
    return trimmed
```

Add `from fastapi import HTTPException` to the existing fastapi import line if not present.

**Step 4 — Verify it passes:**

```bash
cd backend && uv run pytest tests/test_gateway_config_lookup_path_validation.py -v
```
Expected: 9 passed.

**Step 5 — Commit:**

```bash
git add backend/app/api/gateway.py backend/tests/test_gateway_config_lookup_path_validation.py
git commit -m "feat(api): add config-lookup path validator (length + control chars)"
```

---

## Task 3: Singleflight TTL cache for `(gateway_id, path)`

**Files:**
- Create: `backend/app/services/openclaw/config_lookup_cache.py`
- Test: `backend/tests/test_config_lookup_cache.py`

**Why third:** Standalone reusable primitive — handler imports it once everything else is settled. Singleflight is the trickiest part; isolate it with deterministic tests.

**Step 1 — Failing test:**

```python
# ruff: noqa: INP001
"""Unit tests for the gateway config-lookup TTL singleflight cache."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.services.openclaw.config_lookup_cache import ConfigLookupCache


@pytest.mark.asyncio
async def test_first_call_invokes_loader() -> None:
    cache = ConfigLookupCache(ttl_seconds=30.0)
    gateway_id = uuid4()
    calls = 0

    async def _load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"reloadKind": "restart"}

    result = await cache.get_or_load((gateway_id, "agents"), _load)

    assert result == {"reloadKind": "restart"}
    assert calls == 1


@pytest.mark.asyncio
async def test_cache_hit_within_ttl_skips_loader() -> None:
    cache = ConfigLookupCache(ttl_seconds=30.0)
    gateway_id = uuid4()
    calls = 0

    async def _load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"v": calls}

    first = await cache.get_or_load((gateway_id, "agents"), _load)
    second = await cache.get_or_load((gateway_id, "agents"), _load)

    assert first == second == {"v": 1}
    assert calls == 1


@pytest.mark.asyncio
async def test_singleflight_concurrent_calls_share_loader() -> None:
    cache = ConfigLookupCache(ttl_seconds=30.0)
    gateway_id = uuid4()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def _load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"v": calls}

    task_a = asyncio.create_task(cache.get_or_load((gateway_id, "p"), _load))
    await started.wait()
    task_b = asyncio.create_task(cache.get_or_load((gateway_id, "p"), _load))
    await asyncio.sleep(0)  # let task_b register on the same future
    release.set()

    a, b = await asyncio.gather(task_a, task_b)
    assert a == b == {"v": 1}
    assert calls == 1


@pytest.mark.asyncio
async def test_loader_exception_is_not_cached() -> None:
    cache = ConfigLookupCache(ttl_seconds=30.0)
    gateway_id = uuid4()
    calls = 0

    async def _failing() -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await cache.get_or_load((gateway_id, "agents"), _failing)

    async def _ok() -> dict[str, object]:
        return {"ok": True}

    result = await cache.get_or_load((gateway_id, "agents"), _ok)
    assert result == {"ok": True}
    assert calls == 1  # the failing loader ran once; subsequent _ok ran independently


@pytest.mark.asyncio
async def test_ttl_expiry_refetches() -> None:
    cache = ConfigLookupCache(ttl_seconds=0.0)  # instant expiry
    gateway_id = uuid4()
    calls = 0

    async def _load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"v": calls}

    first = await cache.get_or_load((gateway_id, "agents"), _load)
    second = await cache.get_or_load((gateway_id, "agents"), _load)
    assert first == {"v": 1}
    assert second == {"v": 2}
```

**Step 2 — Verify it fails:**

```bash
cd backend && uv run pytest tests/test_config_lookup_cache.py -v
```
Expected: `ImportError: No module named 'app.services.openclaw.config_lookup_cache'`.

**Step 3 — Implement:**

Create `backend/app/services/openclaw/config_lookup_cache.py`:

```python
"""In-process singleflight TTL cache for gateway config schema lookups.

Schema is near-static at gateway runtime, but the inspector page hits the
endpoint per keystroke (debounced) and per breadcrumb click. Without this
cache every request opens a fresh WebSocket (gateway_rpc.py:606-609).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class _Entry:
    value: object
    expires_at: float


class ConfigLookupCache:
    """Per-key TTL cache with singleflight semantics."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._values: dict[tuple[UUID, str], _Entry] = {}
        self._inflight: dict[tuple[UUID, str], asyncio.Future[object]] = {}
        self._lock = asyncio.Lock()

    async def get_or_load(
        self,
        key: tuple[UUID, str],
        loader: Callable[[], Awaitable[object]],
    ) -> object:
        now = time.monotonic()
        async with self._lock:
            entry = self._values.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value
            inflight = self._inflight.get(key)
            if inflight is None:
                inflight = asyncio.get_running_loop().create_future()
                self._inflight[key] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            return await asyncio.shield(inflight)

        try:
            value = await loader()
        except BaseException as exc:  # noqa: BLE001 — propagate to all waiters
            async with self._lock:
                self._inflight.pop(key, None)
            if not inflight.done():
                inflight.set_exception(exc)
            raise

        async with self._lock:
            self._values[key] = _Entry(value=value, expires_at=time.monotonic() + self._ttl)
            self._inflight.pop(key, None)
        if not inflight.done():
            inflight.set_result(value)
        return value
```

**Step 4 — Verify it passes:**

```bash
cd backend && uv run pytest tests/test_config_lookup_cache.py -v
```
Expected: 5 passed.

**Step 5 — Commit:**

```bash
git add backend/app/services/openclaw/config_lookup_cache.py backend/tests/test_config_lookup_cache.py
git commit -m "feat(openclaw): add singleflight TTL cache for config lookups"
```

---

## Task 4: Handler — happy path + cache wiring

**Files:**
- Modify: `backend/app/api/gateway.py` (add handler + module-level cache singleton)
- Test: `backend/tests/test_gateway_config_lookup_api.py` (new, multiple test functions across tasks 4–6)

**Why fourth:** Smallest end-to-end vertical slice; subsequent tasks layer error mapping and version preflight on top.

**Step 1 — Failing test:**

```python
# ruff: noqa: INP001
"""Integration tests for /api/v1/gateways/{id}/config/lookup."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import gateway as gateway_api
from app.api.deps import require_org_admin
from app.api.gateway import router as gateway_router
from app.db.session import get_session
from app.models.gateways import Gateway
from app.models.organization_members import OrganizationMember
from app.models.organizations import Organization
from app.services.openclaw.gateway_rpc import OpenClawGatewayError
from app.services.organizations import OrganizationContext


def _build_app(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    organization: Organization,
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
                organization_id=organization.id,
                user_id=uuid4(),
                role="owner",
                all_boards_read=True,
                all_boards_write=True,
            ),
        )

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[require_org_admin] = _override_require_org_admin
    return app


@pytest_asyncio.fixture
async def setup() -> AsyncIterator[tuple[FastAPI, Organization, Gateway]]:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    org = Organization(id=uuid4(), name="Org One")
    gateway = Gateway(
        id=uuid4(),
        organization_id=org.id,
        name="Gateway One",
        url="https://gateway.example.local",
        workspace_root="/workspace/openclaw",
    )
    async with session_maker() as session:
        session.add(org)
        session.add(gateway)
        await session.commit()

    app = _build_app(session_maker, organization=org)
    try:
        yield app, org, gateway
    finally:
        await engine.dispose()


def _reset_cache() -> None:
    gateway_api._CONFIG_LOOKUP_CACHE = type(gateway_api._CONFIG_LOOKUP_CACHE)(ttl_seconds=30.0)


@pytest.mark.asyncio
async def test_happy_path_returns_schema_and_badges(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    captured: list[tuple[str, Any]] = []

    async def _fake_openclaw_call(method: str, params: Any = None, *, config: Any) -> object:
        captured.append((method, params))
        return {
            "path": "agents.defaults.models",
            "schema": {"type": "object"},
            "reloadKind": "restart",
            "hint": "Restart required.",
            "hintPath": "agents.defaults.models",
            "children": [
                {"path": "agents.defaults.models.foo", "reloadKind": "hot"},
            ],
        }

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake_openclaw_call)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents.defaults.models"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["gateway_id"] == str(gateway.id)
    assert body["reloadKind"] == "restart"
    assert body["children"][0]["reloadKind"] == "hot"
    assert captured == [("config.schema.lookup", {"path": "agents.defaults.models"})]


@pytest.mark.asyncio
async def test_cache_singleflight_single_invocation_within_ttl(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    calls = 0

    async def _fake_openclaw_call(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal calls
        calls += 1
        return {"path": "p", "schema": {}, "reloadKind": "none", "children": []}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake_openclaw_call)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v1/gateways/{gateway.id}/config/lookup"
        r1 = await client.get(url, params={"path": "agents"})
        r2 = await client.get(url, params={"path": "agents"})

    assert r1.status_code == r2.status_code == 200
    assert calls == 1


@pytest.mark.asyncio
async def test_invalid_path_short_circuits_before_rpc(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    called = False

    async def _fake_openclaw_call(method: str, params: Any = None, *, config: Any) -> object:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake_openclaw_call)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "\x00bad"},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_path"
    assert called is False
```

**Step 2 — Verify it fails:**

```bash
cd backend && uv run pytest tests/test_gateway_config_lookup_api.py -v
```
Expected: `AttributeError: module 'app.api.gateway' has no attribute '_CONFIG_LOOKUP_CACHE'` (and/or 404 from missing route).

**Step 3 — Implement** in `backend/app/api/gateway.py`:

Add at the top of the imports block:

```python
from app.services.openclaw.config_lookup_cache import ConfigLookupCache
from app.services.openclaw.gateway_rpc import OpenClawGatewayError, openclaw_call
from app.services.openclaw.gateway_resolver import resolve_gateway_config  # if existing helper; else build inline
```

Then below `BOARD_ID_QUERY` (≈ line 48):

```python
_CONFIG_LOOKUP_CACHE = ConfigLookupCache(ttl_seconds=30.0)
_CONFIG_LOOKUP_RPC_TIMEOUT_SECONDS = 5.0
```

Add the handler at the end of the file:

```python
@router.get(
    "/{gateway_id}/config/lookup",
    response_model=ConfigSchemaLookupResponse,
    operation_id="gateway_config_lookup",
)
async def gateway_config_lookup(
    gateway_id: UUID,
    path: str = Query(..., min_length=1, max_length=512),
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> ConfigSchemaLookupResponse:
    """Look up gateway config schema + reload metadata for a single path."""

    del auth  # only needed to enforce the dependency
    trimmed_path = _validate_config_lookup_path(path)

    gateway_record = await GatewayAdminLifecycleService(session).require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    cfg = _build_gateway_config(gateway_record)  # use existing helper / pattern

    async def _load() -> object:
        return await asyncio.wait_for(
            openclaw_call(
                "config.schema.lookup",
                {"path": trimmed_path},
                config=cfg,
            ),
            timeout=_CONFIG_LOOKUP_RPC_TIMEOUT_SECONDS,
        )

    payload = await _CONFIG_LOOKUP_CACHE.get_or_load(
        (gateway_id, trimmed_path), _load,
    )

    return ConfigSchemaLookupResponse.model_validate(
        {**payload, "gateway_id": gateway_id},
    )
```

Add `import asyncio` and the imports for `GatewayAdminLifecycleService`, `ConfigSchemaLookupResponse`, `UUID`. Use whichever helper `gateway.py` already uses to build a `GatewayConfig` from a `Gateway` row — search `git grep "GatewayConfig(" backend/app/services backend/app/api` and reuse, otherwise inline construction from `gateway_record.url` + workspace_root following `provisioning.py`. **Do NOT invent a new helper if one already exists.**

> **Subagent note:** Before writing code, do `git grep -n "GatewayConfig(" backend/app` and pick the most idiomatic existing call-site to mirror. If none fits, ask the controller.

**Step 4 — Verify happy + cache + invalid path pass:**

```bash
cd backend && uv run pytest tests/test_gateway_config_lookup_api.py -v -k "happy or cache_singleflight or invalid_path"
```
Expected: 3 passed (error-mapping tests in Task 5 still failing, that's fine — only run the matching `-k` here).

**Step 5 — Commit:**

```bash
git add backend/app/api/gateway.py backend/tests/test_gateway_config_lookup_api.py
git commit -m "feat(api): add /gateways/{id}/config/lookup endpoint (happy path + cache)"
```

---

## Task 5: Handler — error mapping

**Files:**
- Modify: `backend/app/api/gateway.py` (wrap the loader call in try/except)
- Modify: `backend/tests/test_gateway_config_lookup_api.py` (append error-case tests)

**Step 1 — Failing tests (append to existing file):**

```python
@pytest.mark.asyncio
async def test_path_not_found_returns_404(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError(
            "config schema path not found",
            details={"code": "INVALID_REQUEST", "message": "config schema path not found"},
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "no.such.path"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "path_not_found"
    assert resp.json()["detail"]["path"] == "no.such.path"


@pytest.mark.asyncio
async def test_other_invalid_request_returns_422(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError(
            "bad shape",
            details={"code": "INVALID_REQUEST", "message": "config schema lookup returned invalid payload"},
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "gateway_rejected_request"


@pytest.mark.asyncio
async def test_method_not_supported_returns_501(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError(
            "method not found",
            details={"code": "METHOD_NOT_FOUND", "message": "unknown method config.schema.lookup"},
        )

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 501
    assert resp.json()["detail"]["error"] == "method_unsupported"
    assert resp.json()["detail"]["requires_gateway_version"] == "2026.5.19"


@pytest.mark.asyncio
async def test_unavailable_returns_503(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        raise OpenClawGatewayError("down", details={"code": "UNAVAILABLE"})

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "gateway_unavailable"


@pytest.mark.asyncio
async def test_timeout_returns_504(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio as _asyncio

    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        await _asyncio.sleep(10)  # exceeds the 5s wait_for in handler

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)
    monkeypatch.setattr(gateway_api, "_CONFIG_LOOKUP_RPC_TIMEOUT_SECONDS", 0.05)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 504
    assert resp.json()["detail"]["error"] == "gateway_timeout"


@pytest.mark.asyncio
async def test_other_gateway_id_returns_404(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _ = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {}

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{uuid4()}/config/lookup",
            params={"path": "agents"},
        )
    assert resp.status_code == 404
```

**Step 2 — Verify they fail:**

```bash
cd backend && uv run pytest tests/test_gateway_config_lookup_api.py -v -k "404 or 422 or 501 or 503 or 504"
```
Expected: 6 failing (most likely 500 with unhandled `OpenClawGatewayError` / `TimeoutError`).

**Step 3 — Implement** — wrap the loader-call in `gateway_config_lookup` (replace the `payload = await _CONFIG_LOOKUP_CACHE.get_or_load(...)` block with this try/except):

```python
try:
    payload = await _CONFIG_LOOKUP_CACHE.get_or_load(
        (gateway_id, trimmed_path), _load,
    )
except asyncio.TimeoutError as exc:
    raise HTTPException(
        status_code=504, detail={"error": "gateway_timeout"},
    ) from exc
except OpenClawGatewayError as exc:
    raise _map_gateway_error(exc, trimmed_path) from exc
```

And add the helper just above the handler:

```python
_GATEWAY_METHOD_REQUIRED_VERSION = "2026.5.19"


def _map_gateway_error(exc: OpenClawGatewayError, path: str) -> HTTPException:
    details: dict[str, Any] = exc.details if isinstance(exc.details, dict) else {}
    code = str(details.get("code") or "").upper()
    message = str(details.get("message") or str(exc) or "")

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
    if code in {"METHOD_NOT_FOUND", "METHOD_NOT_REGISTERED", "NOT_IMPLEMENTED"} or "method not found" in message.lower() or "unknown method" in message.lower():
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

Note: `gateway_rpc.openclaw_call` wraps `TimeoutError` as `OpenClawGatewayError(str(exc))` (line 692). The bare `asyncio.wait_for` raise happens *outside* `openclaw_call`, so it lands in the `asyncio.TimeoutError` branch directly.

**Step 4 — Verify all error tests pass + previous tests still pass:**

```bash
cd backend && uv run pytest tests/test_gateway_config_lookup_api.py -v
```
Expected: 9 passed (3 from Task 4 + 6 error mappings).

**Step 5 — Commit:**

```bash
git add backend/app/api/gateway.py backend/tests/test_gateway_config_lookup_api.py
git commit -m "feat(api): map gateway errors to HTTP (404/422/501/503/504) for config lookup"
```

---

## Task 6: Pass-through fidelity regression guard + unknown reloadKind test

**Files:**
- Modify: `backend/tests/test_gateway_config_lookup_api.py` (append one final test)

**Step 1 — Failing test:**

```python
@pytest.mark.asyncio
async def test_future_reload_kind_passes_through(
    setup: tuple[FastAPI, Organization, Gateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: don't tighten reload_kind to a Literal[...] later."""

    app, _, gateway = setup
    _reset_cache()

    async def _fake(method: str, params: Any = None, *, config: Any) -> object:
        return {
            "path": ".",
            "schema": {},
            "reloadKind": "warm-restart-future",
            "children": [],
        }

    monkeypatch.setattr(gateway_api, "openclaw_call", _fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/gateways/{gateway.id}/config/lookup",
            params={"path": "."},
        )
    assert resp.status_code == 200
    assert resp.json()["reloadKind"] == "warm-restart-future"
```

**Step 2 — Verify it passes** (no code change should be needed if Task 1 was done right):

```bash
cd backend && uv run pytest tests/test_gateway_config_lookup_api.py::test_future_reload_kind_passes_through -v
```
Expected: passed. If it fails, the schema was tightened — go back to Task 1.

**Step 3 — Run full backend suite:**

```bash
cd backend && uv run pytest -q
```
Expected: 0 new failures vs. master baseline.

**Step 4 — Commit:**

```bash
git add backend/tests/test_gateway_config_lookup_api.py
git commit -m "test(api): guard against tightening reload_kind to Literal[...]"
```

---

## Task 7: Regenerate orval-typed API client

**Files:**
- Run: `make api-gen` (regenerates `frontend/src/api/generated/gateways/gateways.ts`)
- Modify: nothing manually — generated diff only

**Step 1 — Run:**

```bash
cd /Users/macmini/Workspace/Agent/openclaw-mission-control/.worktrees/config-reload-inspector
# Backend must be importable; api-gen reads the live OpenAPI from a running server OR a static schema file —
# check `frontend/orval.config.ts` to confirm the source. If it needs a live server:
cd backend && uvicorn app.main:app --reload --port 8000 &  # in another shell, or:
# Otherwise run the offline generator that orval points at.
make api-gen
```

If the orval config requires a live server, start the backend with the test env (`AUTH_MODE=local LOCAL_AUTH_TOKEN=… uvicorn app.main:app …`) and then re-run `make api-gen`. The new hook will be named `useGatewayConfigLookupApiV1GatewaysGatewayIdConfigLookupGet`.

**Step 2 — Verify the hook appears:**

```bash
grep -n "useGatewayConfigLookupApiV1GatewaysGatewayIdConfigLookupGet" frontend/src/api/generated/gateways/gateways.ts
```
Expected: at least one match.

**Step 3 — Type-check the frontend:**

```bash
cd frontend && npm run typecheck
```
Expected: 0 errors.

**Step 4 — Commit:**

```bash
git add frontend/src/api/generated
git commit -m "chore(api-client): regenerate after gateway config lookup endpoint"
```

> **Subagent note:** If the generated diff includes unrelated drift (other endpoints renamed), STOP and ask the controller — that's a pre-existing config issue, not part of this task.

---

## Task 8: Frontend badge component

**Files:**
- Create: `frontend/src/components/ConfigReloadKindBadge.tsx`
- Create: `frontend/src/components/ConfigReloadKindBadge.test.tsx`

**Step 1 — Failing vitest:**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConfigReloadKindBadge } from "./ConfigReloadKindBadge";

describe("ConfigReloadKindBadge", () => {
  it("renders 'Restart required' for restart kind", () => {
    render(<ConfigReloadKindBadge reloadKind="restart" />);
    expect(screen.getByText("Restart required")).toBeInTheDocument();
  });

  it("renders 'Hot reload' for hot kind", () => {
    render(<ConfigReloadKindBadge reloadKind="hot" />);
    expect(screen.getByText("Hot reload")).toBeInTheDocument();
  });

  it("renders 'No-op' for none kind", () => {
    render(<ConfigReloadKindBadge reloadKind="none" />);
    expect(screen.getByText("No-op")).toBeInTheDocument();
  });

  it("renders em dash with explanation tooltip for missing kind", () => {
    render(<ConfigReloadKindBadge reloadKind={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(
      screen.getByTitle("Gateway didn't report restart impact for this path."),
    ).toBeInTheDocument();
  });

  it("renders the raw kind label verbatim for an unknown value", () => {
    render(<ConfigReloadKindBadge reloadKind="warm-restart-future" />);
    expect(screen.getByText("warm-restart-future")).toBeInTheDocument();
  });
});
```

**Step 2 — Verify it fails:**

```bash
cd frontend && npm run test -- ConfigReloadKindBadge
```
Expected: `Cannot find module './ConfigReloadKindBadge'`.

**Step 3 — Implement** `frontend/src/components/ConfigReloadKindBadge.tsx`:

```tsx
import { clsx } from "clsx";

type KnownKind = "restart" | "hot" | "none";

const KNOWN: Record<KnownKind, { label: string; className: string }> = {
  restart: {
    label: "Restart required",
    className: "bg-red-100 text-red-900 border border-red-200",
  },
  hot: {
    label: "Hot reload",
    className: "bg-emerald-100 text-emerald-900 border border-emerald-200",
  },
  none: {
    label: "No-op",
    className: "bg-zinc-100 text-zinc-700 border border-zinc-200",
  },
};

export interface ConfigReloadKindBadgeProps {
  reloadKind: string | null | undefined;
  className?: string;
}

export function ConfigReloadKindBadge({
  reloadKind,
  className,
}: ConfigReloadKindBadgeProps) {
  if (reloadKind === null || reloadKind === undefined) {
    return (
      <span
        className={clsx(
          "inline-flex items-center rounded px-2 py-0.5 text-xs text-muted",
          className,
        )}
        title="Gateway didn't report restart impact for this path."
      >
        —
      </span>
    );
  }

  const known = KNOWN[reloadKind as KnownKind];
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium",
        known?.className ?? "bg-zinc-100 text-zinc-700 border border-zinc-200",
        className,
      )}
    >
      {known?.label ?? reloadKind}
    </span>
  );
}
```

**Step 4 — Verify it passes:**

```bash
cd frontend && npm run test -- ConfigReloadKindBadge
```
Expected: 5 passed.

**Step 5 — Commit:**

```bash
git add frontend/src/components/ConfigReloadKindBadge.tsx frontend/src/components/ConfigReloadKindBadge.test.tsx
git commit -m "feat(ui): ConfigReloadKindBadge for restart/hot/none/—/unknown"
```

---

## Task 9: Frontend page — `/gateways/[gatewayId]/config`

**Files:**
- Create: `frontend/src/app/gateways/[gatewayId]/config/page.tsx`
- Create: `frontend/src/app/gateways/[gatewayId]/config/page.test.tsx` (or `.spec.tsx` per repo convention)

**Step 1 — Failing vitest:**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/generated/gateways/gateways", () => ({
  useGatewayConfigLookupApiV1GatewaysGatewayIdConfigLookupGet: vi.fn(() => ({
    data: {
      gateway_id: "gw-1",
      path: "agents.defaults.models",
      schema: { type: "object" },
      reloadKind: "restart",
      hint: "Restart required.",
      hintPath: "agents.defaults.models",
      children: [
        { path: "agents.defaults.models.foo", reloadKind: "hot" },
      ],
    },
    isLoading: false,
    error: null,
  })),
}));

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: pushMock, push: pushMock }),
  useParams: () => ({ gatewayId: "gw-1" }),
  useSearchParams: () => new URLSearchParams("path=agents.defaults.models"),
  usePathname: () => "/gateways/gw-1/config",
}));

import GatewayConfigPage from "./page";

function renderPage() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <GatewayConfigPage />
    </QueryClientProvider>,
  );
}

describe("GatewayConfigPage", () => {
  it("renders the badge for the current path", () => {
    renderPage();
    expect(screen.getByText("Restart required")).toBeInTheDocument();
  });

  it("renders child rows with their own badges", () => {
    renderPage();
    expect(
      screen.getByText("agents.defaults.models.foo"),
    ).toBeInTheDocument();
    expect(screen.getByText("Hot reload")).toBeInTheDocument();
  });

  it("clicking a child row navigates with new ?path query", async () => {
    renderPage();
    await userEvent.click(screen.getByText("agents.defaults.models.foo"));
    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith(
        expect.stringContaining("path=agents.defaults.models.foo"),
        expect.objectContaining({ scroll: false }),
      );
    });
  });
});
```

**Step 2 — Verify it fails:**

```bash
cd frontend && npm run test -- gateways/\[gatewayId\]/config
```
Expected: `Cannot find module './page'`.

**Step 3 — Implement** `frontend/src/app/gateways/[gatewayId]/config/page.tsx`:

```tsx
"use client";

import { Suspense } from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";

import { ConfigReloadKindBadge } from "@/components/ConfigReloadKindBadge";
import { useGatewayConfigLookupApiV1GatewaysGatewayIdConfigLookupGet as useConfigLookup } from "@/api/generated/gateways/gateways";

export const dynamic = "force-dynamic";

function Inner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = useParams();
  const gatewayIdParam = params?.gatewayId;
  const gatewayId = Array.isArray(gatewayIdParam) ? gatewayIdParam[0] : gatewayIdParam ?? "";

  const path = searchParams.get("path") ?? ".";

  const { data, isLoading, error } = useConfigLookup(gatewayId, { path });

  const goTo = (nextPath: string) => {
    const next = new URLSearchParams(searchParams.toString());
    next.set("path", nextPath);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  if (isLoading) {
    return <div className="text-sm text-muted">Loading…</div>;
  }
  if (error) {
    return <ErrorPanel error={error} />;
  }
  if (!data) {
    return <div className="text-sm text-muted">No data.</div>;
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <Breadcrumbs path={data.path} onJump={goTo} />
        <ConfigReloadKindBadge reloadKind={data.reloadKind ?? null} />
      </header>

      <section className="grid grid-cols-2 gap-4">
        <div className="rounded border border-[color:var(--border)] p-3 text-sm">
          <h3 className="font-medium">Schema</h3>
          <pre className="mt-2 overflow-auto text-xs">{JSON.stringify(data.schema, null, 2)}</pre>
        </div>
        <div className="rounded border border-[color:var(--border)] p-3 text-sm">
          <h3 className="font-medium">Hint</h3>
          <p className="mt-2 text-muted">{data.hint ?? "—"}</p>
        </div>
      </section>

      <section>
        <h3 className="font-medium">Children ({data.children.length})</h3>
        <ul className="mt-2 divide-y divide-[color:var(--border)]">
          {data.children.map((child) => (
            <li
              key={child.path}
              className="flex cursor-pointer items-center justify-between py-2 hover:bg-zinc-50"
              onClick={() => goTo(child.path)}
            >
              <span className="text-sm">{child.path}</span>
              <ConfigReloadKindBadge reloadKind={child.reloadKind ?? null} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Breadcrumbs({ path, onJump }: { path: string; onJump: (p: string) => void }) {
  const segments = path === "." ? ["."] : ["."].concat(path.split("."));
  return (
    <nav className="flex items-center gap-1 text-sm">
      {segments.map((seg, i) => {
        const target = i === 0 ? "." : segments.slice(1, i + 1).join(".");
        return (
          <span key={target} className="flex items-center gap-1">
            <button className="hover:underline" onClick={() => onJump(target)}>
              {seg}
            </button>
            {i < segments.length - 1 && <span className="text-muted">›</span>}
          </span>
        );
      })}
    </nav>
  );
}

function ErrorPanel({ error }: { error: unknown }) {
  const status = (error as { response?: { status?: number } })?.response?.status;
  if (status === 400) {
    return <div className="text-sm text-red-700">Invalid path.</div>;
  }
  if (status === 404) {
    return <div className="text-sm text-muted">Path not found in current gateway schema.</div>;
  }
  if (status === 501) {
    return (
      <div className="text-sm text-amber-800">
        This gateway is too old. Upgrade to OpenClaw 2026.5.19 to use the schema lookup.
      </div>
    );
  }
  return <div className="text-sm text-red-700">Gateway unreachable.</div>;
}

export default function GatewayConfigPage() {
  return (
    <Suspense fallback={<div className="text-sm text-muted">Loading…</div>}>
      <Inner />
    </Suspense>
  );
}
```

**Step 4 — Verify it passes:**

```bash
cd frontend && npm run test -- gateways/\[gatewayId\]/config
```
Expected: 3 passed.

**Step 5 — Commit:**

```bash
git add frontend/src/app/gateways/\[gatewayId\]/config/
git commit -m "feat(ui): add config schema lookup page with breadcrumbs + child drill-in"
```

---

## Task 10: "Config" tab on gateway detail page

**Files:**
- Modify: `frontend/src/app/gateways/[gatewayId]/page.tsx` (add a single link/tab)
- Modify: existing test for the gateway detail page if one exists, otherwise add one targeted assertion in a new co-located test file

**Step 1 — Read the existing tab implementation:**

```bash
grep -n "tab\|TabList\|nav\|Link" frontend/src/app/gateways/\[gatewayId\]/page.tsx | head
```

Identify the existing navigation pattern (likely a flexbox row of `<Link>`s).

**Step 2 — Failing test** (`frontend/src/app/gateways/[gatewayId]/page.test.tsx`):

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Mock the data-fetching hooks the page uses so we focus on the nav assertion.
vi.mock("next/navigation", () => ({
  useParams: () => ({ gatewayId: "gw-1" }),
}));

import GatewayDetailPage from "./page";

describe("GatewayDetailPage", () => {
  it("has a Config tab that links to /gateways/<id>/config", () => {
    render(<GatewayDetailPage />);
    const link = screen.getByRole("link", { name: /config/i });
    expect(link.getAttribute("href")).toBe("/gateways/gw-1/config");
  });
});
```

(If the existing page is heavily wired to other hooks, mock them out the same way Task 9 did.)

**Step 3 — Verify it fails:**

```bash
cd frontend && npm run test -- gateways/\[gatewayId\]/page
```
Expected: `Unable to find role="link" with name /config/i`.

**Step 4 — Implement:** add one `<Link href={...}>Config</Link>` into the existing tab strip in `page.tsx`. Match surrounding tab styling exactly.

**Step 5 — Verify it passes + typecheck:**

```bash
cd frontend && npm run test -- gateways/\[gatewayId\]/page && npm run typecheck
```
Expected: passed; 0 type errors.

**Step 6 — Commit:**

```bash
git add frontend/src/app/gateways/\[gatewayId\]/page.tsx frontend/src/app/gateways/\[gatewayId\]/page.test.tsx
git commit -m "feat(ui): add Config tab on gateway detail page"
```

---

## Task 11: Manual smoke test on `.64` + supersede workaround memories

**Files:**
- Modify: `/Users/macmini/.claude/projects/-Users-macmini-Workspace-Agent-openclaw-mission-control/memory/feedback_restart_required_fields.md` (mark superseded)
- Modify: `/Users/macmini/.claude/projects/-Users-macmini-Workspace-Agent-openclaw-mission-control/memory/feedback_openclaw_config_set_restart_msg.md` (mark superseded)
- Modify: `/Users/macmini/.claude/projects/-Users-macmini-Workspace-Agent-openclaw-mission-control/memory/MEMORY.md` (update index lines)

**Step 1 — Push and wait for CI/CD to land on `.64`:**

```bash
git push origin feat/config-reload-inspector
# Open PR via the normal flow; merge after review; CI/CD self-hosted runner deploys to .64.
```

Per `feedback_cicd_only_deployment` — no scp, no manual ssh+git pull.

**Step 2 — Smoke via `curl` against `.64`:**

```bash
LOCAL_AUTH_TOKEN=… \
  curl -s -H "Authorization: Bearer $LOCAL_AUTH_TOKEN" \
  "https://mc.example.local/api/v1/gateways/<gateway-id>/config/lookup?path=agents.defaults.models" \
  | jq '.reloadKind, .children[0:3]'
```

Expected: `"restart"` for the example path; child rows include their own `reloadKind`. Use `LOCAL_AUTH_TOKEN`, **not** the lead bearer (per `feedback_lead_token_suppresses_wake`).

**Step 3 — Smoke the UI in Chrome:**

Open `https://mc.example.local/gateways/<id>/config?path=.`. Click into `agents` → `defaults` → `models`. Verify badges render and the URL stays in sync.

**Step 4 — Supersede memories** (only after a successful smoke):

Edit `feedback_restart_required_fields.md` to add at the top:

```markdown
> **Superseded 2026-05-21** by the gateway config schema lookup page at `/gateways/<id>/config`. The page surfaces `reloadKind` directly from the gateway — no more grep-the-changelog workflow.
```

Same for `feedback_openclaw_config_set_restart_msg.md`. Update `MEMORY.md` so each index line ends with "— SUPERSEDED 2026-05-21 by config schema lookup page".

**Step 5 — Commit memory updates** (these live outside the repo, so no `git add` needed — the auto-memory system persists them on its own filesystem).

---

## Out of scope (reaffirmed from design)

- Save / edit flow — operators still SSH + `openclaw config set` per `.60`
- Bulk lookup, full-tree search, diff view
- Current-value display alongside schema (needs `config.get`)
- Live updates when gateway config changes underneath

## Dependencies between tasks

```
Task 1 (schemas) ────────────────────┐
                                     ▼
Task 2 (path validator)              │
        │                            │
        ▼                            │
Task 3 (cache) ──────────────────────┤
                                     ▼
                              Task 4 (handler — happy path)
                                     │
                                     ▼
                              Task 5 (handler — error mapping)
                                     │
                                     ▼
                              Task 6 (pass-through guard)
                                     │
                                     ▼
                              Task 7 (regenerate API client)
                                     │
                                     ▼
                              Task 8 (badge component)
                                     │
                                     ▼
                              Task 9 (page)
                                     │
                                     ▼
                              Task 10 (tab link)
                                     │
                                     ▼
                              Task 11 (smoke + memory cleanup)
```

Tasks 1–6 can ship as one backend PR. Tasks 7–10 are the frontend PR. Task 11 is operator-validated after deploy.

## Verification rollup (run after every backend task)

```bash
cd backend && uv run pytest -q
```

## Verification rollup (run after every frontend task)

```bash
cd frontend && npm run typecheck && npm run test
```
