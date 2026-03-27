# QA Grading Rubric Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create the QA grading rubric file (`shared/qa/rubric.md`) on the gateway and update QA-E2E's IDENTITY.md to enforce scored evaluations with Chrome MCP evidence requirements.

**Architecture:** Single markdown file deployed to the gateway shared workspace. QA-E2E reads it before every validation. The rubric defines per-dimension scoring with hard fail thresholds and mandatory Chrome MCP tool calls as evidence. The template already tells QA-E2E to "Score using shared/qa/rubric.md" — we just need to create the file.

**Tech Stack:** Markdown file on gateway (192.168.2.60), scp deployment, QA-E2E IDENTITY.md update.

---

## Task 1: Create the rubric file

**Files:**
- Create: `/tmp/rubric.md` (local draft)
- Deploy to: `root@192.168.2.60:/root/.openclaw/workspace/shared/qa/rubric.md`

**Step 1: Write the rubric**

Create `/tmp/rubric.md` with the full QA grading rubric. The rubric must be actionable by an LLM agent — every dimension has specific Chrome MCP tool calls that prove the score.

```markdown
# QA Grading Rubric

Read this ENTIRE file before starting any validation. Score every dimension. Post scores in your validation comment.

## Evidence Rules

**QA-E2E (browser validation):**
- You MUST use Chrome MCP for ALL UI/frontend tasks.
- Each dimension requires specific `mcp__chrome-devtools__` tool output as evidence.
- Bundle/code grep is NEVER valid evidence for UI dimensions.
- Re-validate EVERY time. Do NOT reuse previous results from memory.
- If the URL is unreachable, report FAIL — that IS the bug.

**QA-Unit (mechanical checks):**
- Run typecheck, lint, and tests in the actual workspace.
- Post exact command output as evidence.

## Review Loop

1. Supervisor routes task to QA after Architect review (or directly for bugfixes/<3 files).
2. QA scores each dimension (0-10) using Chrome MCP evidence.
3. If ANY hard fail → REJECT with specific dimension and evidence.
4. If all pass → POST with scores and evidence.
5. On rejection: Supervisor routes back to developer. Developer fixes and asks @lead to re-route.
6. **Maximum 3 reject/fix/retest rounds.** After round 3, Supervisor escalates to @Miguel with rejection history.

## Frontend Task Scoring

| Dimension | Weight | Fail Threshold | Chrome MCP Evidence Required |
|-----------|--------|----------------|------------------------------|
| Spec Fidelity | 15% | <6/10 | Compare rendered DOM against task description acceptance criteria |
| Interaction | 15% | <7/10 | `mcp__chrome-devtools__click` on interactive elements, verify state changes |
| Visual Quality | 15% | <7/10 | `mcp__chrome-devtools__take_screenshot` — check typography, color, spacing, alignment |
| Originality | 20% | <6/10 | Review for custom design decisions vs generic/cookie-cutter patterns |
| Craft | 15% | <6/10 | Code cleanliness (no hacks, no TODO/FIXME in production paths) |
| Console/Network | 10% | Any error = FAIL | `mcp__chrome-devtools__list_console_messages` types `["error"]` — must be ZERO |
| Code Quality | 5% | Typecheck fails = FAIL | `npm run typecheck` and `npm run lint` — must pass |
| Responsiveness | 5% | <5/10 | `mcp__chrome-devtools__resize_page` to 375x812 (mobile) — check layout |

### Frontend Scoring Guide

**10/10:** Exceeds spec. Polished, delightful, production-ready.
**8-9/10:** Meets spec fully. Minor polish opportunities.
**6-7/10:** Meets spec with gaps. Functional but rough.
**4-5/10:** Partially meets spec. Missing features or broken states.
**1-3/10:** Major issues. Broken, unusable, or spec mismatch.
**0/10:** Not implemented or completely wrong.

## Backend Task Scoring

| Dimension | Weight | Fail Threshold | Evidence Required |
|-----------|--------|----------------|-------------------|
| API Contract | 30% | Missing endpoint = FAIL | `curl` each endpoint from spec, verify status codes + response shape |
| Data Accuracy | 25% | Wrong data = FAIL | Compare API response vs expected from task description |
| Error Handling | 15% | Crash on bad input = FAIL | Send invalid params, verify 4xx not 5xx |
| Performance | 10% | >5s response = FAIL | `time curl` on each endpoint |
| Test Coverage | 20% | <80% pass rate = FAIL | `pytest --tb=short` output |

## Hard Fail Rules (ANY = automatic REJECT)

- Console errors > 0 (excluding third-party deprecation warnings)
- Typecheck fails (`npm run typecheck` exits non-zero)
- Build fails (`npm run build` exits non-zero)
- Service returns 5xx on valid requests
- URL unreachable (CORS, network, server down)
- No Chrome MCP evidence provided for UI tasks

## Validation Comment Format

Post as a task comment with this exact format:

```
QA-E2E validation

BUILD: index-HASH.js
URL: http://192.168.2.63:3000/boards/BOARD_ID

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Spec Fidelity | X/10 | [what was checked] |
| Visual Quality | X/10 | [screenshot reference] |
| Interaction | X/10 | [what was clicked/tested] |
| Console/Network | X/10 | [error count] |
| Code Quality | X/10 | [typecheck/lint result] |
| Originality | X/10 | [design decisions noted] |
| Craft | X/10 | [code observations] |
| Responsiveness | X/10 | [mobile viewport result] |

**Total: XX/100 (weighted)**
**Verdict: PASS/FAIL**
**Fail reason:** [dimension that triggered fail, if any]

@lead
```

## Be SKEPTICAL

LLMs tend toward leniency — fight that instinct. If evidence is ambiguous, score lower. If you can't verify a dimension via Chrome MCP, score 0 for that dimension and explain why. Never give a PASS based on "it probably works" — only on verified evidence.
```

**Step 2: Deploy to gateway**

```bash
ssh root@192.168.2.60 'mkdir -p /root/.openclaw/workspace/shared/qa'
scp /tmp/rubric.md root@192.168.2.60:/root/.openclaw/workspace/shared/qa/rubric.md
```

Verify:
```bash
ssh root@192.168.2.60 'cat /root/.openclaw/workspace/shared/qa/rubric.md | wc -l'
```
Expected: ~80+ lines

**Step 3: Commit the rubric to the repo**

```bash
mkdir -p docs/qa
cp /tmp/rubric.md docs/qa/rubric.md
git add docs/qa/rubric.md
git commit -m "feat(qa): add QA grading rubric with Chrome MCP evidence requirements"
```

---

## Task 2: Update QA-E2E IDENTITY.md

**Files:**
- Modify on gateway: `/root/.openclaw/workspace/workspace-mc-dd1abee5-*/IDENTITY.md`

**Step 1: Add rubric reference to QA-E2E IDENTITY**

SSH to gateway and append rubric instructions:

```bash
ssh root@192.168.2.60 'cat >> /root/.openclaw/workspace/workspace-mc-dd1abee5-*/IDENTITY.md << "EOF"

## QA Rubric
Before EVERY validation, read: `$SHARED_WORKSPACE/qa/rubric.md`
Score EVERY dimension in the rubric. Post the scoring table in your validation comment.
Hard fail rules are non-negotiable. Any hard fail = REJECT regardless of other scores.
EOF
'
```

**Step 2: Verify**

```bash
ssh root@192.168.2.60 'tail -6 /root/.openclaw/workspace/workspace-mc-dd1abee5-*/IDENTITY.md'
```

Expected: Shows the QA Rubric section appended.

---

## Task 3: Update HEARTBEAT template to reference rubric

**Files:**
- Modify: `backend/templates/BOARD_HEARTBEAT.md.j2`

**Step 1: Add rubric reference to QA-E2E nudge message**

In the Supervisor's Step 3 QA-E2E nudge, add "Read $SHARED_WORKSPACE/qa/rubric.md first. Post scored rubric table."

Find the QA-E2E nudge line and update the message to reference the rubric explicitly.

**Step 2: Verify size**

```bash
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('backend/templates'))
r = env.get_template('BOARD_HEARTBEAT.md.j2').render(...)
print(f'Lead: {len(r)} chars')
"
```

Expected: Under 10,500 chars.

**Step 3: Deploy and sync**

```bash
scp backend/templates/BOARD_HEARTBEAT.md.j2 root@192.168.2.64:/home/mcontrol/openclaw-mission-control/backend/templates/
# Sync via MC API
```

**Step 4: Commit**

```bash
git add backend/templates/BOARD_HEARTBEAT.md.j2
git commit -m "feat(heartbeat): reference QA rubric in QA-E2E nudge messages"
```

---

## Task 4: E2E validation test

**Step 1: Trigger a QA-E2E validation to test the rubric**

Nudge QA-E2E to validate a current review task (or create a test scenario). Verify that QA-E2E:
1. Reads the rubric file
2. Posts a scored table with all dimensions
3. Uses Chrome MCP evidence for each dimension
4. Applies hard fail rules correctly

**Step 2: Verify the output format matches the rubric template**

Check the task comment for the scoring table format.

---
