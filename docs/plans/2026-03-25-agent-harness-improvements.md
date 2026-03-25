# Agent Harness Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the agent system from heartbeat-driven polling to event-driven continuous work, reduce template overhead by 80%, add calibrated QA evaluation, and fix liveness/token issues — based on Anthropic's "Harness Design for Long-Running Apps" article.

**Architecture:** 10-item improvement plan across 4 layers: infrastructure prerequisites (liveness, tokens), execution model (event-driven), templates (shrink), and quality (QA rubrics). Each task is self-contained and deployable independently.

**Tech Stack:** Python/FastAPI backend, Jinja2 templates, OpenClaw gateway (Node.js), PostgreSQL, Redis

> **Operational notes**
> - Heartbeat execution uses the heartbeat model (`minimax-m2.5`), not Supervisor's primary `gpt-5.4`. `gpt-5.4` is still used by Supervisor/Codex delegations, but those are subprocess calls layered on top of the heartbeat loop.
> - `shared/` artifacts referenced below live on the gateway at `/root/.openclaw/workspace/shared/` and are already scaffolded there. They are not tracked in this git repo.
> - Deploy `shared/` files manually (`scp`) or create them in-place from an agent session. Template sync only handles files listed in `DEFAULT_GATEWAY_FILES`.
> - `board-start.sh` already contains template sync support (commit `cb3e6fb`), so none of the tasks below require script changes for template deployment.

---

## Task 0: Fix Liveness Prerequisites

**Files:**
- Modify: `backend/app/services/openclaw/constants.py`
- Modify: `backend/app/services/openclaw/provisioning_db.py` (with_computed_status)
- Modify: `backend/tests/test_agent_provisioning_utils.py`

**Problem:** `OFFLINE_AFTER = 10m` marks 30m-heartbeat agents as offline. `DEFAULT_HEARTBEAT_CONFIG` lacks `isolatedSession` and `lightContext`, which completes the unfinished heartbeat-token-optimization work rather than introducing a new configuration idea.

**Step 1: Write the failing test**

```python
def test_default_heartbeat_config_has_isolation():
    from app.services.openclaw.constants import DEFAULT_HEARTBEAT_CONFIG
    assert DEFAULT_HEARTBEAT_CONFIG["isolatedSession"] is True
    assert DEFAULT_HEARTBEAT_CONFIG["lightContext"] is True

def test_offline_threshold_exceeds_max_heartbeat():
    from app.core.durations import parse_every_to_seconds
    from app.services.openclaw.constants import (
        DEFAULT_HEARTBEAT_CONFIG,
        HEARTBEAT_RECOVERY_GRACE_AFTER_INTERVAL,
        OFFLINE_AFTER,
    )

    configured_interval = parse_every_to_seconds(DEFAULT_HEARTBEAT_CONFIG["every"])
    assert OFFLINE_AFTER.total_seconds() > (
        configured_interval + HEARTBEAT_RECOVERY_GRACE_AFTER_INTERVAL.total_seconds()
    )
```

Also keep a provisioning-status regression case with a `30m` heartbeat override so the original false-offline bug stays covered without hardcoding `1800 + 60` into the assertion.

Run: `pytest backend/tests/test_agent_provisioning_utils.py -v -k "isolation or offline_threshold"`
Expected: FAIL

**Step 2: Fix constants.py**

```python
DEFAULT_HEARTBEAT_CONFIG: dict[str, Any] = {
    "every": "10m",
    "target": "last",
    "includeReasoning": False,
    "lightContext": True,
    "isolatedSession": True,
}

OFFLINE_AFTER = timedelta(minutes=35)
```

Note: `35m` plus the existing `1m` recovery grace means Supervisor's 5m heartbeat will not flip to `offline` until roughly 36 minutes after last-seen. Accept that lower sensitivity as a temporary tradeoff to stop false offline status for 30m agents.

TODO: Replace the single global `OFFLINE_AFTER` with per-agent offline detection derived from each agent's effective heartbeat interval.

**Step 3: Run tests**

Run: `pytest backend/tests/ -v -k "heartbeat or offline"`
Expected: PASS

**Step 4: Commit**

```bash
git add backend/app/services/openclaw/constants.py backend/tests/
git commit -m "fix(constants): OFFLINE_AFTER=35m for 30m heartbeats, add isolatedSession defaults"
```

Deployment note: this task only changes backend constants/tests. No `board-start.sh` changes are required.

---

## Task 1: Continuous Workflow Runs

**Files:**
- Modify: `backend/templates/BOARD_HEARTBEAT.md.j2` (worker workflow section, lines 386-421)

**Problem:** Template says "Execute ONE workflow step" and forces stops after PLANNING/IMPLEMENTING. Combined with 30m heartbeat, minimum 3 cycles (90 min) per task. This task is the prompt-side counterpart to the wake-flow work in `docs/plans/2026-03-17-native-event-driven-wake.md`.

**Step 1: Remove forced one-step-per-cycle constraint**

Replace the worker workflow section (lines 386-421) with:

```
   **Execute your workflow.** Read MEMORY.md for your workflow state block. If none exists, start at PLANNING.
   Run through ALL applicable states in one session until you reach `review`, hit a real blocker, or run out of time.

   **PLANNING:** Read task description + comments + specs. Write plan to MEMORY.md. Continue to IMPLEMENTING.
   **IMPLEMENTING:** Delegate to primary ACP. Run feedback loops (typecheck, lint, tests). Review with opposite ACP. Continue to VALIDATING.
   **VALIDATING:** Run self-validation (Step 8). If passes → PATCH to review (Step 9). If fails → back to IMPLEMENTING.

   Do NOT stop between states unless blocked. Small and large tasks both run continuously.
```

**Step 2: Verify template renders under 20K**

Run: `python3 -c "from jinja2 import Environment, FileSystemLoader; ..."`
Expected: Worker < 20000 chars

**Step 3: Deploy and commit**

```bash
scp backend/templates/BOARD_HEARTBEAT.md.j2 root@192.168.2.64:/home/mcontrol/.../templates/
git add backend/templates/BOARD_HEARTBEAT.md.j2
git commit -m "feat(heartbeat): continuous workflow — remove one-step-per-cycle constraint"
```

---

## Task 2: Shrink HEARTBEAT Template (19KB → 8-10KB)

**Files:**
- Modify: `backend/templates/BOARD_HEARTBEAT.md.j2`
- Create on gateway shared workspace: `/root/.openclaw/workspace/shared/docs/validation-cookbook.md`
- Create on gateway shared workspace: `/root/.openclaw/workspace/shared/docs/qa-routing.md`
- Modify: `backend/tests/test_template_size_budget.py`

**Problem:** 19KB of prompt overhead before any coding. Most is inline curl/jq blocks, validation cookbook, and policy prose the model doesn't need every cycle.

**Step 1: Identify what to cut**

Keep (essential, target rendered size ~8-10KB):
- Role identification (is_lead/is_worker)
- Auth: read TOOLS.md, check-in endpoint
- Task selection: fetch assigned task, read comments
- Workflow: continuous plan/build/validate
- Result posting: PATCH to review, post comment
- HEARTBEAT_OK gate

Move to on-demand docs (agent can read when needed):
- Inline curl/jq API discovery blocks (~2KB)
- Full validation cookbook with deployment steps A-E (~3KB)
- Port/URL consistency section (~1KB)
- Interrupted ACP session recovery (~1KB)
- Test failure classification rules (~1KB)
- Lead: full Codex review prompt (~3KB)
- Lead: full Claude Code routing prompt (~2KB)

**Step 2: Extract to callable scripts/docs**

Create plain markdown files at `/root/.openclaw/workspace/shared/docs/validation-cookbook.md` and `/root/.openclaw/workspace/shared/docs/qa-routing.md`. Template references them directly: "For deployment validation steps, read `$SHARED_WORKSPACE/docs/validation-cookbook.md`."

Do not use Jinja2 `includes/` here. Includes reduce template source duplication, but they do not shrink the rendered heartbeat prompt. The extracted content needs to live as separate `.md` files in `shared/docs/` on the gateway.

Deployment note: these `shared/docs/` files are outside the repo and outside template sync. Copy them with `scp` or have an agent create/update them on the gateway workspace.

**Step 3: Write size test**

```python
def test_worker_template_under_10kb():
    rendered = render_template(is_lead=False, ...)
    assert len(rendered) <= 10000, f"Worker template {len(rendered)} chars > 10000"
```

Budget note: aim to land in the 8-10KB range after cleanup rather than forcing an artificially small 4KB target.

**Step 4: Rewrite template to target 8-10KB**

Minimal worker template structure:
```
# HEARTBEAT.md
## Setup
Read TOOLS.md for BASE_URL, AUTH_TOKEN, BOARD_ID.
## Pre-Flight
1) Check in: POST $BASE_URL/api/v1/agent/heartbeat
2) If fails, stop.
## Work
1) Fetch assigned task. If none, post idle and return HEARTBEAT_OK.
2) Run continuously: PLAN → IMPLEMENT → VALIDATE → PATCH to review.
3) Activate skills: frontend-design, feature-dev, superpowers, simplify.
4) Post progress comment every 30 min while working.
5) Stay in your role. Ask @lead for cross-role work.
6) For validation details: read $SHARED_WORKSPACE/docs/validation-cookbook.md
## HEARTBEAT_OK
Say HEARTBEAT_OK only when check-in succeeded and work is complete or reported.
```

**Step 5: Commit**

```bash
git add backend/templates/ backend/tests/
git commit -m "refactor(heartbeat): shrink worker template from 19KB to 8-10KB"
```

---

## Task 3: Structured Task Handoff Files

**Files:**
- Create on gateway shared workspace: `/root/.openclaw/workspace/shared/tasks/README.md` (schema documentation)
- Modify: `backend/templates/BOARD_HEARTBEAT.md.j2` (reference handoff files)

**Problem:** MEMORY.md is thin prose. Agents lose context between sessions. Article recommends structured handoff artifacts.

**Step 1: Define handoff schema**

Create `/root/.openclaw/workspace/shared/tasks/README.md`:
```markdown
# Task Handoff Schema
Each in-progress task has a file: shared/tasks/<task_id>.md

## Required Fields
- task_id: UUID
- objective: one sentence
- sprint_contract: what "done" looks like for this sprint
- files_touched: list of files modified
- commands_run: last 5 commands with output summary
- last_build_result: pass/fail + error if fail
- deployment_target: URL
- blockers: list or "none"
- next_action: exact next step to take
```

**Step 2: Update template to read/write handoff files**

Add to worker workflow:
```
Before starting work, read handoff file if it exists:
  cat $SHARED_WORKSPACE/tasks/$TASK_ID.md 2>/dev/null
After each work session, update the handoff file with current state.
```

Deployment note: `shared/tasks/` lives on the gateway workspace, not in this repo.

**Step 3: Commit**

```bash
git commit -m "feat(handoff): add structured task handoff files in shared workspace"
```

---

## Task 4: High-Level Task Specs

**Problem:** 500+ word task descriptions with inline code samples cause anchoring. Article says high-level specs work better.

**Action:** This is a process change, not a code change. Document the rule:

Create `/root/.openclaw/workspace/shared/docs/task-spec-guidelines.md`:
```markdown
# Task Specification Guidelines
- Goal: one sentence
- Constraints: tech/deployment/compatibility limits
- Acceptance checks: 3-5 bullet points (what "done" looks like)
- References: links to specs, plans, shared docs
- Do NOT include inline code samples — put those in linked spec files
- Do NOT specify implementation approach — let the agent decide
```

Deployment note: this is a plain markdown doc in the gateway `shared/docs/` workspace, not a repo-backed template.

---

## Task 5: Design-Quality Prompt Anchoring

**Problem:** No quality anchor in task specs. Article shows "museum quality" phrasing shapes output.

**Step 1: Add quality anchor to template**

In the worker workflow section, add:
```
Design quality anchor: Build interfaces that feel professionally designed for a government operations center.
Think Linear meets Bloomberg Terminal — information-dense but never cluttered, calm but not boring.
Never produce generic AI aesthetics (default gradients, cookie-cutter layouts, Inter/Roboto fonts).
```

**Step 2: Add to task creation guidance**

Update lead template task creation section to include quality anchor in task descriptions.

---

## Task 6: QA Grading Rubric

**Files:**
- Create on gateway shared workspace: `/root/.openclaw/workspace/shared/qa/rubric.md`
- Modify: QA-E2E IDENTITY.md (reference rubric)
- Modify: QA-Unit IDENTITY.md (reference rubric)

**Step 1: Create rubric**

```markdown
# QA Grading Rubric

## Frontend Tasks
| Dimension | Weight | Fail Threshold | How to Check |
|-----------|--------|----------------|-------------|
| Spec Fidelity | 25% | <6/10 | Compare against plan docs |
| Interaction | 20% | <7/10 | Click/drag/toggle all features |
| Visual Quality | 20% | <7/10 | Typography, color, spacing, motion |
| Responsiveness | 10% | <5/10 | Resize viewport to mobile/tablet |
| Console/Network | 15% | Any error = fail | Chrome MCP list_console_messages |
| Code Quality | 10% | Typecheck fails = fail | tsc --noEmit, npm run lint |

## Backend Tasks
| Dimension | Weight | Fail Threshold | How to Check |
|-----------|--------|----------------|-------------|
| API Contract | 30% | Missing endpoint = fail | curl all endpoints from spec |
| Data Accuracy | 25% | Wrong data = fail | Compare API response vs DB |
| Error Handling | 15% | Crash on bad input = fail | Send invalid params |
| Performance | 10% | >5s response = fail | time curl |
| Test Coverage | 20% | <80% pass rate = fail | pytest --tb=short |

## Hard Fail Rules (any = REJECT)
- Console errors > 0 (excluding warnings and third-party deprecation notices)
- Typecheck fails
- Build fails
- Service returns 5xx
- Data mismatch vs database
```

Deployment note: keep the rubric in `shared/qa/` on the gateway and reference it from the QA identities there.

---

## Task 7: Few-Shot QA Calibration

**Files:**
- Create on gateway shared workspace: `/root/.openclaw/workspace/shared/qa/examples/pass-frontend-kanban.md`
- Create on gateway shared workspace: `/root/.openclaw/workspace/shared/qa/examples/fail-frontend-regression.md`
- Create on gateway shared workspace: `/root/.openclaw/workspace/shared/qa/examples/pass-backend-api.md`
- Create on gateway shared workspace: `/root/.openclaw/workspace/shared/qa/examples/fail-backend-endpoint.md`

**Problem:** QA checks pass/fail without calibrated judgment. Article says few-shot examples align evaluator.

**Action:** Extract 4 labeled examples from our actual history (Kanban task approval, UX regression rejection, API spec validation, comments endpoint 404).

---

## Task 8: Cost Tracking Per Task (Future Investigation)

**No implementation in this plan.** Descope this until the gateway API for session token usage is understood.

**Problem:** No per-task cost attribution today, and it is not yet verified that the gateway exposes session token usage in a stable API shape that the backend can consume.

**Next step:** Investigate gateway/session APIs first:
- Confirm whether per-session token usage is exposed via API, persisted metadata, or logs.
- Decide whether task comments should ingest usage directly, or whether the gateway needs a new endpoint/field.
- Only then scope backend changes in `backend/app/api/tasks.py` and `backend/app/schemas/tasks.py`.

---

## Task 9: Agent Consolidation Analysis (Future)

**No code changes.** Document analysis for future reference.

Create `docs/plans/2026-03-25-agent-consolidation-analysis.md`:
- Current: 7 agents with 30m heartbeats = 4 layers of latency
- Proposed: 3 functional roles (Planner/Supervisor, Generator/Programmer, Evaluator/QA)
- Benefit: fewer agents = fewer token rotations, simpler routing, less Supervisor overhead
- Risk: less specialization, harder to parallelize
- Decision: defer until items 0-3 deliver measurable improvement

---

## Dependency Graph

```
Task 0 (liveness) ──→ Task 1 (continuous workflow) ──→ Task 2 (shrink template)
                                                       │
                                                       ├──→ Task 3 (handoff files)
                                                       ├──→ Task 5 (quality anchor)

Task 4 (task specs) ──→ Task 6 (QA rubric) ──→ Task 7 (few-shot calibration)

Task 8 (cost tracking) — future investigation, independent
Task 9 (consolidation) — future, no dependencies
```

## Estimated Effort

| Task | Effort | Impact |
|------|--------|--------|
| 0. Liveness | 15 min | Unblocks 30m heartbeats |
| 1. Continuous workflow runs | 30 min | Eliminates 90-min minimum per task |
| 2. Shrink template | 3-4 hours | Lower prompt overhead without losing critical guidance |
| 3. Handoff files | 30 min | Better context preservation |
| 4. Task specs | 15 min | Process doc only |
| 5. Quality anchor | 15 min | Shapes output quality |
| 6. QA rubric | 2 hours | Calibrated evaluation |
| 7. Few-shot calibration | 1 hour | Aligned QA judgment |
| 8. Cost tracking | Future | Requires gateway API investigation first |
| 9. Consolidation | 0 (doc only) | Future reference |
