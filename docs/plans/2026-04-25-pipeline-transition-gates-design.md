# Pipeline Transition Gates — Design

**Date:** 2026-04-25
**Status:** Proposed
**Author:** Operator + Claude Opus 4.6 + Codex gpt-5.5 review

## Problem

LLM agents skip mechanical pipeline steps (commit, build, deploy, verify)
because template instructions are unreliable. Today's session required 8
template patches for problems that kept recurring. Backend enforcement is
the only durable solution — agents can ignore prose but can't bypass API
gates.

### Observed failures (2026-04-25)

| Failure | Times | Template fix worked? |
|---------|-------|---------------------|
| PF submitted review with no commit | 3 | No — PF asked @lead |
| PF committed but didn't deploy | 2 | No — operator had to nudge |
| PF submitted pre-existing code as new work | 1 | Backend gate caught it |
| Supervisor PATCHed without nudging | 2 | Partially — nudge fires but PATCH skipped |
| PF didn't re-read full FAIL findings | 2 | Unknown — just deployed |
| PF didn't set packet_commit_sha in PATCH | 2 | No — PF can't, field not in agent allowlist |

## Existing Enforcement (deployed)

1. **Transition table** (`_AGENT_PATH_VALID_TRANSITIONS`) — 13 legal pairs,
   blocks rework->review, inbox->review, etc.
2. **Deploy truth** (`_require_deploy_truth`) — synchronous SHA check against
   `/__build` on review/done transitions when `supports_build_metadata=True`.
3. **Rework progress** (`rework_entry_commit_sha`) — snapshots SHA on rework
   entry, rejects review if SHA unchanged.

## Proposed Gates

### Gate 1: Require `packet_commit_sha` on review transitions

**What:** Reject `in_progress -> review` when `packet_commit_sha` is null,
for tasks that have a `validation_target` or a deployable `review_packet_type`
(`frontend_ui`, `mixed`, `backend_api`, `infra_ops`).

**Why:** PF submitted for review 3 times today with null SHA. The backend
accepted it (no gate), and the Architect had to catch stale evidence.

**Scope:** Only tasks with `validation_target is not None` OR
`review_packet_type in DEPLOYABLE_TYPES`. Pure `review_only` tasks without
a validation target are exempt (e.g., locale quality review, spec review).

**Prerequisite — expand agent allowlist:** Non-lead agents currently cannot
PATCH `packet_commit_sha` (allowed fields: `status`, `comment`,
`custom_field_values`). Add `packet_commit_sha` and `packet_build_sha` to
the agent-path allowed fields in `_apply_non_lead_agent_task_rules`. Without
this, agents will always hit 409 and escalate to @lead.

**Implementation:**

```python
DEPLOYABLE_PACKET_TYPES = {"frontend_ui", "mixed", "backend_api", "infra_ops"}

def _require_commit_sha_for_review(task: Task) -> None:
    """Reject review transition without packet_commit_sha for deployable tasks."""
    if task.validation_target is None and task.review_packet_type not in DEPLOYABLE_PACKET_TYPES:
        return  # review-only, no target — skip
    if not task.packet_commit_sha:
        raise HTTPException(409, detail={
            "message": "Cannot move to review without packet_commit_sha. "
                       "Commit your changes, then include packet_commit_sha in the PATCH.",
            "code": "review_missing_commit",
        })
```

**Call site:** In all three PATCH paths (agent, lead, admin) where target
status is `review`, after transition validation, before commit.

**Error message is actionable:** Agent reads "include packet_commit_sha in
the PATCH" and retries. No @lead escalation needed.

### Gate 2: Async deploy parity check

**What:** After a task with `validation_target` transitions to `review`,
enqueue a background job that verifies the live target matches
`packet_commit_sha`. If parity fails, revert to `in_progress` and auto-post
a diagnostic comment.

**Why:** PF committed code but didn't deploy 3 times today. The Architect
caught stale builds, wasting a review cycle each time. Async verification
catches this without blocking the PATCH.

**Scope:** Fires only when:
- `validation_target is not None`
- Rollout flag enabled (default: enforced)
- `supports_build_metadata is True` for hard check via `/__build`
- `supports_build_metadata is False/None` for degraded soft check (metric
  only, no revert)

**Flow:**

```
Agent PATCHes status=review
  -> PATCH succeeds immediately
  -> DB commit
  -> Enqueue deploy_parity_check(task_id, expected_sha, expected_status,
       expected_updated_at, prior_agent_id)
  -> Background worker runs (1-5s later):
       fetch /__build from validation_target
       compare live SHA vs packet_commit_sha
       IF match: no action (task stays in review)
       IF mismatch:
         compare-and-swap guard: only revert if task.status == 'review'
           AND task.packet_commit_sha == expected_sha
           AND task.updated_at == expected_updated_at
         revert task to in_progress
         restore assigned_agent_id to prior_agent_id
         auto-post system comment:
           "Deploy parity failed: live /__build reports <live_sha>,
            but packet_commit_sha is <expected_sha>. Build and deploy
            before resubmitting to review."
```

**Correlation guards (per Codex review):**
- Job carries: `task_id`, `expected_sha`, `expected_updated_at`, `prior_agent_id`
- Revert only if all match current DB state (compare-and-swap)
- Stale jobs cannot revert newer submissions
- Idempotent — safe to retry

**Assignee restoration:** The review transition clears `assigned_agent_id`.
The job payload captures `prior_agent_id` before the PATCH. On revert,
restore it so the task is actionable in `in_progress`.

**Optional enhancement:** Add `deploy_parity_status` field
(`pending | passed | failed | skipped`) so Supervisor knows whether
parity has been verified before creating approvals. Not required for MVP.

### Gate 3: Scope rule

Deploy parity fires only when `validation_target is not None`.

Additionally:
- `supports_build_metadata is True` -> hard check (revert on failure)
- `supports_build_metadata is False/None` -> soft check (emit metric, no revert)

Tasks without `validation_target` skip entirely — there's nothing to fetch.

## Interaction with existing _require_deploy_truth

The existing `_require_deploy_truth` is **synchronous** and fires on
review + done transitions. For the `review` transition, we change behavior:

- **review:** async parity check replaces the sync hard-fail. The sync gate
  still runs but only for `done` transitions.
- **done:** sync gate remains. Approval -> done must still verify SHA
  against live target synchronously.

This means: agents can PATCH to review without waiting for the live fetch,
but the final approval -> done gate is still synchronous and hard.

## Codex review corrections (2026-04-25, gpt-5.5/high)

### C1: Gate 1 timing — must use projected state

The agent path runs `_apply_non_lead_agent_task_rules` BEFORE
`_finalize_updated_task` applies `update.updates` to the ORM row. So the
gate cannot read `task.packet_commit_sha` — it will see the old null value
even if the agent sends a new SHA in the same PATCH.

**Fix:** Check `update.updates.get("packet_commit_sha", task.packet_commit_sha)`
(the intended value), or extend `_projected_task` to include
`packet_commit_sha` and `review_packet_type`.

### C2: Phase 2 must change sync deploy-truth for review

`_require_deploy_truth` currently hard-fails review transitions when
`supports_build_metadata=True`. Making deploy parity async for review
means removing the sync `/__build` fetch from `STATUS_GATES["deploy_truth"]`
for the `review` status, keeping it only for `done`.

**Fix:** Change `STATUS_GATES["deploy_truth"]` from `{"review", "done"}` to
`{"done"}`. The async parity check replaces the sync gate for review.

### C3: Assignee capture for revert

Review transition clears `assigned_agent_id` temporarily, then
`_finalize_updated_task` auto-assigns the board lead. The parity job
cannot read the worker from the post-review row — it sees the lead.

**Fix:** Capture `prior_agent_id = task.assigned_agent_id` BEFORE the
review transition runs, pass it in the queue payload.

### C4: `_projected_task` lacks `review_packet_type`

The deployable-type check needs `review_packet_type` in the projection.
Currently `_projected_task` does not project it.

**Fix:** Add `review_packet_type` to `_projected_task`, or read it
directly from `task.review_packet_type` (it doesn't change during the
PATCH, so the pre-update value is correct).

### C5: Phase 1 tests must account for sync deploy-truth

If `supports_build_metadata=True`, a review PATCH with SHA still 409s
from the existing sync gate unless live `/__build` matches. Phase 1
tests must either set `supports_build_metadata=False`, disable the
rollout flag, or mock a matching live target.

### C6: Queue pattern

Use the existing custom Redis-list envelope/worker pattern:
`QueuedTask`, `enqueue_task_with_delay`, `_TASK_HANDLERS` registration
in `queue.py` / `queue_worker.py`. Follow the webhook ingestion
enqueue-after-commit pattern in `board_webhooks.py`.

## Migration plan (corrected)

### Phase 1: Agent allowlist + Gate 1 (1 PR)
1. Add `packet_commit_sha`, `packet_build_sha` to agent-path allowed fields
   in `_apply_non_lead_agent_task_rules`
2. Add `_require_commit_sha_for_review` gate using projected/intended SHA
   value (not the pre-update ORM row)
3. Read `review_packet_type` from `task.review_packet_type` (static during
   PATCH) for the deployable-type check
4. Tests: agent PATCH review with null SHA + validation_target -> 409,
   agent PATCH review with SHA + validation_target -> allowed (mock
   deploy-truth or set `supports_build_metadata=False`),
   agent PATCH review with null SHA + no target + review_only -> allowed
5. Deploy, sync templates (agents can now self-correct on 409)

### Phase 2: Async deploy parity (1 PR)
1. Change `STATUS_GATES["deploy_truth"]` from `{"review", "done"}` to
   `{"done"}` — remove sync gate for review transitions
2. Add queue task type `deploy_parity_check` following existing
   `QueuedTask` / `_TASK_HANDLERS` pattern
3. Capture `prior_agent_id` before review transition, pass in queue payload
4. Wire into review transition: enqueue after DB commit
5. Implement compare-and-swap revert + auto-comment using system comment
6. Tests: mock `/__build`, verify revert on mismatch, no-op on match,
   stale job doesn't revert newer submission (CAS guard),
   revert restores prior_agent_id
7. Deploy

### Phase 3 (optional): deploy_parity_status field
1. Add field to Task model
2. Set `pending` on review entry, `passed`/`failed` by worker
3. Supervisor checks `deploy_parity_status != pending` before creating approval
4. Tests

## What this does NOT solve

These remain template/review-chain responsibilities:
- Translation quality (Architect/QA judgment)
- Whether code implements the spec (Architect review)
- Whether all FAIL findings are addressed (Architect re-review)
- Supervisor PATCH + nudge atomicity (template bash blocks)
- Whether browser evidence is real (QA-E2E validation)

The backend enforces **mechanical correctness** (did you commit? did you
deploy?). The review chain enforces **quality** (is it right?).

## Test matrix

| Scenario | Expected |
|----------|----------|
| Agent PATCH review, null SHA, has validation_target | 409 review_missing_commit |
| Agent PATCH review, null SHA, no validation_target, review_only | Allowed |
| Agent PATCH review, valid SHA, has validation_target | Allowed, parity job enqueued |
| Parity job: live matches SHA | No action |
| Parity job: live mismatches SHA | Revert to in_progress + comment |
| Parity job: task already moved to done by admin | No revert (CAS fails) |
| Parity job: newer SHA submitted while job pending | No revert (CAS fails) |
| Agent PATCH review after rework, same SHA | 409 rework_same_commit |
| Agent PATCH review after rework, new SHA | Allowed |
| Done transition: sync deploy truth still fires | 409 on mismatch |
