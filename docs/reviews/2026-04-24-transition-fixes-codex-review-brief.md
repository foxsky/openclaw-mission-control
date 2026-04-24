# Transition-bug fixes — codex review brief (2026-04-24)

## Context

Earlier today (2026-04-24) codex reviewed `transition-bug-review.md`
on `feature/phase-0-workflow-invariants` and validated the cycle-1
`inbox → review` bug while surfacing 7 additional findings (A–G + L9).
Two follow-up commits land the fixes for A, B, D, E, G and document
finding C as an intentional deferral. Finding F (test coverage) is
addressed implicitly via the new tests.

This brief asks codex to confront those follow-ups: did the fixes
land where I claim they did, do the tests prove what I claim they
prove, and what's still wrong.

## Commits to review

- **`0eb19cf6`** — `fix(tasks): close agent-path transition holes +
  wire deploy_truth_v1 flag`
- **`cedecd53`** — `fix(tasks): leads can PATCH deploy-truth
  metadata; document C deferral`

Branch: `feature/phase-0-workflow-invariants` (5 commits ahead of
`prod/master`).

## Changes — what each fix does, where

### A. Lead `inbox → in_progress` shortcut now stamps `in_progress_at`

**Location:** `backend/app/api/tasks.py`, in `_lead_apply_status`,
inside the inbox→in_progress shortcut (assignment-and-start).

**Diff:**
```python
if update.task.status == "inbox" and target_status == "in_progress" and assigning_agent:
    update.task.status = target_status
+   update.task.in_progress_at = utcnow()
    return
```

**Why:** prior behavior set the status without stamping the
timestamp. Later `review` transitions then cleared null →
`previous_in_progress_at`, losing cycle-time truth.

**Test:** `test_lead_inbox_to_in_progress_shortcut_stamps_in_progress_at`
in `test_task_agent_permissions.py`. Asserts `updated.in_progress_at
is not None` after the shortcut.

### B. No-op `status=X` PATCH no longer wipes `previous_in_progress_at`

**Location:** three sites in `tasks.py`:

1. Non-lead path `_apply_non_lead_agent_task_rules`: added early
   return `if status_value == update.task.status` before the
   side-effect branches.
2. Lead path `_lead_apply_status`: inbox/rework branches now guard
   with `if update.task.status != target_status`.
3. Admin path `_apply_admin_task_rules`: each
   inbox/cancelled/review/in_progress branch now guards with
   `status_changing = status_value != update.task.status`.

**Why:** previously, branches unconditionally assigned
`previous_in_progress_at = in_progress_at`. A no-op PATCH on a
review task with `in_progress_at=null` and a previously-set
`previous_in_progress_at` would wipe the prior value to null.

**Test:** `test_admin_noop_review_preserves_previous_in_progress_at`
in `test_task_agent_permissions.py`. Pre-set `previous_in_progress_at
= original_in_progress`, do a no-op `status=review` PATCH, assert
`refreshed.previous_in_progress_at == original_in_progress`.

### D. Cycle-1 + rework → review/done shortcuts blocked

**Location:** `backend/app/api/tasks.py`, top-of-module
`_AGENT_PATH_VALID_TRANSITIONS` constant + `_validate_agent_transition`
helper. Called from `_apply_non_lead_agent_task_rules` after
`status_value` is computed.

**Allow-list contents:**

```python
_AGENT_PATH_VALID_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    # forward progress
    ("inbox", "in_progress"),
    ("rework", "in_progress"),
    ("in_progress", "review"),
    # completion — subject to downstream gates
    ("in_progress", "done"),
    ("review", "done"),
    # backward / abandon
    ("in_progress", "inbox"),
    ("rework", "inbox"),
    ("in_progress", "rework"),
    # no-op self-moves
    ("inbox", "inbox"),
    ("in_progress", "in_progress"),
    ("review", "review"),
    ("rework", "rework"),
    ("done", "done"),
})
```

**Implicitly rejected (raises 403):** `inbox → review`, `inbox →
done`, `rework → review`, `rework → done`, `review → {inbox, rework,
in_progress}`, `done → *`.

**Why:** prior code accepted any target status without source-state
validation. Cycle-1 `9d8d868b` was QA-Unit moving inbox → review
twice without ever entering in_progress.

**Tests:**
- `test_task_status_contract.py` — 27 parametrized pure unit tests
  (13 legal + 14 illegal pairs) against `_validate_agent_transition`.
- `test_task_agent_permissions.py` — DB-backed
  `test_non_lead_agent_inbox_to_review_is_refused` and
  `test_non_lead_agent_rework_to_review_is_refused`. Both assert
  403 + "Invalid status transition" + state unchanged in DB.

### E. `deploy_truth_v1` rollout flag now actually gates

**Location:** `backend/app/api/tasks.py`:
- New `_resolve_rollout_flags(session, board_id) -> dict | None`
  helper.
- `_require_deploy_truth` signature gains optional `rollout_flags:
  dict | None = None` keyword arg.
- Inside the gate, when `rollout_flags is not None and not
  bool(rollout_flags.get("deploy_truth_v1", False))`, schedule a
  `deploy_truth_v1_disabled` shadow metric and return.
- All three call sites (`create_task` line ~2293, lead
  pre-apply line ~3158, non-lead/admin finalize line ~3702) now
  resolve flags first, pass through.

**Why:** prior behavior — flag existed in `Board.rollout_flags`
allowlist (`schemas/boards.py:28`) but was read by zero code.
Phase V enforcement ran whether the board opted in or not.

**Tests:** three new in `test_deploy_truth.py`:
- `test_deploy_truth_skipped_when_flag_disabled` — flag=False with
  missing SHA → degraded-shadow emit, no raise
- `test_deploy_truth_enforces_when_flag_enabled` — flag=True with
  missing SHA → 409 raise
- `test_deploy_truth_unchanged_when_flag_absent` — `rollout_flags=
  None` (backward-compat) → 409 raise

### G. Leads can PATCH `packet_commit_sha`/`packet_build_sha`/`supports_build_metadata`

**Location:** `backend/app/api/tasks.py`,
`_validate_lead_update_request`. Three fields added to
`allowed_fields` set.

**Why:** the deploy-truth pre-apply gate in
`_apply_lead_task_update` was already wired to run when these
fields change. But the lead-allowed-fields filter at the top of
the lead path stripped them with 403 "unsupported fields" — the
gate could never actually fire.

**Test:** `test_lead_can_patch_deploy_truth_metadata` in
`test_task_agent_permissions.py`. Lead PATCHes `packet_commit_sha=
"781c10f", supports_build_metadata=True` on a review-status task,
asserts both applied.

### C. Central TRANSITION_RULES table — considered, deferred

**Decision rationale documented inline** in `tasks.py` at the
`_AGENT_PATH_VALID_TRANSITIONS` constant comment block:

> "Why no equivalent `_LEAD_PATH_VALID_TRANSITIONS` / admin
> table: the lead path's inbox→in_progress shortcut is
> conditional on `assigning_agent`, a side condition a pure
> allow-list table can't express. The lead path's inline
> validation in `_lead_apply_status` is already explicit,
> status-scoped, and covered by its own tests. The admin path is
> intentionally permissive (cancel/uncancel, migration scripts).
> A uniform table across all three roles would either lose
> information (drop the side conditions) or recreate the
> per-role logic anyway."

## Test results

Full suite: **897 passed, 5 skipped, 4 xfailed, 0 failed.**

Up from 890 baseline. 7 net-new tests:
- 27 parametrized transition unit tests (status_contract)
- 4 DB-backed integration tests (permissions)
- 3 deploy-truth flag tests

One pre-existing uncommitted `BOARD_AGENTS.md.j2` template drift on
the working tree exceeds the 23000-char per-file bootstrap cap. Not
caused by these commits — verified via `git stash && pytest`.

## What I want you to confront

Do not validate. Confront.

1. **Allow-list correctness.** Is
   `_AGENT_PATH_VALID_TRANSITIONS` actually correct, or did I
   miss a legitimate transition that some existing flow depends
   on? Look at `test_tasks_done_approval_gate.py` — it expects
   non-lead `review → done` and `in_progress → done` to be
   accepted (subject to approval / review-toggle). I added those
   late after a test failure. Are there other agent-path flows I
   haven't thought of? E.g. resurrection of a `cancelled` task
   to `inbox`? Re-opening a `done` task?

2. **No-op guard correctness.** I added `if status_value ==
   update.task.status: return` early in the non-lead path. That
   skips ALL side effects on a no-op. But it also skips the
   `assigned_agent_id = update.actor.agent.id` re-assignment
   that the `else` branch (in_progress) was doing. Is there any
   flow where a no-op `status=in_progress` PATCH was relying on
   that re-assignment to claim the task? If so, my early-return
   regresses ownership-claiming.

3. **Lead deploy-truth metadata gate.** I added the three fields
   to lead-allowed_fields. The pre-apply gate already runs when
   these fields are part of `update.updates`. But: does the gate
   handle the case where a lead sets `supports_build_metadata=
   True` AND `packet_commit_sha=X` simultaneously on a task that
   currently has `supports_build_metadata=null`? Specifically,
   the projected task — does the projection apply BOTH fields
   before the gate runs them? See `_projected_task` at line
   ~525.

4. **Rollout flag fail-shape.** Currently when
   `rollout_flags["deploy_truth_v1"]` is False, we emit a
   shadow metric with reason `"deploy_truth_v1_disabled"` and
   return. But: should we also do this when the flag KEY is
   missing entirely (vs explicitly False)? My code uses
   `bool(rollout_flags.get("deploy_truth_v1", False))` — missing
   == False, so missing keys are treated as opted-out. Is that
   the safer fail-shape, or should missing default to opted-in
   for backward compat (since prior behavior was always
   enforced)?

5. **C deferral defensibility.** I argued a uniform
   role-keyed transition table would lose information or
   recreate per-role logic. Is that true, or am I rationalizing?
   Could a more general `(from, to, condition)` schema work,
   where `condition` is a function reference? Or is that just
   bloat for one shortcut?

6. **Test coverage gaps.** Codex finding F said no test asserted
   non-lead `inbox → review` was refused. I added that. But:
   did I cover the FULL test matrix codex implicitly demanded?
   Review the parametrized list and the DB-backed tests against
   the allow-list and tell me what's still missing.

7. **Anything else.** Specifically: did either commit introduce
   a NEW bug class while fixing the original? Look for: behavior
   coupling (status side effects entangled with timestamp logic),
   TOCTOU between projection and apply, async ordering between
   `_resolve_rollout_flags` and the gate fetch.

Cite file paths and line numbers. Numbered findings with
severity (critical/high/medium/low). Do not defer to my framing.
