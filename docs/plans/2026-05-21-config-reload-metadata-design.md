# Gateway config reload-metadata path inspector — design

**Date:** 2026-05-21
**Status:** Design validated, revised after Codex (gpt-5.5 / xhigh) review, awaiting implementation plan
**Driver:** OpenClaw 2026.5.19 (#81612)
**Gateway minimum:** 2026.5.19 (current MC default in `app/core/config.py:94` is 2026.02.9 — endpoint either preflights compat or maps method-not-supported errors to 501)

## Goal

Surface OpenClaw's per-path config reload metadata (`restart` / `hot` / `none`) in MC as a read-only path inspector. Replace the "grep CHANGELOG.md and guess" workflow operators currently use before running `openclaw config set` on `.60`.

## Non-goals

- No save/edit flow. MC does not write gateway config in v1.
- No restart gating. Operators continue editing `openclaw.json` via SSH + `openclaw config set / patch`.
- No bulk lookup, caching, diffing, or full-tree search.

## Why now

OpenClaw 5.19 added `resolveConfigReloadMetadata` to the SDK and wired it into the existing `config.schema.lookup` WS RPC. The metadata is on the wire — MC just doesn't surface it. Two MC memories currently encode the operator's mental model as workarounds:

- `feedback_restart_required_fields.md`
- `feedback_openclaw_config_set_restart_msg.md`

Both get superseded once this page ships.

## Success criteria

1. Operator pastes a dot-path in the MC UI; sees schema + reload badge in ≤2s.
2. Browsing into children of an object path works (click-in).
3. Read-only. No write controls anywhere on the page.
4. Badge values match `resolveConfigReloadMetadata` exactly: `restart` / `hot` / `none`, with `—` fallback for missing field.
5. Once shipped and used, mark the two workaround memories superseded.

## Architecture

```
┌──────────────────────────┐         WS RPC          ┌──────────────────────┐
│  Frontend                │                         │  OpenClaw gateway    │
│  /gateways/[id]/config   │                         │  on .60 :18789       │
│  path inspector page     │                         │                      │
└────────────┬─────────────┘                         │  config.schema.      │
             │ GET                                   │  lookup(path)        │
             │ /api/v1/gateways/{id}/config/lookup   │  → {schema,          │
             │ ?path=agents.defaults.models          │     reloadKind,      │
             ▼                                       │     children[]}      │
┌──────────────────────────┐    openclaw_call()      │                      │
│  MC backend (FastAPI)    │ ──────────────────────► │                      │
│  GET handler in          │ ◄────────────────────── │                      │
│  backend/app/api/        │                         │                      │
│  gateway.py              │                         │                      │
└──────────────────────────┘                         └──────────────────────┘
```

No plugin. No `.60` changes. No DB migrations.

Frontend cannot speak gateway WS RPC directly (no device pairing key in the browser). All RPC goes through MC backend.

## Backend

**File:** extend `backend/app/api/gateway.py` (don't create new module).

**Endpoint:**

```
GET /api/v1/gateways/{gateway_id}/config/lookup
Query: path (required, string, dot-path or ".")
Auth:  existing org-scope guard
```

**Handler signature** (matches sibling routes in `gateway.py`):

```python
@router.get("/{gateway_id}/config/lookup",
            response_model=ConfigSchemaLookupResponse,
            operation_id="gateway_config_lookup")
async def gateway_config_lookup(
    gateway_id: UUID,
    path: str = Query(..., min_length=1, max_length=512),
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> ConfigSchemaLookupResponse:
    ...
```

`gateway.py:42` defines `router = APIRouter(prefix="/gateways", ...)` with no router-level dependencies — all org-scope auth is per-handler. The new handler must include `AUTH_DEP` and `ORG_ADMIN_DEP` explicitly. Gateway resolution must go through `GatewayAdminLifecycleService.require_gateway(...)` which filters by `Gateway.organization_id == ctx.organization.id` (otherwise a token holder could probe any org's gateway by id).

**Handler steps:**

1. Resolve gateway via `GatewayAdminLifecycleService.require_gateway(session, gateway_id, ctx.organization.id)` → raises 404 if not in org.
2. Validate `path`: reject only on empty-after-trim, length > 512, or NUL/control chars. **No grammar regex** — real lookup paths use bracket-quoted keys like `agents.defaults.models["openai-codex/gpt-5.5"].params`. Let the gateway parser be authoritative.
3. Build `GatewayConfig` from the resolved gateway (existing pattern in `provisioning.py`).
4. Optional gateway-version preflight: call `check_gateway_version_compatibility(config, minimum_version="2026.5.19")` once per `gateway_id` (cached in-process), or skip and rely on lazy error mapping in step 6.
5. Call inside a timeout: `payload = await asyncio.wait_for(openclaw_call("config.schema.lookup", params={"path": path}, config=cfg), timeout=5.0)`. `gateway_rpc.py:507-508` shows `_send_request` has no inner response timeout, so we wrap externally.
6. Errors arrive as raised exceptions, not result envelopes (`gateway_rpc.py:471-477` raises `OpenClawGatewayError`; `openclaw_call:665-692` re-raises it and wraps `TimeoutError`/`OSError`/`WebSocketException` as `OpenClawGatewayError`). The handler dispatches on `exc.details.get("code")` and `exc.details.get("message")` — see error mapping below.
7. Validate payload shape against `ConfigSchemaLookupResponse` and return, attaching `gateway_id`.

**Response model** (new in `backend/app/schemas/gateway_api.py`):

```python
from sqlmodel import SQLModel
from pydantic import Field
from app.schemas.common import SQLModelConfig  # existing helper

class ConfigSchemaLookupChild(SQLModel):
    path: str
    reload_kind: str | None = Field(default=None, alias="reloadKind")
    hint: str | None = None

    model_config = SQLModelConfig(validate_by_name=True)

class ConfigSchemaLookupResponse(SQLModel):
    gateway_id: UUID
    path: str
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    reload_kind: str | None = Field(default=None, alias="reloadKind")
    hint: str | None = None
    hint_path: str | None = Field(default=None, alias="hintPath")
    children: list[ConfigSchemaLookupChild] = Field(default_factory=list)

    model_config = SQLModelConfig(validate_by_name=True)
```

`reload_kind: str | None` (not `Literal[...]`) so future gateway values pass through. Frontend renders unknowns as `—`. Style matches `backend/app/schemas/skills_marketplace.py:32-34` (`metadata_ = Field(alias="metadata")` + `SQLModelConfig(validate_by_name=True)`).

**Error mapping** (dispatch on `OpenClawGatewayError.details`):

| Cause | HTTP | Body |
|---|---|---|
| Empty/oversize/control-char path | 400 | `{error: "invalid_path"}` |
| `code=INVALID_REQUEST` AND `message=="config schema path not found"` | 404 | `{error: "path_not_found", path}` |
| Any other `code=INVALID_REQUEST` | 422 | `{error: "gateway_rejected_request", detail}` |
| `code` indicating method not registered (older gateway) | 501 | `{error: "method_unsupported", requires_gateway_version: "2026.5.19"}` |
| `code=UNAVAILABLE` | 503 | `{error: "gateway_unavailable", detail}` |
| `asyncio.TimeoutError` (wrapped as `OpenClawGatewayError` by `openclaw_call`) | 504 | `{error: "gateway_timeout"}` |
| Other `OpenClawGatewayError` (transport, OSError) | 503 | `{error: "gateway_unreachable"}` |
| Non-org-member resolving gateway_id | 404 | (matches `require_gateway` not-found semantics — don't leak org existence) |

The "path not found" discriminator is the message text because the gateway source (`server-methods-DaqMkVkJ.js`) emits `errorShape(ErrorCodes.INVALID_REQUEST, "config schema path not found")` — same code as schema-validation failures. Match the exact string.

**Caching.** Without a cache, every keystroke past the 300ms debounce opens+closes a fresh WebSocket (`gateway_rpc.py:606-609`). Add a small in-process TTL cache keyed by `(gateway_id, path)` with 30s TTL and singleflight (one in-flight RPC per key). Schema rarely changes at runtime. Existing pattern in MC: `backend/app/services/openclaw/gateway_compat.py` caches version checks similarly.

## Frontend

**File:** `frontend/src/app/gateways/[gatewayId]/config/page.tsx` (new).

**Layout:**

```
┌──────────────────────────────────────────────────────────────┐
│ ← Gateway details                                            │
│                                                              │
│ Config schema lookup                                         │
│ ─────────────────────────────────────────────────────────── │
│                                                              │
│ Path: [ agents.defaults.models                          ]   │
│                                                              │
│ . › agents › defaults › models           [ Restart required ]│
│                                                              │
│ ┌──────────────────────────┬────────────────────────────┐   │
│ │  Schema                  │  Hint                       │   │
│ │  type: object            │  Model selection per agent. │   │
│ │  required: false         │  Restart-required because   │   │
│ │  description: …          │  …                          │   │
│ └──────────────────────────┴────────────────────────────┘   │
│                                                              │
│ Children (12)                                                │
│ ┌────────────────────────────────────────────────────────┐   │
│ │ openai-codex/gpt-5.5         [ Restart required ]  ›  │   │
│ │ openai-codex/gpt-5.4         [ Restart required ]  ›  │   │
│ │ heartbeat                    [ Hot reload      ]  ›  │   │
│ │ pricing                      [ Restart required ]  ›  │   │
│ └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**Data flow:**

- `path` lives in URL query (`?path=agents.defaults.models`). Bookmarkable, shareable.
- `useQuery(['gateway-config-lookup', gatewayId, path], fetch…)` via existing React Query setup.
- Debounced via `useDebouncedValue(path, 300)`.
- Empty path → default to `.` (root). Drill in by clicking a child row.
- Click a breadcrumb segment → updates URL query, re-fetches.

**Badge component** (`frontend/src/components/ConfigReloadKindBadge.tsx`, new):

```tsx
const STYLES = {
  restart: { label: "Restart required", className: "bg-red-100 text-red-900 …" },
  hot:     { label: "Hot reload",       className: "bg-emerald-100 text-emerald-900 …" },
  none:    { label: "No-op",            className: "bg-zinc-100 text-zinc-700 …" },
} as const;
// reloadKind === undefined → render "—" with tooltip:
// "Gateway didn't report restart impact for this path."
```

Tailwind palette matches existing MC status chips (see `app/agents/[agentId]/page.tsx`). Copy, don't abstract on first use.

**Generated API client.** MC uses orval (`frontend/src/api/generated/`, config at `frontend/orval.config.ts`). After backend lands, run `make api-gen` (which wraps `npm run api:gen`). Orval composes hook names as `use<OperationId-ish><PathSummary><Method>` — e.g. existing `useGatewaysStatusApiV1GatewaysStatusGet` (`frontend/src/api/generated/gateways/gateways.ts:1680`). The new hook will be `useGatewayConfigLookupApiV1GatewaysGatewayIdConfigLookupGet`. Wrap it in a local alias `useConfigLookup(gatewayId, path)` co-located with the page so call sites stay readable.

**Empty / error states:**

- 400 invalid path → inline red helper text under input. Prior result stays visible.
- 404 path not found → schema panel: "Path not found in current gateway schema."
- 503 unreachable → full-card error with timestamp of last successful lookup + retry button.
- Loading → skeleton on schema panel and children list.

**Navigation.** Add a "Config" tab on `gateways/[gatewayId]/page.tsx` alongside existing tabs. One link, no menu restructure.

**App Router gotchas.** Mark the page as a client component, `export const dynamic = "force-dynamic"`. `useSearchParams` requires either page-level dynamic or a Suspense boundary — `frontend/src/app/invite/page.tsx:150-170` is the in-repo example of the Suspense wrap. `frontend/src/app/boards/[boardId]/page.tsx:857-899` shows the URL-driven `router.replace(..., { scroll: false })` pattern this design follows.

**No write controls.** No save button, no edit fields on the schema, no "copy as config patch" helper in v1. The badge is the entire feature.

## Tests

**Backend** (`backend/tests/test_gateway_config_lookup.py`, new). Mocking pattern follows `backend/tests/test_gateway_resolver.py` and `backend/tests/test_gateway_rpc_connect_scopes.py`: patch `openclaw_call` at the import site, raise `OpenClawGatewayError(message, details={"code": "..."})` for error cases.

1. Happy path: mocked `openclaw_call` returns `{path, schema, reloadKind: "restart", children: [...]}` → 200, response validates, alias keys round-trip.
2. Empty / whitespace-only / >512-char path → 400 *and* `openclaw_call` never invoked.
3. `OpenClawGatewayError("config schema path not found", details={"code": "INVALID_REQUEST", "message": "config schema path not found"})` → 404 `path_not_found`.
4. `OpenClawGatewayError("...", details={"code": "INVALID_REQUEST", "message": "config schema lookup returned invalid payload"})` → 422 `gateway_rejected_request`.
5. `OpenClawGatewayError` carrying a "method not found"-shaped code → 501 with `requires_gateway_version`.
6. `OpenClawGatewayError(..., details={"code": "UNAVAILABLE"})` → 503 `gateway_unavailable`.
7. `asyncio.TimeoutError` raised inside the `wait_for` → 504 `gateway_timeout`.
8. Gateway resolved to a different org → 404 (verified via `require_gateway`, not 403 — don't leak org existence).
9. Pass-through fidelity: `reloadKind: "future-value"` → MC echoes it untouched (regression guard against accidental re-tightening to `Literal[...]`).
10. Cache behavior: two requests with identical `(gateway_id, path)` within 30s → `openclaw_call` invoked once (singleflight assertion).

**Frontend** (Vitest, alongside the page):

1. Renders badge for each of `restart` / `hot` / `none`.
2. Renders `—` for missing `reloadKind`.
3. Click on child row updates URL `?path=…`.
4. Click on breadcrumb segment updates URL.
5. 400 / 404 / 503 error states render correct helper text.

**Integration smoke** (manual on `.64` dev clone, not CI):

- `curl` the live endpoint with a real auth token, `path=agents.defaults.models` — confirm `reloadKind` populates from `.60`.
- Browser-test the badge render.

## Rollout

1. Backend PR first. Endpoint is useful for `curl` debugging even without the UI.
2. Frontend PR next, depends on regenerated API client.
3. No DB migrations, no template sync, no `.60` deploy, no plugin vendor step.
4. CI/CD path: foxsky push → CI → deploy to `.64` (per `feedback_cicd_only_deployment.md`). `.60` untouched.
5. After shipping and operator use: mark `feedback_restart_required_fields.md` and `feedback_openclaw_config_set_restart_msg.md` superseded with a pointer to the new page.

## Out of scope (v1)

- Save / edit flow
- Bulk lookup (multiple paths in one call)
- Caching layer
- Live update when gateway config changes underneath
- Showing current value alongside schema (would need `config.get`; add later if requested)
- Diff view between two paths
- Search across the full schema tree
