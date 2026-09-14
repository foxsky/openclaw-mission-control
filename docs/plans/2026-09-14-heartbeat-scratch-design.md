# Heartbeat Instructions via Monitor Scratch — Design

**Date:** 2026-09-14
**Branch:** `design/heartbeat-scratch`
**Status:** design approved in review; spec awaiting operator review (no implementation yet)

## Goal

Mission Control (MC) delivers each agent's heartbeat instructions on OpenClaw 2026.8+ gateways
by writing them into the agent's **heartbeat monitor scratch**, the only place the 2026.8+
runtime reads heartbeat instructions from. Legacy gateways (≤ 2026.7) keep today's
`HEARTBEAT.md` workspace file.

## Why

On OpenClaw 2026.8+:

- The runtime never reads `HEARTBEAT.md`. Heartbeat instructions come from per-job monitor
  scratch stored in the gateway state database, appended to the heartbeat prompt
  (`docs/gateway/heartbeat.md`, "Monitor scratch" and the migration note).
- `agents.files.set` rejects `HEARTBEAT.md` (`INVALID_REQUEST unsupported file "HEARTBEAT.md"`);
  the allowed names are `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `BOOTSTRAP.md`,
  `MEMORY.md`.

Observed on the production gateway (2026.9.4) on 2026-09-14:

- Every MC agent's heartbeat scratch is empty (`currentRevision: 0`, `scratch: null`); no
  `backups/heartbeat-migration` archive exists and no MC agent workspace has `HEARTBEAT.md`.
  MC agents therefore run heartbeats with **no instructions**.
- The gateway agent's heartbeat prompt still says "Read HEARTBEAT.md and follow it strictly…".
- Template sync writes to `HEARTBEAT.md` are rejected: silently skipped for workers and the main
  agent, but a board lead raises `Gateway rejected required lead workspace files as unsupported`
  (caught per agent during sync).

## Decisions (operator)

1. **Ownership:** scratch holds an MC-managed block plus a free-form agent notes area. MC
   rewrites only its block and preserves agent notes.
2. **Timing:** MC writes scratch at the same moments it writes workspace files today
   (provisioning, lifecycle updates/wakes, template sync), under existing rules (paused boards
   skipped). No new schedule.
3. **Prompt:** on 2026.8+ gateways MC sets a short scratch-aware heartbeat prompt; a per-agent
   prompt stored in MC still wins, with a warning if it references `HEARTBEAT.md`.
4. **Approach:** A. Heartbeat scratch is a virtual workspace file inside the existing file-sync
   pipeline (rejected alternatives below).

## Verified gateway contract (OpenClaw 2026.8.1 – 2026.9.4)

| Fact | Source |
|---|---|
| Each agent has one heartbeat monitor job with `declarationKey == "heartbeat:<agentId>"` and `payload.kind == "heartbeat"`, present even when the heartbeat is disabled | live `cron.list` on 2026.9.4 |
| `cron.list` params include `agentId`, `includeDisabled`, `limit` (≤ 200), `offset` | `CronListParamsSchema` (dist `src-*.mjs`) |
| `cron.scratch.get {id\|jobId}` → `{scratch: {content, revision, updatedAtMs} \| null, currentRevision, maxBytes}` | `cron.scratch.get` handler (dist `cron-*.mjs`), `CronScratchGetResultSchema` |
| `cron.scratch.set {id\|jobId, content: string(≤ 262144) \| null, expectedRevision?: int ≥ 0}` → `{ok: true, scratch, currentRevision, maxBytes}` or `{ok: false, reason: "revision-conflict", currentRevision}` | `CronScratchSetParamsSchema`, `CronScratchSetResultSchema` |
| Both scratch methods resolve the job through `cronJobMatchesCallerScope`; out-of-scope jobs answer "not found" | handlers |
| `cron.scratch.*` and `heartbeat:` job declarations exist in every tag from `v2026.8.1` to `v2026.9.4`, the same releases that use keyed `agents.entries` | `git grep` on tags |
| Scratch that is "effectively empty" (blank lines, Markdown/HTML comments, headings, fences, empty checklist stubs) skips the heartbeat (`reason=empty-heartbeat-file`) | `docs/gateway/heartbeat.md` |
| An agent can replace its own scratch during a heartbeat turn (`heartbeat_respond` optional `scratch`) | `docs/gateway/heartbeat.md` |
| `config.patch` with no changed paths is a no-op (`respondConfigPatchNoop`: no write, no restart); a real change writes config with `restartReason=config.patch` | `config.patch` handler (dist `config-*.mjs`) |

## Architecture

Templates and triggers stay as they are; only the destination of the rendered `HEARTBEAT.md`
changes on keyed-layout gateways.

1. **Layout remembered per operation.** `OpenClawGatewayControlPlane` records the layout that
   `patch_agent_heartbeats` already detects from `config.get` (`_uses_keyed_agent_entries`, #18)
   and exposes `uses_keyed_agent_entries()`: the recorded answer, or one `config.get` when none
   is recorded yet. No new config surface.
2. **`HeartbeatScratchWriter`** (new module), one public call `write(agent_id, instructions)`:
   resolve the job, read scratch, splice MC's block, write with `expectedRevision`.
3. **`splice_mc_block(existing: str | None, instructions: str) -> str`**, pure function (no I/O).
4. **Routing in `_set_agent_files`.** On keyed layouts the rendered `HEARTBEAT.md` goes to the
   writer instead of `agents.files.set`, and `HEARTBEAT.md` is not treated as an expected
   workspace file (so no lead "unsupported file" error; stale-file cleanup on older gateways is
   unaffected).
5. **Scratch-aware prompt.** When MC builds a keyed-layout heartbeat for an agent without a stored
   `prompt`, it sets: "Follow the heartbeat checklist appended below. If nothing needs attention,
   reply HEARTBEAT_OK." A stored prompt is kept; if it mentions `HEARTBEAT.md`, MC logs
   `gateway.heartbeat_prompt.references_heartbeat_md` and adds a sync warning.
6. **Templates.** `BOARD_HEARTBEAT.md.j2` remains the single source. Its three role variants'
   `# HEARTBEAT.md` titles and "this file" wording become destination-neutral
   ("# Heartbeat checklist", "this checklist").

Legacy-layout gateways (≤ 2026.7) keep writing `HEARTBEAT.md` exactly as today.

## Scratch format

```
<!-- mission-control:heartbeat:begin (managed by Mission Control; edits here are overwritten) -->
…rendered BOARD_HEARTBEAT.md.j2…
<!-- mission-control:heartbeat:end -->

## Agent notes
…agent-owned; preserved across Mission Control syncs…
```

- MC's block always comes first so its instructions lead the appended scratch.
- Markers are HTML comments; the block always carries real instructions, so scratch is never
  "effectively empty".
- The rendered block tells the agent to keep the markers and to edit only under
  `## Agent notes`, including when it replaces scratch via `heartbeat_respond`.

`splice_mc_block` rules:

| Existing scratch | Result |
|---|---|
| `None` / empty | MC block + blank `## Agent notes` section |
| Contains a begin/end marker pair | Replace text between the first pair only; everything else unchanged |
| Text without markers | MC block on top; existing text kept below under `## Agent notes` (header added if absent) |
| Duplicate or stray markers | First complete begin/end pair is MC's; all other text preserved verbatim |
| Splice result equals existing | Returned unchanged (caller skips the write) |

## Data flow (provisioning, update, wake, template sync)

1. `upsert_agent` → `patch_agent_heartbeats` → `config.get`: layout detected and recorded; on
   keyed layouts the scratch-aware prompt is applied to MC's desired heartbeat.
2. Templates render as today (including `HEARTBEAT.md`).
3. `_set_agent_files`: on keyed layouts `HEARTBEAT.md` → `HeartbeatScratchWriter.write`.
4. Writer:
   1. `cron.list {agentId, includeDisabled: true}` → job with `declarationKey == "heartbeat:<agentId>"`.
   2. `cron.scratch.get {id}` → `(content, revision)`.
   3. `new = splice_mc_block(content, rendered)`; if `new == content`, stop (no write, no revision bump).
   4. `cron.scratch.set {id, content: new, expectedRevision: revision}`.
   5. On `revision-conflict`: re-read, re-splice, write once more.
5. Lifecycle continues unchanged (stale-file cleanup, wake).

Applies to every MC agent processed by that path, whether or not its heartbeat is enabled, so a
paused board's agents have instructions ready when resumed (paused boards themselves remain
skipped by sync until resumed).

## Error handling

A heartbeat-instructions failure never blocks credentials, workspace files, or the wake. Each is
logged with a stable event name, surfaced in the sync result, and retried by the next trigger.

| Case | Handling |
|---|---|
| Heartbeat job not found (new agent before the gateway materializes its monitor job) | Up to 3 lookups, backoff 0.5 → 1 → 2 s. Still missing: warning `gateway.heartbeat_scratch.job_missing` + sync-result error; lifecycle continues |
| `revision-conflict` twice | Warning `gateway.heartbeat_scratch.conflict`; skip |
| Rendered block larger than `maxBytes` | Error `gateway.heartbeat_scratch.too_large`; no write, no truncation |
| Job hidden by caller scope ("not found") | Same as job not found |
| Gateway unreachable / timeout | Existing gateway error handling (retry/backoff, propagate) |
| Stored prompt references `HEARTBEAT.md` | Keep prompt; warning `gateway.heartbeat_prompt.references_heartbeat_md` |
| Board lead | Scratch failures do not raise (unlike today's rejected-file path) |

Accepted trade-off: if an agent removes MC's markers via `heartbeat_respond`, the next write puts
MC's block back on top and keeps the agent's text as notes; an unmarked copy of the old block
pasted by the agent would remain in the notes.

## Testing

Unit (test-first):

- `splice_mc_block`: table-driven over the rules above.
- `HeartbeatScratchWriter` (fake `openclaw_call`): job selection by `declarationKey` among several
  jobs; missing then present on retry; missing after retries → warning, no raise; identical
  content → no `cron.scratch.set`; `expectedRevision` passed; one conflict → retry succeeds; two →
  skip with warning; oversized → error, no write.
- Routing: keyed layout sends `HEARTBEAT.md` to the writer and never to `agents.files.set`; lead
  does not raise; legacy layout unchanged (existing tests stay green).
- Prompt: keyed default when none stored; stored prompt kept (+ warning if it references
  `HEARTBEAT.md`); legacy unchanged.
- Templates: neutral wording renders; size budget test still passes.

Pre-merge validation against the production gateway (read-only, nothing written):

- Resolve every MC agent's heartbeat job from the live `cron.list`.
- Render each agent's real instructions from its real DB data; check size against `maxBytes`.
- Run the full write path with gateway calls captured (not sent).
- Validate the prompt config change with OpenClaw's own `applyMergePatch` +
  `validateConfigObjectWithPlugins`, fed MC's real DB-derived heartbeat payloads.

## Rollout

- Back up `openclaw.json` before merge. The first keyed-layout sync replaces the gateway agent's
  stale "Read HEARTBEAT.md…" prompt: a real `config.patch` write, which OpenClaw applies with a
  gateway reload/restart.
- After deploy verify: the gateway agent's scratch goes from revision 0 to 1 with MC markers
  (`openclaw cron scratch <jobId>`); its next heartbeat run is no longer skipped; the agent checks
  in; the sync result has no errors.
- Rollback: revert the PR. The scratch block may stay (valid instructions) or be cleared with
  `openclaw cron scratch <jobId> --unset`; restore the prompt from the config backup if needed.

## Assumptions under review

Not yet proven from source; each must be confirmed or the design amended before planning.

1. A new agent's heartbeat monitor job exists (or appears within seconds) after `agents.create` /
   `config.patch`.
2. `cron.list {agentId}` returns system-declared heartbeat jobs, including disabled ones, and
   `declarationKey` is stable for the agent's lifetime.
3. `expectedRevision: 0` is the correct compare-and-set value when scratch is `null`.
4. MC's paired device (operator.read/admin/approvals/pairing) passes `cronJobMatchesCallerScope`
   for every agent's heartbeat job.
5. Scratch is appended to the configured heartbeat prompt (not replaced), so a short prompt plus
   scratch is the full instruction set.
6. HTML-comment markers do not change the effectively-empty check when real instructions are
   present, and survive storage verbatim.
7. `heartbeat_respond`'s `scratch` fully replaces content (no append/merge) and bumps revision.
8. A heartbeat `prompt` change via `config.patch` restarts or hot-reloads the gateway (which one).
9. The layout is always known before `_set_agent_files` in every trigger path (provision, update,
   wake, template sync).
10. Excluding `HEARTBEAT.md` from the expected set does not make stale-file cleanup delete or
    mis-handle anything on keyed or legacy gateways.
11. The 262144 limit is bytes (UTF-8) as enforced by the store, versus string length in the
    RPC schema.
12. Adding a default `prompt` does not create perpetual config diffs (MC compare vs gateway
    normalization).

## Rejected alternatives

- **B. Separate heartbeat-instructions service** invoked from provisioning, lifecycle, and sync.
  Duplicates trigger wiring in three places; the two write paths can drift.
- **C. Instructions in the heartbeat `prompt` config.** Puts a large playbook into
  `openclaw.json` (every change is a config write and reload), contradicts OpenClaw's documented
  home for heartbeat instructions, and separates MC's checklist from the agent's own scratch.

## Out of scope

- MC's exact-dict heartbeat comparison sending no-op `config.patch` calls for enabled agents.
- The disabled gateway event subscriber.
- Un-pausing the "Dev Squad" board (operator decision).
