# Heartbeat Instructions via Monitor Scratch — Design

**Date:** 2026-09-14
**Branch:** `design/heartbeat-scratch`
**Status:** implemented on this branch (Tasks 1-7); awaiting production validation and operator
approval to merge.

## Goal

On OpenClaw gateways that use the keyed `agents.entries` config layout (2026.8.1 and later,
including 2026.7.2-beta.6+ prereleases), Mission Control (MC) delivers each agent's heartbeat
instructions by writing them into the agent's **heartbeat monitor scratch** instead of the
`HEARTBEAT.md` workspace file, which that runtime never reads. Legacy-layout gateways keep
today's `HEARTBEAT.md` file.

## Why

On keyed-layout OpenClaw:

- Ordinary heartbeat polls build the prompt from the configured heartbeat `prompt` (or the
  built-in default) and append `\n\nHeartbeat monitor scratch:\n<scratch.trim()>`, followed by a
  current-time block (`src/infra/heartbeat-runner-prompt.ts:203`, `appendHeartbeatScratch`).
  Due-task turns append scratch to a generated task prompt. Exec/cron/wake-payload turns carry
  no scratch text. Normal agent bootstrap context (e.g. `AGENTS.md`) still applies.
- `agents.files.set` rejects `HEARTBEAT.md` (`unsupported file`), and the file listing omits it
  (`src/gateway/server-methods/agents.ts:126`).
- The built-in default prompt is already scratch-aware: "Follow the heartbeat monitor scratch
  context when provided. … If nothing needs attention, reply NO_REPLY."
  (`src/auto-reply/heartbeat.ts:10-12`). `HEARTBEAT_OK` is still accepted at reply edges.
- A configured prompt is used verbatim (trimmed); nothing rewrites prompts mentioning
  `HEARTBEAT.md` (`resolveHeartbeatPromptCore`, doctor migrations).

Observed on the production gateway (2026.9.4), 2026-09-14:

- Every MC agent's heartbeat scratch is unset (`currentRevision: 0`, `scratch: null`); no
  `backups/heartbeat-migration` archive; no `HEARTBEAT.md` in MC workspaces. Unset scratch does
  **not** skip the heartbeat — the model gets only the prompt and decides by itself.
- All 8 MC agents carry a per-entry prompt telling the model to "Read HEARTBEAT.md…" (one also
  names `TOOLS.md`), inherited from MC's stored `heartbeat_config.prompt`. The model is told to read
  a file that does not exist.
- MC's `AGENTS.md` and `BOOTSTRAP.md` templates also point at `HEARTBEAT.md`
  (`BOARD_AGENTS.md.j2:141,312,340,590,593,785,849`; `BOARD_BOOTSTRAP.md.j2:36`), including
  "Source Setup vars from HEARTBEAT.md" for workers and a required-files list that could make
  agents recreate the file.
- Template sync writes to `HEARTBEAT.md` are rejected: skipped for workers and main, but a board
  lead raises `Gateway rejected required lead workspace files as unsupported`.

## Decisions (operator)

1. **Ownership:** scratch holds an MC-managed block plus a free-form agent notes area. MC
   rewrites only its block and preserves notes (while the same monitor job survives).
2. **Timing:** MC writes scratch whenever it writes agent files today (lifecycle provision/update/
   wake, sweep/reconcile, template sync). No new schedule.
3. **Prompt — CHANGED, needs re-approval.** Approved earlier: MC sets its own scratch-aware
   prompt. Review showed OpenClaw's default already does that, with current guidance
   (`NO_REPLY`, automations for recurring tasks). Proposed: on keyed layouts MC **stops
   authoring a prompt** — it removes prompts that reference `HEARTBEAT.md` (MC's legacy prompts)
   so the gateway default applies; any other stored prompt is kept as intentional customization.
4. **Approach:** A. Heartbeat scratch is a virtual destination inside the existing file-sync
   pipeline.

## Verified gateway contract (v2026.9.4 source; dist 2026.9.4)

| Fact | Source |
|---|---|
| Heartbeat monitors are reconciled for **enrolled** agents: when any entry has a `heartbeat` key, exactly the entries with one (disabled `every: "0m"` included) | `src/infra/heartbeat-config.ts:57`; `src/cron/heartbeat-monitor.ts:83,146` |
| Monitor job: `declarationKey == "heartbeat:<normalizedAgentId>"`, heartbeat payload; normal reconciliation keeps the job id; un-enrolling/deleting removes the job **and its scratch**; re-creation gets a new UUID and empty scratch | `src/cron/heartbeat-monitor.ts`; `src/cron/service/jobs.ts:218`; `src/cron/store/row-codec.ts:412` |
| Monitors are created by system-job reconciliation: at gateway start (async) and after a hot config change; failed convergence retries after 30 s. `agents.create` does not wait for it; a no-op `config.patch` does not reconcile | `src/gateway/server-cron.ts:1552,1572`; `src/gateway/server-reload-hot.ts:258`; `src/gateway/server-methods/config.ts:1101` |
| `cron.list {agentId, includeDisabled: true, limit ≤ 200, offset}` includes heartbeat jobs; without `includeDisabled` disabled jobs are hidden; pages via `hasMore`/`nextOffset`; no lookup by declaration key | `src/cron/service/ops-read.ts:279,348,395`; `packages/gateway-protocol/src/schema/cron.ts:541` |
| `cron.scratch.get {id}` → `{scratch: {content, revision, updatedAtMs} \| null, currentRevision, maxBytes}`; `scratch` is null for never-set **and** unset (tombstone) rows, so `currentRevision` can be > 0 with null scratch | `src/cron/scratch-store.ts:22-60` |
| `cron.scratch.set {id, content \| null, expectedRevision?}`: CAS against `currentRevision`; conflict (or job gone) is a normal response `{ok: false, reason: "revision-conflict", currentRevision}`; every accepted non-null write bumps revision, even if identical | `src/cron/scratch-store.ts:150-215`; `src/gateway/server-methods/cron.ts:745` |
| Limit is 262,144 **UTF-8 bytes** (store and tool), plus a schema `maxLength` of 262,144 chars | `src/cron/scratch-contract.ts:2` |
| Scopes: `cron.list` needs `operator.read`; `cron.scratch.get/set` need `operator.admin` (MC's device has both). `cronJobMatchesCallerScope` only restricts agent-runtime callers | `src/gateway/methods/core-descriptors.ts:323`; `src/gateway/server-methods/cron-caller-scope.ts:34` |
| Effectively-empty (existing scratch only) → skip `empty-heartbeat-file`: ignores leading HTML comments (an unclosed `<!--` hides everything after it), blank lines, ATX headings, empty list stubs, bare fence lines | `src/auto-reply/heartbeat.ts:25-88` |
| `heartbeat_respond` `scratch` is a full replacement, persisted after the turn with CAS on the revision read at preflight; event/wake turns can replace scratch without having seen it | `src/agents/tools/heartbeat-response-tool.ts:30`; `src/infra/heartbeat-dispatch.ts:222`; `src/infra/heartbeat-runner-prompt.ts:143` |
| `agents.entries.*` changes are **hot-applied** (`restartHeartbeat`, `reconcileSystemJobs`), not a process restart, unless reload is disabled or other restart-required paths change; in-flight heartbeat turns keep their config | `src/gateway/config-reload-plan.ts:200`; `src/infra/heartbeat-runner-scheduler.ts` |
| `config.patch` is an RFC-7396 merge patch: `null` deletes a key; no changed paths → no write | dist `merge-patch-*.mjs`; `src/gateway/server-methods/config.ts:1101` |

## Architecture

1. **Layout on the lifecycle control plane.** `patch_agent_heartbeats` already reads the config
   snapshot on the same `OpenClawGatewayControlPlane` instance that later runs
   `_set_agent_files` (every production path goes provision → `upsert_agent` →
   `patch_agent_heartbeats` → render → `_set_agent_files`). It records the detected layout
   **before** its no-change early return; `uses_keyed_agent_entries()` returns it (or does one
   `config.get` if unset). Nothing is cached across operations or on template sync's outer
   control plane.
2. **`HeartbeatScratchWriter`** (new module): `write(agent_id, instructions) -> str | None`
   (warning code or None). Resolves the job, reads, splices, CAS-writes. Never raises except
   `asyncio.CancelledError`.
3. **`splice_mc_block(existing: str | None, instructions: str) -> str`**: pure.
4. **Routing in `_set_agent_files`** (keyed layout only):
   - `HEARTBEAT.md` is removed from the physical write loop, from the lead "unsupported" check,
     and from stale-deletion candidates (`_stale_file_candidates`), so cleanup never touches it.
   - The scratch write runs **after** all physical files are written, so a scratch problem can't
     block other files, credentials visibility, or the wake.
   - Scratch notes are preserved regardless of `overwrite=True` (overwrite applies to physical
     files only).
5. **Prompt (keyed layout; pending re-approval).** When building the desired heartbeat, a prompt
   that mentions `HEARTBEAT.md` is sent as `prompt: null` (deleted), letting the gateway default
   apply; other stored prompts are sent unchanged. `_normalize_heartbeat_for_compare` keeps
   `prompt` in the disabled-heartbeat comparison, so the removal also reaches the 7 disabled
   agents. MC's DB rows are not rewritten.
6. **Templates, layout-aware.** A `heartbeat_in_scratch` render variable (from the layout)
   selects wording:
   - `BOARD_HEARTBEAT.md.j2`: "# Heartbeat checklist" / "this checklist" in all three role
     variants (neutral in both layouts), plus, on keyed layouts, a short note that MC's markers must
     be kept and personal notes go under `## Agent notes`.
   - `BOARD_AGENTS.md.j2`: references to `HEARTBEAT.md` become "the heartbeat checklist (heartbeat
     monitor scratch)" on keyed layouts; the worker "Source Setup vars from HEARTBEAT.md" step now
     points at the `## Tools` values in AGENTS.md, not the checklist; the persistent
     scratch-preservation guidance (only pass `scratch` to `heartbeat_respond` with the complete
     current scratch in hand, keep MC's marked block, notes under `## Agent notes`) applies to
     every role, including main, since a wake/exec/cron turn never sees scratch content but the
     agent can still overwrite it via `heartbeat_respond`.
   - `BOARD_BOOTSTRAP.md.j2`: `HEARTBEAT.md` dropped from the required-files list on keyed layouts.
   - `scripts/check_agent_workspace_drift.py` needs no change: it only compares template/workspace
     pairs the operator passes. Keyed-layout comparisons must include
     `"heartbeat_in_scratch": "true"` in the supplied render context, or AGENTS.md renders the
     legacy `HEARTBEAT.md` wording and the script reports false drift against a keyed workspace.
7. **Diagnostics.** `_set_agent_files` returns warning codes; `LifecycleResult` gains
   `warnings: tuple[str, ...]`; `GatewayTemplatesSyncResult` gains `warnings: list[...]` (same
   shape as errors). Warnings don't count as errors (CLI exit code unchanged) and don't set
   `last_provision_error`.

Legacy-layout gateways keep writing `HEARTBEAT.md`, keep the stored prompt, and render today's
wording.

## Scratch format and splice invariant

```
<!-- mission-control:heartbeat:begin (managed by Mission Control; edits here are overwritten) -->
…rendered BOARD_HEARTBEAT.md.j2…
<!-- mission-control:heartbeat:end -->

## Agent notes
…agent-owned…
```

**Invariant: the result is always `BEGIN + "\n" + instructions.strip() + "\n" + END + "\n\n" +
notes_section`, with MC's block first.** Placing it first also means no unclosed comment in agent
text can hide MC's instructions from the effectively-empty check.

`splice_mc_block` computes `notes`:

1. `existing` None/blank → `notes = ""`.
2. Find the first line that is exactly `BEGIN` (after strip) and the first later line exactly `END`.
   If both exist, remove that span (inclusive); every other line is kept in order, including any
   text that was before the block. Otherwise (no pair, or unmatched marker lines) nothing is
   removed.
3. `notes = remaining.strip()`; a leading `## Agent notes` heading is dropped from `notes` and
   re-added by the invariant (so it isn't duplicated).
4. `notes_section = "## Agent notes\n" + (notes + "\n" if notes else "")`.

The writer skips `cron.scratch.set` when the splice result equals the existing content (the store
would otherwise bump revision on identical writes).

## Writer flow

1. Resolve: page `cron.list {agentId, includeDisabled: true, limit: 200, offset}` until a job has
   `declarationKey == "heartbeat:<agentId>"` and payload kind `heartbeat`, or `hasMore` is false.
   The job id is used only within this call.
2. `cron.scratch.get {id}` → `content = scratch?.content`, `revision = currentRevision` (always
   the top-level value).
3. `new = splice_mc_block(content, instructions)`; equal → done.
4. `len(new.encode("utf-8")) > maxBytes` → warning `heartbeat_scratch.too_large`; no write, no
   truncation.
5. `cron.scratch.set {id, content: new, expectedRevision: revision}`.
6. `{ok: false, reason: "revision-conflict"}` → back to step 1 (re-resolve: the job may have been
   replaced), at most one retry; then warning `heartbeat_scratch.conflict`.

Bounds: each RPC wrapped in `asyncio.wait_for` (5 s; `openclaw_call` itself has no response
deadline); job-missing retries use `asyncio.sleep` (0.5 s, 1 s — 3 lookups); total writer budget
15 s (total, enforced), well under the 60 s lifecycle deadline in heartbeat_sweep/lifecycle_reconcile.
Cancellation propagates.

## Error handling

Heartbeat-instruction failures never block credentials, physical files, or the wake. Each is logged
(`gateway.heartbeat_scratch.<code>`, agent id, gateway id) and added to the lifecycle/sync
warnings. The next trigger retries.

| Case | Warning code |
|---|---|
| No monitor job after 3 lookups (not yet reconciled, or agent not enrolled) | `job_missing` |
| Revision conflict twice | `conflict` |
| Spliced UTF-8 size > `maxBytes` | `too_large` |
| `scratch.get/set` "not found" (job removed mid-call) | `job_missing` |
| Gateway error / timeout during the scratch step | `gateway_error` (not raised) |

Earlier steps (config patch, physical files) keep today's error behavior. A board lead no longer
raises for `HEARTBEAT.md` on keyed layouts.

**Accepted limits:**

- Marker ownership is cooperative: CAS prevents a stale model write from clobbering MC, but a
  model can deliberately replace scratch and drop MC's block. The next MC write restores the block
  on top; any pasted copy of old instructions stays in notes.
- Notes survive only while the same monitor job exists. Deleting/un-enrolling an agent removes its
  scratch.
- Template sync skips paused boards, so those agents get scratch only once their board is resumed
  and synced (or a lifecycle run touches them).
- Prompt removal and first scratch write are not atomic; between them the agent gets the default
  prompt with no scratch (no worse than today).
- An inherited `agents.defaults.heartbeat.prompt` that names `HEARTBEAT.md` is not removed (MC
  only edits per-agent entries); OpenClaw merges defaults under entries, so such a default would
  stay effective for MC agents until an operator removes it (production has none as of
  2026-09-14).
- A scratch warning from a run whose later step (e.g. session reset) fails is only logged, not
  reported in the sync result; that agent's sync error is reported and it is retried.

## Testing

Unit (test-first):

- `splice_mc_block`, table-driven: None; blank; notes only; valid block + notes; block after
  leading text; duplicate blocks (only first removed); lone BEGIN / lone END; notes already
  headed `## Agent notes`; unclosed `<!--` in notes; idempotence (`splice(splice(x)) ==
  splice(x)`); result never effectively empty per a Python port of OpenClaw's rule.
- Writer (fake RPC): paging to find the job; disabled job found; missing then present; missing
  after retries; `scratch: null` with `currentRevision: 3` → `expectedRevision: 3`; identical →
  no set; one conflict → re-resolve + success; two → `conflict`; multibyte content over the byte
  limit but under the char limit → `too_large`; RPC timeout → `gateway_error`; cancellation
  propagates.
- Routing: keyed layout → no `agents.files.set`/delete for `HEARTBEAT.md`; scratch after
  physical files; lead doesn't raise; writer warning reaches `LifecycleResult` and sync result;
  legacy layout unchanged.
- Prompt/compare: HEARTBEAT.md prompt → `prompt: null` on keyed; custom prompt kept; disabled agent
  with legacy prompt produces one patch, and a second sync against the patched config (run through
  a gateway-normalized round trip) produces none; legacy unchanged.
- Templates: keyed/legacy renders of AGENTS/BOOTSTRAP/HEARTBEAT have no `HEARTBEAT.md` reference
  on keyed; UTF-8 byte budget for the rendered block with headroom for notes.

Pre-merge validation against production (read-only):

- Resolve every MC agent's heartbeat job from live `cron.list`.
- Render each agent's real keyed-layout instructions from real DB data; check byte size.
- Run the writer and routing with gateway calls captured, not sent.
- Feed MC's real DB-derived heartbeat payloads (with prompt removal) through OpenClaw's
  `applyMergePatch` + `validateConfigObjectWithPlugins` against the live `openclaw.json`, and
  confirm the patched result yields no further diff.

## Rollout

- Before deploy: back up `openclaw.json`, and save every MC agent's scratch
  (`openclaw cron scratch <jobId> --json`) to a file.
- The first keyed sync deletes 8 legacy prompts: one real `config.patch`, hot-applied (heartbeat
  runner restart + system-job reconcile), not a gateway process restart.
- Verify:
  - `openclaw cron scratch <jobId>` for each synced agent shows MC's markers.
  - `openclaw.json` entries have no `HEARTBEAT.md` prompts, and a second sync is a `config.patch`
    no-op.
  - The gateway agent's next ordinary heartbeat run includes "Heartbeat monitor scratch:" in its
    prompt (session transcript) and the agent checks in to MC.
  - Sync/lifecycle results carry no warnings.
- Rollback: revert the PR. That only stops MC from updating scratch; it doesn't restore a working
  file path, since the gateway rejects `HEARTBEAT.md`. Scratch may stay as is (valid instructions).
  To remove only MC's block while keeping notes, write the notes back with CAS
  (`openclaw cron scratch <jobId> --file notes.md --expected-revision <n>`), not `--unset`
  (clears everything). Restore prompts from the config backup if wanted.

## Rejected alternatives

- **B. Separate heartbeat-instructions service** called from each trigger path. Duplicates trigger
  wiring; two write paths can drift.
- **C. Instructions inside the heartbeat `prompt` config.** Puts a ~25 KB playbook into
  `openclaw.json` for every agent (each change is a config write + hot reload), bypasses the
  documented scratch home, and leaves no place for agent notes.

## Out of scope

- MC's unmerged-dict heartbeat comparison sending gateway no-op `config.patch` calls for enabled
  agents (beyond the disabled-prompt fix above).
- The disabled gateway event subscriber.
- Un-pausing the "Dev Squad" board (operator decision).
