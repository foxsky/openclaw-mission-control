# Goal: guard heartbeat_config.model against dead provider refs (post-6.5 hardening)

**Status: IN PROGRESS — follow this document until every Done criterion is checked.**

## Goal statement

No MC write path can persist a `heartbeat_config.model` that references the retired
`openai-codex` provider, and MC's heartbeat sync warns (log-only) whenever it is about
to push a heartbeat model whose provider is not configured on the target gateway.
All stale `openai-codex` strings in non-test code/comments are updated to the
post-rename reality.

## Context (why)

OpenClaw 2026.6.5's updater renamed the gateway provider `openai-codex` → `openai`
(api `openai-codex-responses` → `openai-chatgpt-responses`; the old api value is now
schema-rejected). The one stale MC-side ref — Supervisor's DB
`heartbeat_config.model = "openai-codex/gpt-5.5"` — was hot-fixed in the prod DB on
2026-06-09. Codex review (gpt-5.5/xhigh, 2026-06-09) confirmed the push-back chain
(`_heartbeat_config` → `_updated_agent_list` → `config.patch`,
`backend/app/services/openclaw/provisioning.py:149-152,1014-1031,963-966`) and rated
one residual HIGH: `AgentCreate`/`AgentUpdate` accept arbitrary `heartbeat_config`
dicts (`backend/app/schemas/agents.py:84-87,173-176`), the service persists them
(`backend/app/services/openclaw/provisioning_db.py:1666-1674,1715-1728`), and the
edit UI preserves unknown heartbeat keys
(`frontend/src/app/agents/[agentId]/edit/page.tsx:213-229`) — so an operator or API
client can re-introduce a legacy model ref tomorrow and the next sync will push it
into the gateway, where it fails closed (silent dead heartbeat for that agent).

Verified live 2026-06-09 (do NOT redo): all gateway heartbeat entries already have
`skipWhenBusy: true`, so post-fix sync converges with no config.patch churn; prod DB
has zero remaining `openai-codex` refs in `agents.heartbeat_config`,
`identity_profile`, `soul_template`, `identity_template`.

## Non-goals

- No silent normalization (`openai-codex/X` → `openai/X`) of operator input — reject
  with a clear 422 instead; silent rewrites hide typos and surprise operators.
- No general model-catalog validation against live gateway state on the API write
  path (would couple agent CRUD to gateway availability).
- No gateway-side changes, no gateway restarts, no scp deploys (CI/CD only via
  foxsky push → GitHub Actions → self-hosted runner on .64).
- Test fixtures using old label values (`test_observability_poller_*.py`,
  `test_mc_hooks.py`) stay as-is — they are label-value-agnostic and still valid.

## Implementation steps — TDD, in order

Each step: write the failing test FIRST, see it fail, implement, see it pass, then
`make backend-format` (isort/black) before commit.

### Step 1 — schema-level rejection of retired provider refs

1. Failing tests (new file `backend/tests/test_agent_heartbeat_model_validation.py`):
   - `AgentUpdate(heartbeat_config={"model": "openai-codex/gpt-5.5", ...})` raises
     ValidationError naming the legacy provider and the replacement (`openai/...`).
   - Same for `AgentCreate`.
   - `"model": "openai/gpt-5.5"`, `"model": "ollama/qwen3.5:cloud"`, absent `model`
     key, and non-dict-shaped configs all still validate (no regression).
   - API-level test: PATCH agent with legacy model → HTTP 422, body mentions
     `openai-codex`.
2. Implement: a validator on the `heartbeat_config` field in
   `backend/app/schemas/agents.py` (shared by Create/Update — single constant
   `RETIRED_MODEL_PROVIDERS = {"openai-codex"}` in
   `backend/app/services/openclaw/constants.py`). Reject when
   `heartbeat_config.get("model", "").split("/", 1)[0]` is retired.
3. Verify: new tests pass; full backend suite green (`make backend-test` or
   `uv run pytest`); no other test broke.

### Step 2 — sync-time provider-existence warning (log-only)

1. Failing test (extend `backend/tests/test_global_heartbeats_reconciliation.py` or
   new file): `patch_agent_heartbeats` with an entry whose heartbeat model provider
   is absent from `config_data["models"]["providers"]` emits a WARNING log naming
   agent id + model (use `caplog`); the patch still proceeds unchanged (warn, don't
   block — MC must not brick syncs on gateway catalog drift).
2. Implement in `patch_agent_heartbeats` (provisioning.py:919) — `config_data` from
   `_gateway_config_agent_list` is already in hand; compare each entry's merged
   heartbeat `model` prefix against `config_data.get("models", {}).get("providers", {})`
   keys. No behavior change, log only.
3. Verify: test passes, suite green.

### Step 3 — stale-string cleanup (no behavior change)

- `backend/app/models/gateway_observability_samples.py:15` — docstring example
  `provider="openai-codex"` → `provider="openai"`.
- `backend/app/services/openclaw/constants.py:56` — reword comment: gateway-only
  heartbeat fields survive because of MC's client-side overlay
  (provisioning.py:1028-1031), not gateway-side "config.patch merges".
- `backend/scripts/mc_hooks.py:13` — docstring example model →
  `openai/gpt-5.5`.
- `frontend/src/app/gateways/[gatewayId]/config/page.tsx:165-166` (+ matching
  `page.test.tsx:140,154`) — example path `models["openai-codex/gpt-5.5"]` →
  `models["openai/gpt-5.5"]` (comment/example only; keep the test asserting the same
  string it renders).
- Verify: backend suite green; `npm test` green for the touched frontend test;
  `git diff` shows ONLY comment/docstring/example-string changes in this step.

### Step 4 — ship via CI/CD

1. Branch from master, commits per step (or one commit if small), push to foxsky,
   open PR against `foxsky/openclaw-mission-control` master (NOT abhi1693 upstream).
2. Verify: CI green (lint + tests). Merge. Deploy workflow green (smoke test passes).
3. Post-deploy live verification on .64:
   - `journalctl -u mc-backend` shows clean restart, no validator import errors.
   - PATCH a scratch value: attempt to set Supervisor `heartbeat_config.model` to
     `openai-codex/gpt-5.5` via the agents API → expect 422 (then confirm DB row
     unchanged: still `openai/gpt-5.5`).
   - Confirm no unexpected `config.patch` fired (gateway log / `noop` behavior),
     Dev Squad still paused, `agent_heartbeats.enabled` all false.

## Done criteria (all must be checked)

- [ ] Step 1 tests exist, failed first, now pass; legacy model refs rejected with 422
      at the API; valid configs unaffected.
- [ ] Step 2 warning fires in test when provider missing from gateway config; sync
      unblocked.
- [ ] Step 3 grep proof: `grep -rn "openai-codex" backend/app backend/scripts frontend/src`
      returns ZERO hits outside test fixtures.
- [ ] Full backend suite green locally AND in CI; frontend tests green.
- [ ] PR merged to foxsky master; Deploy workflow green; mc-backend restarted by
      CI/CD only.
- [ ] Live 422 repro on prod + DB row verified unchanged.
- [ ] Memory updated: note in `project_openclaw_v65_state.md` that the HIGH residual
      is closed (with PR number).

## Constraints (standing rules — do not violate)

- CI/CD-only deployment; never scp or ssh+git pull to .64.
- Never restart the gateway; avoid any config.patch outside the tested sync path
  (each one SIGUSR1s the gateway).
- Surgical diffs: every changed line traces to a step above; no drive-by refactors.
- If a step's verification fails twice for the same cause, stop and report instead
  of looping.
