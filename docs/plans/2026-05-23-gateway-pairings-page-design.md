# Gateway pairings page — design

**Date:** 2026-05-23
**Status:** Design validated, revised after Codex (gpt-5.5 / xhigh) review, awaiting implementation plan
**Driver:** Live `device.pair.list` on `.60` shows 17 paired devices accumulated over months (CLI shells, control-UI sessions, CI probes). Operators have no MC-side view today; cleanup requires SSH + `openclaw nodes list` (which itself currently times out on loopback) + raw WS RPC scripts.

**Tenant assumption (load-bearing):** the gateway is a single multi-tenant resource on `.60` but MC currently operates a single org per gateway in practice — `Gateway.organization_id` is a one-to-one link (`backend/app/models/gateways.py:21`). The pairings response exposes ALL devices paired to the gateway, not just those provisioned by MC. Operators in one MC org may therefore see devices belonging to other clients (CLI shells, control-UI sessions) that the gateway happens to serve. v1 accepts this as the operational reality; if MC ever serves multiple orgs against one shared gateway, this endpoint must be re-scoped before that switch.

## Goal

Replace the SSH-and-grep workflow operators currently use to inspect/clean stale gateway pairings. The page lists paired devices with operator-useful metadata and offers a one-click Remove with a confirm modal. Self-protect: MC refuses to remove the device its own backend is authenticated as.

## Non-goals (v1)

- Pending-approval flow (`device.pair.approve` / `.reject`) — deferred to v2
- Cross-gateway view at `/admin/pairings`
- Server-side bulk-revoke / age-based auto-purge
- DB-persisted revoke audit log — application-log audit IS in v1 (see Backend → Audit logging); the DB table that would subscribe to `device.pair.resolved` via `mc-gateway-subscriber` is v2
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

**Response models** (new in `backend/app/schemas/gateway_api.py`).

The frontend table needs `lastUsedAtMs`, `scopes` (flattened union across tokens), and a token count — but not the per-token detail. v1 flattens the response server-side: no `tokens[]` array. This also keeps the wire surface narrower for the multi-tenant exposure concern.

```python
class GatewayDevice(SQLModel):
    device_id: str = Field(alias="deviceId")
    public_key: str = Field(alias="publicKey")
    platform: str | None = None
    client_id: str | None = Field(default=None, alias="clientId")
    client_mode: str | None = Field(default=None, alias="clientMode")
    role: str | None = None
    scopes: list[str] = Field(default_factory=list)      # union across tokens
    token_count: int = Field(default=0, alias="tokenCount")
    last_used_at_ms: int | None = Field(default=None, alias="lastUsedAtMs")  # max across tokens
    remote_ip: str | None = Field(default=None, alias="remoteIp")
    approved_at_ms: int | None = Field(default=None, alias="approvedAtMs")
    is_self: bool = Field(default=False, alias="isSelf")  # server-computed
    model_config = SQLModelConfig(validate_by_name=True)


class GatewayDeviceListResponse(SQLModel):
    gateway_id: UUID
    devices: list[GatewayDevice]
    model_config = SQLModelConfig(validate_by_name=True)
```

Style matches the existing `ConfigSchemaLookupResponse` precedent: `SQLModelConfig(validate_by_name=True)`, aliases for camelCase round-trip, `str | None` typing on optionals.

Backend transforms each gateway-side device dict into a `GatewayDevice` by:
1. union'ing `scopes` across all `tokens[]` entries (plus the device-level `scopes` if present)
2. computing `last_used_at_ms = max(t.get("lastUsedAtMs", 0) or 0 for t in tokens) or None`
3. setting `token_count = len(tokens)`
4. computing `is_self` per the self-protect anchor below

The transform is in a small `_project_device(raw: dict) -> GatewayDevice` helper next to the handler. Pydantic `ValidationError` on the raw dict surfaces as 502 `gateway_invalid_payload` (same pattern as the config-lookup endpoint after the hint-shape hotfix).

**Self-protect (corrected from initial design):** MC's own device identity is NOT derivable from `cfg.token` — that is just a bearer auth token (`backend/app/services/openclaw/gateway_rpc.py:538`). The authoritative source is the local Ed25519 keypair persisted on the MC backend's disk, exposed via `backend/app/services/openclaw/device_identity.py:load_or_create_device_identity()` → returns a `DeviceIdentity` with `device_id` (SHA-256 of raw publicKey, hex) and `public_key_pem`. `public_key_raw_base64url_from_pem(public_key_pem)` (same module) produces exactly the base64url shape the gateway emits in `device.pair.list` (verified live: MC's `e6bdd3ea61…` device shows publicKey `O7Pwgfa0uk2zYVHgxG_iiUWLn24IDxYtmKitzrueI0A`).

Self-protect comparison runs against `device_id` (32-byte hex string), not publicKey — `device_id` is what `device.pair.list` indexes on and what `device.pair.remove` takes as a param, so anchoring on that closes the loop with one identity primitive.

DELETE handler:
- Loads `local_device_id = load_or_create_device_identity().device_id`
- If `path_device_id == local_device_id` → **409 `cannot_remove_self`**, RPC never invoked
- If `load_or_create_device_identity()` raises (corrupted/missing identity file) → log structured error and **503 `self_identity_unavailable`** (do NOT silently proceed without self-protect; refuse the operation)

GET handler:
- Same `local_device_id` lookup; for each projected device, `is_self = (device.device_id == local_device_id)`
- If the identity lookup fails, ALL `is_self` are `False` AND the response includes `is_self_resolved: False` (frontend disables every Remove button)

This makes self-protect deterministic in both endpoints AND survives a corrupted identity file without granting a foot-gun.

**Pre-implementation RPC probe** (gates Task 4 of the plan):

Before coding the DELETE handler, run a one-shot Python script from `.64` that calls `device.pair.remove` with a deliberately invalid deviceId (e.g. `"00000000000000000000000000000000000000000000000000000000deadbeef"`). Capture and document:
- exact param shape accepted (`{"deviceId": "…"}` vs `{"id": "…"}` vs other)
- error code + message when the deviceId is not found
- error code + message if MC's current token lacks `operator.pairing` (shouldn't happen — we have the scope — but worth probing)

This is the same approach that would have caught the config-lookup hint-shape mismatch BEFORE shipping. Document the captured shape in the implementation plan; the unit tests use the verified shape.

**Error mapping (factored):** the existing `_map_gateway_error` in `backend/app/api/gateway.py` logs `gateway.config_lookup.failed` and special-cases path-not-found — too endpoint-specific to reuse blindly. Refactor before adding the pairing handler:

```
_map_gateway_error_common(exc) -> dict  # canonical {code, message, request_id} extraction + logger
_map_config_lookup_error(exc, path) -> HTTPException  # config-specific dispatch
_map_pairing_error(exc, device_id, operation) -> HTTPException  # pairing-specific dispatch
```

Each endpoint-specific mapper:
- inherits the canonical INVALID_REQUEST → 422 / UNAVAILABLE → 503 / method-not-found → 501 / generic → 503 fallbacks via `_map_gateway_error_common`
- adds its own endpoint-specific overrides

Pairing-specific dispatch:
| Gateway error | HTTP | Body |
|---|---|---|
| `INVALID_REQUEST` + message contains `"device not found"` or `"unknown device"` | 404 | `{"error": "device_not_found", "device_id": <id>}` |
| `INVALID_REQUEST` + message contains `"insufficient scope"` or `"missing scope"` | 403 | `{"error": "gateway_pairing_scope_denied"}` |
| Self-protect violation (handler-side, before RPC) | 409 | `{"error": "cannot_remove_self", "device_id": <id>}` |
| Self-identity unavailable (handler-side, before RPC) | 503 | `{"error": "self_identity_unavailable"}` |
| Other transport/timeout | 503/504 | (canonical) |

The exact `insufficient scope`/`missing scope` discriminators come from the pre-implementation RPC probe.

**Audit logging (v1):** every DELETE handler invocation emits a structured log line with operator identity and outcome:

```python
logger.info(
    "gateway.pairing.remove.attempt user_id=%s org_id=%s gateway_id=%s device_id=%s",
    auth.user.id, ctx.organization.id, gateway_id, path_device_id,
)
# ... after RPC returns:
logger.info(
    "gateway.pairing.remove.outcome user_id=%s gateway_id=%s device_id=%s outcome=%s request_id=%s",
    auth.user.id, gateway_id, path_device_id, outcome, exc.request_id if isinstance(exc, OpenClawGatewayError) else None,
)
```

`outcome` is one of `success`, `not_found`, `scope_denied`, `cannot_remove_self`, `self_identity_unavailable`, `gateway_unavailable`, `gateway_timeout`, `other`. This is the audit trail v1 ships with — a DB-persisted table that subscribes to `device.pair.resolved` events is v2.

**Caching:** none. The device list is small (<50 entries) and operators expect post-revoke refresh to reflect reality immediately. List endpoint just calls the RPC directly with `asyncio.wait_for(timeout=5.0)`.

**`operation_id`s:** explicit on both routes so orval generates clean hook names. List → `operation_id="list_gateway_devices"` → `useListGatewayDevices`. Delete → `operation_id="remove_gateway_device"` → `useRemoveGatewayDevice`.

**Stale-list race / idempotency:** if the operator opens the page, a third party removes a device on `.60` directly, and the operator then clicks Remove on the now-gone entry, the gateway emits `INVALID_REQUEST` + "device not found" → handler returns 404 `device_not_found`. Frontend treats 404 as "already removed" — toast "Device already removed" and invalidate the list query (which refetches and removes the stale row). This is the documented behavior; no retry, no special handling.

**Concurrent DELETE on the same device:** two operators click Remove simultaneously. First request wins (200); second arrives after the gateway has already removed the device → 404 path above. Acceptable.

## Frontend

**File:** `frontend/src/app/gateways/[gatewayId]/pairings/page.tsx` (new).

Wraps `DashboardPageLayout` with the same auth gate as the config inspector — `useAuth` + `useOrganizationMembership` → `isAdmin` prop. `adminOnlyMessage`: *"Only organization owners and admins can manage gateway pairings."* `headerActions`: a single `<Button variant="outline">Back to gateway</Button>`.

**Data flow:** `useGatewayDevices(gatewayId)` (orval-generated from the new endpoint) → React Query, no debounce, refetch on `useDeleteGatewayDevice` success via query invalidation.

**Table columns** (left to right):

| Column | Source | Notes |
|---|---|---|
| Client | `clientId` + `clientMode` | e.g. `gateway-client / backend`, `cli / cli`, `openclaw-control-ui / webchat` |
| Remote IP | `remoteIp` | `—` when absent (older devices) |
| Last used | `lastUsedAtMs` (server-computed) | `formatTimestamp` in America/Fortaleza pt-BR (per `feedback_use_fortaleza_timezone`); `"never"` when null |
| Approved | `approvedAtMs` | Same timezone helper |
| Scopes | `scopes` (server-flattened union) | Tailwind chip row, truncated to 3 + `"+N more"` tooltip |
| Device ID | `deviceId.slice(0, 12) + "…"` | Monospace; click-to-copy via `navigator.clipboard` |
| Actions | — | `<Button variant="destructive" size="sm">Remove</Button>` |

**Self-protect UI:** the row where `isSelf === true` renders the Remove button `disabled` with `title="This is MC's own backend device — removing would lock MC out of the gateway."`. A small `(this is MC)` pill renders next to the Client column for clarity.

**Self-identity unavailable:** if the GET response sets `isSelfResolved === false` (server couldn't load the local identity), the page renders a banner: *"MC could not verify its own device identity. Remove actions are disabled until this is resolved."* Every Remove button is disabled until the backend recovers.

**Confirm dialog:** clicking Remove opens `ConfirmActionDialog` (existing `@/components/ui/confirm-action-dialog`). Body: *"Remove paired device `<deviceId.slice(0,12)>…`? The device will lose gateway access immediately. This cannot be undone."* Confirm fires the DELETE mutation; on success the device-list query is invalidated and the table refetches.

**States** (exact copy is bikeshed; defer to PR — listed here for the test-plan's sake):
- Loading: skeleton table
- Empty: `"No paired devices."`
- 403: `"Admin required."` (rendered by `DashboardPageLayout` admin-gate fallback)
- 503 `self_identity_unavailable`: banner above the table
- 503 generic / 504: full-card error with retry button
- Delete 404 (already removed): toast `"Device already removed."` + automatic list refetch
- Delete other-error: toast `"Remove failed: <error.message>"`

**Pairings link** on the gateway detail page (`frontend/src/app/gateways/[gatewayId]/page.tsx`): add a third `<Button variant="outline">Pairings</Button>` next to Config + Edit, admin-gated identically (`isAdmin && gatewayId`).

**App Router gotchas:** same as the config inspector — `"use client"` + `export const dynamic = "force-dynamic"` + Suspense wrap around the `Inner` component for `useSearchParams` (even though this page doesn't use URL state today, the Suspense wrap is the project pattern for App Router client pages).

## Tests

**Backend** (`backend/tests/test_gateway_devices_api.py`, new). Mocking pattern follows `test_gateway_config_lookup_api.py`: httpx ASGITransport + `monkeypatch.setattr(gateway_api, "openclaw_call", _fake)`. Self-identity is mocked via `monkeypatch.setattr(gateway_api, "load_or_create_device_identity", lambda: FakeIdentity(device_id="…"))` where appropriate.

**GET tests:**

1. **Happy list:** mocked `device.pair.list` returns the live shape (3 devices, one matching MC's own `device_id`) → 200, response includes `is_self_resolved: true`, exactly one device has `isSelf: true`, `scopes` is union'd across `tokens[].scopes`, `lastUsedAtMs` = max across tokens, `tokenCount` matches.
2. **Empty list:** mock returns `{"pending": [], "paired": []}` → 200, `devices: []`, `is_self_resolved: true`.
3. **Self-identity unavailable:** `load_or_create_device_identity()` raises → 200 with `is_self_resolved: false` and every `device.isSelf` = `false`. List itself still loads.
4. **Malformed gateway payload (regression vs the hint-shape bug):** RPC returns `{"paired": "not-a-list"}` → 502 `gateway_invalid_payload`.
5. **Cross-org gateway_id:** → 404 (via `require_gateway`).

**DELETE tests:**

6. **Remove happy path:** DELETE on a non-self device → 200, `device.pair.remove` called with `{"deviceId": "…"}` (or whatever the pre-impl probe verified; pick one canonical shape and pin it here).
7. **Self-protect:** DELETE on the deviceId that matches MC's own `device_id` → **409 `cannot_remove_self`**, RPC NEVER invoked.
8. **Self-identity unavailable refuses writes:** `load_or_create_device_identity()` raises → DELETE returns **503 `self_identity_unavailable`**, RPC never invoked (writes are blocked when self-protect cannot be evaluated).
9. **Device not found (idempotent revoke / stale list race):** RPC raises `OpenClawGatewayError("device not found", details={"code":"INVALID_REQUEST", "message":"device not found"})` → 404 `device_not_found`.
10. **Gateway pairing-scope denied:** RPC raises `OpenClawGatewayError("insufficient scope", details={"code":"INVALID_REQUEST", "message":"insufficient scope: operator.pairing"})` → 403 `gateway_pairing_scope_denied`.
11. **Gateway unreachable:** RPC raises `OpenClawGatewayError("down", details={"code":"UNAVAILABLE"})` → 503.
12. **Outer timeout:** `asyncio.wait_for` fires → 504.
13. **Audit log emitted on every DELETE outcome:** assert `logger.info` was called with `gateway.pairing.remove.outcome` and the expected `outcome=` value, for each of: success, not_found, cannot_remove_self, gateway_unavailable, gateway_timeout. (One parametrized test.)

**Frontend** (`page.test.tsx`, alongside the page). Vitest + RTL + heavy mocks per `/config/page.test.tsx` precedent — mock `useGatewayDevices`, `useDeleteGatewayDevice`, `next/navigation`, `@/auth/clerk`, `useOrganizationMembership`, stub `DashboardPageLayout`.

1. Renders one row per device, shows clientId + clientMode + remoteIp.
2. The `isSelf` row's Remove button is `disabled` and shows the lock-out title.
3. Clicking Remove on a non-self row opens the confirm dialog with the truncated deviceId.
4. Confirming fires the delete mutation; the query is invalidated on success.
5. After a 404 delete response (already-removed race), toast renders and list refetches.
6. When `isSelfResolved === false`, the banner renders and every Remove button is disabled.

**Smoke (manual on `.64`):** `curl -X DELETE` for a known-stale CLI device, confirm `device.pair.list` no longer includes it. Per `feedback_validate_before_approve`, the operator does this against the live `.60` gateway via `.64` after deploy.

## Rollout

**Implementation order** (matches the config-lookup feature's shape; revised after Codex review):

1. **Pre-impl RPC probe** — script the `device.pair.remove` call against a nonexistent deviceId from `.64`, capture exact params + error shapes (not-found, scope-denied). Output goes into the implementation plan's "verified shapes" section. **Gates Task 5.**
2. **Mapper refactor** — split `_map_gateway_error` in `backend/app/api/gateway.py` into `_map_gateway_error_common` + `_map_config_lookup_error`. Existing config-lookup tests must still pass without touching them. One commit.
3. Response schema (`GatewayDevice`, `GatewayDeviceListResponse`) + projection helper `_project_device(raw)` + schema-level unit tests.
4. GET handler + happy/empty/self-identity-unavailable/malformed-payload/cross-org tests (5 tests).
5. DELETE handler + `_map_pairing_error` helper + remove-happy/self-protect/self-identity-unavailable/not-found/scope-denied/unavailable/timeout tests (7 tests).
6. Audit logging integrated into the DELETE handler + one parametrized test asserting `gateway.pairing.remove.outcome` log emission across all outcomes.
7. **Backend PR** → CI/CD → `.64`
8. Orval client regen (drift-only commit + new-endpoints commit, per #2/#3 precedent)
9. Frontend page + ConfirmActionDialog wiring + 6 vitest cases (including the `isSelfResolved` banner case)
10. Pairings button on gateway detail page + vitest case
11. **Frontend PR** → CI/CD → `.64`
12. Post-deploy: open the page, revoke 1-2 known-stale CLI devices, confirm `device.pair.list` shrinks AND `journalctl -u mc-backend` shows the audit log lines

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

## Hotfix amendment — 2026-05-25 — self-protect anchor changed (PR #9, hotfix)

The original design anchored self-protect on `load_or_create_device_identity().device_id`. Production smoke on `.64` against the live `.60` gateway showed every device returned `isSelf: false` — MC's local Ed25519 identity (`f569c3b9a5…`) does not match the gateway's view of MC's paired device (`e6bdd3ea61…`). The pairing handshake from April 2026 used a different keypair; MC currently authenticates via `cfg.token`, not the local identity file.

PR #9 switched the anchor to an IP+clientId+clientMode heuristic match against the projected device list, with a new `GATEWAY_CLIENT_OUTBOUND_IP` config override and autodetect via DGRAM `connect` + `getsockname`. The 5.25 follow-up batch (`fix/pairings-followup-batch`) added a fail-closed branch when the heuristic resolves to an empty self-set, plus renamed the audit log's `request_id` field to `gateway_request_id` to disambiguate from the HTTP-level request_id.

See memory `project_mc_pairings_page.md` for the post-hotfix live state.
