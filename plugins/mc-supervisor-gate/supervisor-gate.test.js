import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createSupervisorGate,
  extractText,
  hasActionRequiredSignal,
  hasMutationIntent,
  isOkShortcut,
} from "./supervisor-gate.js";

const supervisorCtx = {
  agentId: "lead-05002170-201b-4c66-bae1-26c0c833f206",
  sessionKey: "session-a",
  runId: "run-a",
};

test("detects Mission Control action-required responses", () => {
  assert.equal(
    hasActionRequiredSignal({
      content: [{ type: "text", text: '{"action_required":true,"action":"inspect_review_gates"}' }],
    }),
    true,
  );
  assert.equal(
    hasActionRequiredSignal("LEAD_NEXT_ACTION_REQUIRED approved_review_needs_done_gate"),
    true,
  );
  assert.equal(hasActionRequiredSignal('{"action_required":false}'), false);
});

test("detects mutating MC commands without treating read-only probes as mutations", () => {
  assert.equal(hasMutationIntent("exec", { command: "curl -s http://127.0.0.1/api/lead/next-action" }), false);
  assert.equal(hasMutationIntent("exec", { command: "curl -X PATCH http://127.0.0.1/api/mission-control/tasks/123" }), true);
  assert.equal(
    hasMutationIntent("exec", {
      command: "python3 /root/.openclaw/workspace/hqctl.py promote-to-review VP-08",
    }),
    true,
  );
});

test("recognizes only short heartbeat OK replies as shortcuts", () => {
  assert.equal(isOkShortcut("HEARTBEAT_OK"), true);
  assert.equal(isOkShortcut("NO_REPLY"), true);
  assert.equal(isOkShortcut("Heartbeat OK, no action required."), true);
  assert.equal(isOkShortcut("I found action_required true and created task 123."), false);
});

test("asks for one revision when action was required and no mutation happened", () => {
  const gate = createSupervisorGate();
  gate.beforeToolCall({ toolName: "exec", params: { command: "curl -s http://mc/api/lead/next-action" }, toolCallId: "tc1" }, supervisorCtx);
  gate.toolResultPersist(
    {
      toolName: "exec",
      toolCallId: "tc1",
      message: { content: [{ type: "text", text: '{"action_required":true}' }] },
    },
    { ...supervisorCtx, toolCallId: "tc1" },
  );

  const result = gate.beforeAgentFinalize({ lastAssistantMessage: "HEARTBEAT_OK" }, supervisorCtx);
  assert.equal(result.action, "revise");
  assert.match(result.reason, /action_required/);
});

test("allows OK after a mutating MC action", () => {
  const gate = createSupervisorGate();
  gate.beforeToolCall({ toolName: "exec", params: { command: "curl -s http://mc/api/lead/next-action" }, toolCallId: "tc1" }, supervisorCtx);
  gate.toolResultPersist({ toolName: "exec", toolCallId: "tc1", message: '{"action_required":true}' }, supervisorCtx);
  gate.beforeToolCall(
    { toolName: "exec", params: { command: "curl -X POST http://mc/api/mission-control/tasks" }, toolCallId: "tc2" },
    supervisorCtx,
  );

  assert.equal(gate.beforeAgentFinalize({ lastAssistantMessage: "HEARTBEAT_OK" }, supervisorCtx), undefined);
});

test("does not loop beyond the configured revision budget", () => {
  const gate = createSupervisorGate({ maxRevisionsPerRun: 1 });
  gate.beforeToolCall({ toolName: "exec", params: { command: "curl -s http://mc/api/lead/next-action" }, toolCallId: "tc1" }, supervisorCtx);
  gate.toolResultPersist({ toolName: "exec", toolCallId: "tc1", message: '{"action_required":true}' }, supervisorCtx);

  assert.equal(gate.beforeAgentFinalize({ lastAssistantMessage: "NO_REPLY" }, supervisorCtx)?.action, "revise");
  assert.equal(gate.beforeAgentFinalize({ lastAssistantMessage: "NO_REPLY" }, supervisorCtx), undefined);
});

test("extracts text from nested tool results", () => {
  assert.equal(
    extractText({
      content: [
        { type: "text", text: "alpha" },
        { type: "json", json: { beta: true } },
      ],
    }).includes("beta"),
    true,
  );
});
