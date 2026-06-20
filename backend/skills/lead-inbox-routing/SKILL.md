---
name: lead-inbox-routing
description: Use when a board lead must route inbox work, decide whether decomposition is required, create planned subtasks, or assign new work to the right role.
---

# Lead Inbox Routing

Use this only as a board lead for `inbox` tasks or for
`lead-next-action-gate` actions that return `route_inbox`.

## Inbox Invariant — no agent work on the `inbox` column

`inbox` is the lead's **unassigned** triage backlog. **A task in `inbox` must
never carry an `assigned_agent_id`.** Agents begin working any task assigned to
them, so an assigned-but-`inbox` task makes a worker or validator run against
the inbox column — which is forbidden. Enforce on both create and route:

- **Not yet ready to start** (e.g. waiting on `depends_on_task_ids`): create it
  **UNASSIGNED**. It sits as inbox backlog with no owner until you route it.
- **Ready to start now**: assign and activate in the **same** PATCH —
  `{"assigned_agent_id":"UUID","status":"in_progress"}` (the backend
  auto-converts the target to `review` for validators). Never assign without
  the status move.

If you ever see a task that is both `inbox` and assigned, fix it immediately —
route it (assign + activate) or clear the assignment. Do not leave an agent
working the inbox column.

## Subtask Creation

Before creating a task, check the current task list for similar titles. Do not
create duplicates. Create it **UNASSIGNED** (inbox backlog); assign + activate
when you route it (see Inbox Invariant).

```bash
curl -fsS -X POST "$BASE_URL/api/v1/agent/boards/$BOARD_ID/tasks" \
  -H "X-Agent-Token: $AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"T","description":"D","priority":"high"}'
```

## Decomposition Gate

Run this first for new inbox tasks or tasks assigned to the lead. Route to
Architect if any condition is true:

- five or more acceptance criteria
- multi-component deliverable
- two or more deliverables
- missing acceptance-criteria list
- new architecture: data model, API contract, auth scope, or state-machine
  status

Skip decomposition only when the task is a single deliverable, has fewer than
five acceptance criteria, follows a shipped pattern, and needs no architectural
decision. If skipping, record `[NO-DECOMPOSE: <reason>]`.

Architect route:

```json
{"assigned_agent_id":"ARCHITECT_ID","status":"in_progress"}
```

The backend's lead-path validator only allows the `inbox → in_progress`
shortcut on the lead path; it then **auto-corrects** the target to
`review` when the assigned agent is a review-only validator (Architect
or QA). PATCHing `status:"review"` directly returns 403 with
`Lead status gate failed: board leads can only change status when the
current task status is review`.

Nudge text:

```text
DECOMPOSE $TASK_ID. Post per-AC to subtask map with roles, review_packet_type, validation_target*, and dependency order. Do NOT implement or move status.
```

If Architect already posted a plan, skip the route and run umbrella lifecycle.

## Umbrella Lifecycle

1. Create each subtask from Architect's plan **UNASSIGNED** (no
   `assigned_agent_id` — inbox is backlog; see Inbox Invariant), with
   `depends_on_task_ids`, copied acceptance criteria, **and `parent_task_id`
   pointing at the umbrella**. The `parent_task_id` link is what lets the
   backend cascade: when the umbrella moves to `done`/`cancelled`, the
   `task.parent_terminated` activity event fires listing any non-terminal
   children, and `/lead/next-action` will return `cancel_orphan_child` so
   the obsolete child gets cleaned up automatically.
2. Mark pure-container umbrella tasks with **one** `UMBRELLA_RETIRED` comment
   listing the created subtask ids. Leads cannot cancel tasks — only operators
   can (`POST .../tasks/{id}` with `{"status":"cancelled"}` returns 403 for
   agents). Leave the umbrella in `inbox` after posting the marker. Skip
   re-commenting on later ticks if a `UMBRELLA_RETIRED` marker is already
   present.
3. If the umbrella has its own artifact, link real dependencies through
   `depends_on_task_ids` or an `OperatorDecision`. Do not write `is_blocked`
   directly.
4. Route subtasks as normal tasks.

Subtask create payload shape:

```json
{
  "title": "<subtask title>",
  "description": "<copied AC>",
  "parent_task_id": "<umbrella UUID>",
  "depends_on_task_ids": ["<sibling UUID>"]
}
```

Subtasks are created **unassigned in `inbox`** (backlog). Step 4 ("Route
subtasks as normal tasks") is where you assign an owner AND move the task out
of `inbox` in one PATCH — one subtask at a time as its dependencies clear, per
the Inbox Invariant. `parent_task_id` is rejected by the backend if it points
at a task on a different board or at the subtask itself.

## Normal Task Routing

- Frontend/UI/product surface work -> Programmer-Frontend.
- Backend/API/persistence/auth/service work -> Programmer-Backend.
- Infra/deploy/live-target/build-drift/operator-target work -> DevOps.
- Review, QA, or Architect validation tasks already in `review` status -> just
  assign: `{"assigned_agent_id":"UUID"}` (no status change). The current `review`
  status accepts assignment without re-PATCHing status.
- Inbox task being routed to a validator (Architect, QA-Unit, QA-E2E) -> patch
  `{"assigned_agent_id":"UUID","status":"in_progress"}`. The lead-path validator
  ONLY accepts the inbox-shortcut to `in_progress`; the backend then
  auto-converts the target to `review` when the assignee has
  `validation_flow=qa_validation` or `dev_acp_flow=review_only`. PATCHing
  `status:"review"` directly on an inbox task returns 403 with `Lead status
  gate failed: board leads can only change status when the current task status
  is review`.
- Unassigned implementation inbox task plus idle implementation agent -> patch
  `{"assigned_agent_id":"UUID","status":"in_progress"}` and nudge once.

Use board-visible agent UUIDs from the current health scan. Do not hardcode
agent ids.
