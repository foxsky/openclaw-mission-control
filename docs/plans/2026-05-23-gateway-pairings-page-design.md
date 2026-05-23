# Gateway pairings page — design

**Date:** 2026-05-23
**Status:** Design validated, awaiting implementation plan
**Driver:** Live `device.pair.list` on `.60` shows 17 paired devices accumulated over months (CLI shells, control-UI sessions, CI probes). Operators have no MC-side view today; cleanup requires SSH + `openclaw nodes list` (which itself currently times out on loopback) + raw WS RPC scripts.

## Goal

Replace the SSH-and-grep workflow operators currently use to inspect/clean stale gateway pairings. The page lists paired devices with operator-useful metadata and offers a one-click Remove with a confirm modal. Self-protect: MC refuses to remove the device its own backend is authenticated as.

## Non-goals (v1)

- Pending-approval flow (`device.pair.approve` / `.reject`) — deferred to v2
- Cross-gateway view at `/admin/pairings`
- Server-side bulk-revoke / age-based auto-purge
- Revoke audit log (would subscribe to `device.pair.resolved` via existing `mc-gateway-subscriber`)
- Token rotation
- Public-key fingerprint display alongside `deviceId`

## Success criteria

1. Operator sees all paired devices in MC within 2s of opening the page
2. Clicking Remove on a stale device successfully drops it from `device.pair.list` (re-fetched after success)
3. MC's own backend device is non-removable from the UI (button disabled + tooltip)
4. Backend refuses the remove RPC if the targeted deviceId matches MC's own (belt-and-suspenders, 409)
5. After shipping, the stale-CLI-device count on `.60` drops via natural operator cleanup

## Architecture

```
┌──────────────────────────────┐     WS RPC         ┌──────────────────┐
│ Frontend                     │                    │ OpenClaw gateway │
│ /gateways/[id]/pairings      │                    │ on .60 :18789    │
│ - table of devices           │                    │                  │
│ - confirm modal              │                    │ device.pair.list │
└──────────────┬───────────────┘                    │ device.pair.     │
               │ GET  /api/v1/gateways/{id}/devices │   remove(devId)  │
               │ DELETE /api/v1/gateways/{id}/      │                  │
               │        devices/{deviceId}          │                  │
               ▼                                    │                  │
┌──────────────────────────────┐  openclaw_call()   │                  │
│ MC backend (FastAPI)         │ ────────────────►  │                  │
│ - org-admin-scoped handlers  │ ◄────────────────  │                  │
│ - self-protect compares      │                    │                  │
│   deviceId against own       │                    │                  │
│   gateway-client publicKey   │                    │                  │
└──────────────────────────────┘                    └──────────────────┘
```

No plugin. No `.60` changes. No DB migrations. Verified live: MC is paired with full operator scopes (`device.pair.list` returns 17 entries; MC's backend client `e6bdd3ea61…` has `operator.read/admin/approvals/pairing`).

## Backend

**File:** extend `backend/app/api/gateway.py` (same module as `/config/lookup`).

**Endpoints:**

```
GET    /api/v1/gateways/{gateway_id}/devices
DELETE /api/v1/gateways/{gateway_id}/devices/{device_id}
Auth:  org-admin (existing AUTH_DEP + ORG_ADMIN_DEP)
```

**RPC wiring:** `openclaw_call("device.pair.list", config=cfg)` and `openclaw_call("device.pair.remove", {"deviceId": …}, config=cfg)`. Both methods are already in `GATEWAY_METHODS` (`gateway_rpc.py`).

**Response models** (new in `backend/app/schemas/gateway_api.py`):

```python
class GatewayDeviceToken(SQLModel):
    role: str
    scopes: list[str] = Field(default_factory=list)
    created_at_ms: int | None = Field(default=None, alias="createdAtMs")
    last_used_at_ms: int | None = Field(default=None, alias="lastUsedAtMs")
    model_config = SQLModelConfig(validate_by_name=True)


class GatewayDevice(SQLModel):
    device_id: str = Field(alias="deviceId")
    public_key: str = Field(alias="publicKey")
    platform: str | None = None
    client_id: str | None = Field(default=None, alias="clientId")
    client_mode: str | None = Field(default=None, alias="clientMode")
    role: str | None = None
    scopes: list[str] = Field(default_factory=list)
    remote_ip: str | None = Field(default=None, alias="remoteIp")
    approved_at_ms: int | None = Field(default=None, alias="approvedAtMs")
    tokens: list[GatewayDeviceToken] = Field(default_factory=list)
    is_self: bool = False  # populated server-side, not from gateway
    model_config = SQLModelConfig(validate_by_name=True)


class GatewayDeviceListResponse(SQLModel):
    gateway_id: UUID
    devices: list[GatewayDevice]
    model_config = SQLModelConfig(validate_by_name=True)
```

Style matches the existing `ConfigSchemaLookupResponse` precedent: `SQLModelConfig(validate_by_name=True)`, aliases for camelCase round-trip, `str | None` typing on optionals.

**Self-protect:** after listing devices, MC derives its own publicKey from the stored pairing token in `cfg.token`. For each device, set `is_self = (device.publicKey == own_public_key)`. DELETE handler refuses with **409 `cannot_remove_self`** if `device_id` matches a device whose publicKey is MC's own.

If `gateway_rpc.py` doesn't expose a publicKey-derivation helper today, add one. The token is base64-encoded keypair material; the helper uses the existing pairing util (or `cryptography.hazmat` X25519/Ed25519 derivation — verify which curve the gateway uses during implementation).

**Error mapping:** reuses the existing `_map_gateway_error` helper from the config-lookup endpoint. Additional dispatch:
- `device.pair.remove` `INVALID_REQUEST` + message containing `"device not found"` → 404 `{"error": "device_not_found", "device_id": <id>}`
- Self-protect violation → 409 `{"error": "cannot_remove_self", "device_id": <id>}`
- Other transport/timeout → 503/504 (existing branches)

**Caching:** none. The device list is small (<50 entries) and operators expect post-revoke refresh to reflect reality immediately. List endpoint just calls the RPC directly with `asyncio.wait_for(timeout=5.0)`.

**`last_used_at_ms` derivation:** surfaced as a computed `lastUsedAtMs` on the response, equal to `max(t.last_used_at_ms or 0 for t in device.tokens)` — most-recent activity across all tokens for that device. Falls back to `None` when no token has been used.

## Frontend

**File:** `frontend/src/app/gateways/[gatewayId]/pairings/page.tsx` (new).

Wraps `DashboardPageLayout` with the same auth gate as the config inspector — `useAuth` + `useOrganizationMembership` → `isAdmin` prop. `adminOnlyMessage`: *"Only organization owners and admins can manage gateway pairings."* `headerActions`: a single `<Button variant="outline">Back to gateway</Button>`.

**Data flow:** `useGatewayDevices(gatewayId)` (orval-generated from the new endpoint) → React Query, no debounce, refetch on `useDeleteGatewayDevice` success via query invalidation.

**Table columns** (left to right):

| Column | Source | Notes |
|---|---|---|
| Client | `clientId` + `clientMode` | e.g. `gateway-client / backend`, `cli / cli`, `openclaw-control-ui / webchat` |
| Remote IP | `remoteIp` | `—` when absent (older devices) |
| Last used | `max(tokens[].lastUsedAtMs)` | `formatTimestamp` in America/Fortaleza pt-BR (per `feedback_use_fortaleza_timezone`); `"never"` when no token has been used |
| Approved | `approvedAtMs` | Same timezone helper |
| Scopes | flatten `tokens[].scopes` set | Tailwind chip row, truncated to 3 + `"+N more"` tooltip |
| Device ID | `deviceId.slice(0, 12) + "…"` | Monospace; click-to-copy via `navigator.clipboard` |
| Actions | — | `<Button variant="destructive" size="sm">Remove</Button>` |

**Self-protect UI:** the row where `is_self === true` renders the Remove button `disabled` with `title="This is MC's own backend device — removing would lock MC out of the gateway."`. A small `(this is MC)` pill renders next to the Client column for clarity.

**Confirm dialog:** clicking Remove opens `ConfirmActionDialog` (existing `@/components/ui/confirm-action-dialog`). Body: *"Remove paired device `<deviceId.slice(0,12)>…`? The device will lose gateway access immediately. This cannot be undone."* Confirm fires the DELETE mutation; on success the device-list query is invalidated and the table refetches.

**States:**
- Loading: skeleton with 3 placeholder rows
- Empty: `"No paired devices."`
- 403: `"Admin required."` (rendered by `DashboardPageLayout` admin-gate fallback)
- 503: `"Gateway unreachable."`
- Delete error: `<Toast>` (existing toast helper) — `"Remove failed: <error.message>"`

**Pairings link** on the gateway detail page (`frontend/src/app/gateways/[gatewayId]/page.tsx`): add a third `<Button variant="outline">Pairings</Button>` next to Config + Edit, admin-gated identically (`isAdmin && gatewayId`).

**App Router gotchas:** same as the config inspector — `"use client"` + `export const dynamic = "force-dynamic"` + Suspense wrap around the `Inner` component for `useSearchParams` (even though this page doesn't use URL state today, the Suspense wrap is the project pattern for App Router client pages).

## Tests

**Backend** (`backend/tests/test_gateway_devices_api.py`, new). Mocking pattern follows `test_gateway_config_lookup_api.py`: httpx ASGITransport + `monkeypatch.setattr(gateway_api, "openclaw_call", _fake)`.

1. **Happy list:** mocked `device.pair.list` returns the live shape (3 devices, one matching MC's own publicKey) → 200, `devices[].is_self` true for exactly one row, scopes/last_used pass through.
2. **Empty list:** mock returns `{"pending": [], "paired": []}` → 200, `devices: []`.
3. **Remove happy path:** DELETE on a non-self device → 200, `device.pair.remove` called with `{"deviceId": "…"}`.
4. **Self-protect:** DELETE on the deviceId whose publicKey matches MC's own → **409 `cannot_remove_self`**, RPC never invoked.
5. **Device not found:** RPC raises `OpenClawGatewayError("device not found", details={"code":"INVALID_REQUEST", "message":"device not found"})` → 404 `device_not_found`.
6. **Cross-org gateway_id:** → 404 (via `require_gateway`).
7. **Gateway unreachable:** RPC raises `OpenClawGatewayError("down", details={"code":"UNAVAILABLE"})` → 503.
8. **Outer timeout:** `asyncio.wait_for` fires → 504.
9. **Pass-through:** unknown fields on a device pass through (alias-by-name; extras ignored).

**Frontend** (`page.test.tsx`, alongside the page). Vitest + RTL + heavy mocks per `/config/page.test.tsx` precedent — mock `useGatewayDevices`, `useDeleteGatewayDevice`, `next/navigation`, `@/auth/clerk`, `useOrganizationMembership`, stub `DashboardPageLayout`.

1. Renders one row per device, shows clientId + clientMode + remoteIp.
2. The `is_self` row's Remove button is `disabled` and shows the lock-out title.
3. Clicking Remove on a non-self row opens the confirm dialog with the truncated deviceId.
4. Confirming fires the delete mutation; the query is invalidated on success.
5. After mutation success, the table refetches (verified by the mock being called twice).

**Smoke (manual on `.64`):** `curl -X DELETE` for a known-stale CLI device, confirm `device.pair.list` no longer includes it. Per `feedback_validate_before_approve`, the operator does this against the live `.60` gateway via `.64` after deploy.

## Rollout

**Implementation order** (matches the config-lookup feature's shape; ~9 tasks, two PRs):

1. Response schemas (`GatewayDevice`, `GatewayDeviceToken`, `GatewayDeviceListResponse`) + schema unit tests
2. MC-own-publicKey derivation helper + unit test
3. GET handler + happy/empty/cross-org tests
4. DELETE handler + remove-happy/self-protect/not-found tests
5. Error mapping coverage tests (UNAVAILABLE→503, timeout→504)
6. **Backend PR** → CI/CD → `.64`
7. Orval client regen (drift-only commit + new-endpoints commit, per #2/#3 precedent)
8. Frontend page + ConfirmActionDialog wiring + 5 vitest cases
9. Pairings button on gateway detail page + vitest case
10. **Frontend PR** → CI/CD → `.64`
11. Post-deploy: open the page, revoke 1-2 known-stale CLI devices, confirm `device.pair.list` shrinks

**Deploy path:** foxsky push → CI → self-hosted runner deploys to `.64` (per `feedback_cicd_only_deployment`). No `.60` changes, no plugin work, no template sync, no DB migrations.

## Out of scope (reaffirmed)

- Pending-approval flow (`device.pair.approve` / `.reject`)
- Bulk-revoke / age-filtered auto-purge
- Cross-gateway view at `/admin/pairings`
- Revoke audit log via `mc-gateway-subscriber` subscription to `device.pair.resolved`
- Token rotation
- Public-key fingerprint display

## Memory hygiene

No existing memory needs supersede on shipping — the pairing-gap memory was already updated in this session. Optionally add `project_mc_pairings_page.md` after first operator use, recording where the page lives and how to use it.
