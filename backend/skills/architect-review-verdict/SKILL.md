---
name: architect-review-verdict
description: Use when an Architect or review-only board agent must review submitted work, decomposition, architecture, API, auth, or state-machine changes.
---

# Architect Review Verdict

Use this only in review-only mode. The Architect reviews and posts verdicts;
the Architect does not implement, deploy, spawn implementation workers, move
implementation status, or resubmit worker output.

## Review Inputs

Read the task description, comments, declared `review_packet_type`,
`validation_target*`, submitted commit/diff, and worker evidence packet. If the
target or evidence packet is absent, verdict is FAIL or INCONCLUSIVE; do not
fill the gap with source-level inference.

## PASS Gates

- Findings map to acceptance criteria or declared contracts.
- Frontend/UI PASS requires the submitted frontend browser evidence packet:
  target URL, navigation/snapshot, DOM/raw-key scan, console/network output,
  interaction proof, responsive proof when applicable, and source commit SHA matching the live `/__build.sha`. Do NOT cite the asset filename — that hash churns on every Vite rebuild and creates false-positive deploy-mismatch rejections.
- Backend/API/persistence PASS requires runtime evidence: exact endpoint
  status/body, non-HTTP trigger/log/readback, persistence readback,
  migration/schema proof, and deploy target/version proof when applicable.
- Source grep and bundle grep are rejected evidence for rendered/live UI PASS.
- If `review_packet_type` is absent, `other`, or the task has
  `required_roles=[]`, do not post a PASS that implies the task is ready for
  done. Route to lead/operator to correct the packet type first.
- For decomposition PASS, structured evidence must include
  `planned_child_task_ids` for every created child task or
  `no_child_tasks_required:true`.
- **Verbatim spec-value match**: when the spec quotes a specific value or
  property (e.g., `rotateY(-14deg)`, `mb-12`, `lg:left-8`, `perspective: 2200px`,
  `transform-origin: left center`), your evidence row for that AC must include
  both the **spec quote** and the **live measurement** on the same line, and
  FAIL if they differ — unless the spec explicitly allows variation
  (e.g., `or measured equivalent`). The `or measured equivalent` clause covers
  numeric variation (e.g., `rotateY(-16deg)` for `-14deg`, `perspective: 2400`
  for `2200`); it does **not** cover direction inversion, pivot-axis flip, or
  substitution of a named CSS class for a different one. Direction-mirrored
  transforms (`rotateY(+N)` vs `rotateY(-N)` at mirrored origin) are
  structurally different, not "measured equivalent". When you observe a
  structural deviation but the AC otherwise reads as met, the verdict is FAIL
  with `blocking_owner` set to the implementer, not PASS with a non-blocking
  note.

## Rework Diagnosis (mandatory on the SECOND consecutive review of the same task)

If the task's review history shows the implementer has already had ONE prior
FAIL or INCONCLUSIVE verdict from any reviewer on this same scope, your verdict
comment MUST include a **Root-cause measurement** section before any guidance.
Background: production incident 2026-05-06 — task `e4738a7c` (E.02 Responsive
4×3 matrix) burned four PF rework cycles before the Architect measured the
actual overflow source. PF was attacking a feature-card grid; the real culprit
was a 5× `lg:grid-cols-2` section with 20px scrollWidth/clientWidth delta.
Iterative-guess rework wastes ~30 minutes per cycle.

**Required measurement shape:**

- For frontend layout/responsive defects: open the live target at the exact
  failing viewport (e.g., 375px) via Playwright or browser inspection, capture
  computed `scrollWidth`/`clientWidth`/`offsetWidth` on the **specific element
  that produces the symptom**, not on the element the implementer was already
  attacking. Quote both numbers verbatim.
- For source-level defects: identify the file path + line range causing the
  observed behavior. Quote the offending construct verbatim. If the implementer
  cited a different file/line on their previous attempt, call out the
  divergence explicitly.
- For runtime/contract defects: run the exact request that fails, quote the
  status code / body / readback verbatim.

Only AFTER the measurement may you give corrective guidance ("apply X to
selector Y"). Guidance without a measured root cause is the failure pattern
that produced the 4× anti-loop on E.02. The measurement makes the corrective
guidance falsifiable.

## AC Quoting Rule (verbatim, no paraphrase)

When you cite an acceptance criterion in your verdict — whether in
`Blocking findings`, `Non-blocking findings`, or the `AC coverage` line —
the AC text must be **copied verbatim** from the task description's
`Acceptance criteria:` section. Do not summarize, reword, simplify, or
describe what the live state shows in place of the AC text — that lets a
deviation pass review by quietly matching what was implemented instead of
what was specified.

If the spec AC says `Preserve/verify dashboard wrapper perspective tilt
from VP-13: rotateY(-14deg), transform-origin: left center`, your finding
quotes exactly that. If the live state has `rotateY(+14deg)` at
`right center`, your finding reports the live value and your verdict is
FAIL — not a reworded AC like "right edge near / left edge recedes" that
turns a deviation into a description.

## Comment Format

Post one task comment:

```text
$AGENT_NAME review for $TASK_ID
Verdict: PASS/FAIL/INCONCLUSIVE
Target: <review target, validation_target*, commit/build>
Scope: cross-cutting | per-AC
AC coverage: <e.g., 1, 3, 5 (architecture); ALL (freshness)>
Evidence reviewed: <worker packet, browser/runtime output, tests, diff>
Blocking findings:
- <file:line or verbatim AC quote> <finding> <required evidence/fix>
Non-blocking findings:
- <item or none>
Evidence gaps:
- <missing packet/output or none>
Verdict basis: PASS means no blocking findings AND every spec-value/AC quoted
verbatim matched the live observation; FAIL means any blocking finding or
verbatim deviation; INCONCLUSIVE means missing evidence packet or source drift.
@lead <one-line routing intent — see "Required @ citation" below>
Lead wake: structured-review-verdict review event
```

## Required @ citation

Every verdict comment MUST end with `@lead` (or `@Supervisor` —
both refer to the board lead and the backend treats them as
equivalent) plus a one-line routing intent BEFORE the `Lead wake:`
line. The structured `/review-events` API auto-wakes the lead for
routing logic, but the prose comment is what the operator sees in
the dashboard, agent text dumps, and scrollback. Without an
explicit citation, the wake is invisible to the human-facing
channel.

**Routing-intent shapes:**

- PASS, all required reviewer roles already passed →
  `@lead approve and move to done`
- PASS, more reviewers still need to run →
  `@lead @<NextReviewer> next gate is <role>`
- FAIL → `@lead move to rework for <owner> (<one-line reason>)`
- INCONCLUSIVE / packet-missing → `@lead route operator (<reason>)`

The lead is the universal router; the agent doesn't need to PATCH status
itself. If a next reviewer is named (e.g., `@QA-E2E`), it's a hint for
the lead — the lead's `lead-review-routing` skill makes the actual
assignment.

**`Scope:` line.** Choose `per-AC` when each acceptance criterion has
its own observable artifact (e.g. each AC names a specific component or
behavior). Choose `cross-cutting` when the finding applies to the change
as a whole (e.g. freshness, traceability, regression, architecture
coherence) — Architect review is often structural rather than checklist-
shaped, and this line tells the lead which lens you used.

**`AC coverage:` line.** Names the acceptance criteria you actually
inspected, by number, with the lens used for each. Use `ALL` when the
finding (typically cross-cutting) applies to every AC. This preserves
per-AC traceability without forcing every cross-cutting finding into a
table row that distorts its scope.

Then use `structured-review-verdict` with `reviewer_role="architect"` and the
matching verdict. The review-events API wakes the lead after the structured
event is stored; do not send a separate board-memory chat or task-comment
nudge.

For a challenged or rejected Architect verdict, use `reviewer-recheck`.
