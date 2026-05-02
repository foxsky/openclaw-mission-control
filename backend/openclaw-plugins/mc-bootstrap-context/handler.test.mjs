// Standalone smoke test for mc-bootstrap-context handler. No pytest /
// vitest harness — runs as `node handler.test.mjs` from the plugin dir
// or any cwd. Covers the pure logic (agent-id inference, role split,
// markdown rendering) and exercises the handler's side-effect contract
// against a stub gateway-event payload with fetchJSON intercepted.
//
// Skipped on purpose: real HTTP calls. The handler's network path is
// validated end-to-end on the gateway host, not here.

import handler from "./handler.js";

let passed = 0;
let failed = 0;

function expect(label, actual, predicate) {
  const ok = typeof predicate === "function" ? predicate(actual) : actual === predicate;
  if (ok) {
    passed++;
    return;
  }
  failed++;
  console.error(`FAIL: ${label}\n  got: ${JSON.stringify(actual)?.slice(0, 200)}`);
}

function makeBootstrapEvent(overrides = {}) {
  const base = {
    type: "agent",
    action: "bootstrap",
    sessionKey: "agent:lead-05002170-201b-4c66-bae1-26c0c833f206:lead",
    timestamp: Date.now(),
    messages: [],
    context: {
      sessionKey: "agent:lead-05002170-201b-4c66-bae1-26c0c833f206:lead",
      workspaceDir: "/root/.openclaw/agents/lead-05002170-201b-4c66-bae1-26c0c833f206",
      bootstrapFiles: [],
      cfg: {
        hooks: {
          internal: {
            entries: {
              "mc-bootstrap-context": {
                enabled: true,
                env: {
                  MC_BASE_URL: "http://test:8000",
                  BOARD_ID: "board-uuid",
                  MC_OPERATOR_TOKEN: "tok",
                  TIMEOUT_MS: "1500",
                },
              },
            },
          },
        },
      },
    },
  };
  return mergeDeep(base, overrides);
}

function mergeDeep(a, b) {
  if (!b) return a;
  const out = { ...a };
  for (const [k, v] of Object.entries(b)) {
    if (v && typeof v === "object" && !Array.isArray(v) && a[k]) {
      out[k] = mergeDeep(a[k], v);
    } else {
      out[k] = v;
    }
  }
  return out;
}

// Replace `node:http` and `node:https` import resolution by stubbing
// the global fetch path. The handler dynamically imports them — this
// approach won't intercept that. Instead, drive the test against a
// real loopback server listening on a free port.

import http from "node:http";

async function withStubServer(handler, body, status = 200) {
  return await new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(typeof body === "string" ? body : JSON.stringify(body));
    });
    server.listen(0, "127.0.0.1", async () => {
      const addr = server.address();
      try {
        const result = await handler(`http://127.0.0.1:${addr.port}`);
        server.close(() => resolve(result));
      } catch (err) {
        server.close(() => reject(err));
      }
    });
  });
}

// --- 1. ignores non-bootstrap events
{
  const ev = makeBootstrapEvent({ type: "command", action: "new" });
  await handler(ev);
  expect("non-bootstrap event leaves bootstrapFiles untouched", ev.context.bootstrapFiles.length, 0);
}

// --- 2. ignores non-MC agents (main, repro-*, eval-*, mc-gateway-*)
{
  for (const id of ["main", "repro-test-agent", "eval-lead-050", "mc-gateway-3821a85a"]) {
    const ev = makeBootstrapEvent({
      sessionKey: `agent:${id}:s`,
      context: { sessionKey: `agent:${id}:s`, workspaceDir: `/root/.openclaw/agents/${id}` },
    });
    await handler(ev);
    expect(`non-MC agent ${id} leaves bootstrapFiles empty`, ev.context.bootstrapFiles.length, 0);
  }
}

// --- 3. missing config → no-op (no throw)
{
  const ev = makeBootstrapEvent({
    context: { cfg: { hooks: { internal: { entries: {} } } } },
  });
  await handler(ev);
  expect("missing config is a clean no-op", ev.context.bootstrapFiles.length, 0);
}

// --- 4. lead bootstrap with stubbed MC backend → injects MC_RUNTIME_BRIEF.md
{
  await withStubServer(
    async (baseUrl) => {
      const ev = makeBootstrapEvent({
        context: { cfg: { hooks: { internal: { entries: { "mc-bootstrap-context": { enabled: true, env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", MC_OPERATOR_TOKEN: "t", TIMEOUT_MS: "1500" } } } } } } },
      });
      await handler(ev);
      const f = ev.context.bootstrapFiles[0];
      expect("lead injection produces one file", ev.context.bootstrapFiles.length, 1);
      expect("lead file is named MC_RUNTIME_BRIEF.md", f?.name, "MC_RUNTIME_BRIEF.md");
      expect("lead file content is markdown lead brief", f?.content, (s) => typeof s === "string" && s.startsWith("# MC Runtime Brief — Lead"));
      expect("lead file content surfaces action", f?.content, (s) => s.includes("inspect_review_gates"));
      expect("lead file content surfaces task title", f?.content, (s) => s.includes("Bug: agents.update"));
    },
    {
      action: "inspect_review_gates",
      reason_code: "approved_review_needs_done_gate",
      action_required: true,
      task_id: "c611f081",
      task_title: "Bug: agents.update RPC wipes",
      task_status: "review",
      details: { approval_state: "approved" },
    },
  );
}

// --- 5. worker bootstrap with stubbed MC backend → injects task table
{
  await withStubServer(
    async (baseUrl) => {
      const ev = makeBootstrapEvent({
        sessionKey: "agent:mc-3461451b-5824-4ed0-872c-d14d5d2be107:s",
        context: {
          sessionKey: "agent:mc-3461451b-5824-4ed0-872c-d14d5d2be107:s",
          workspaceDir: "/root/.openclaw/agents/mc-3461451b-5824-4ed0-872c-d14d5d2be107",
          cfg: { hooks: { internal: { entries: { "mc-bootstrap-context": { enabled: true, env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", MC_OPERATOR_TOKEN: "t", TIMEOUT_MS: "1500" } } } } } },
        },
      });
      await handler(ev);
      const f = ev.context.bootstrapFiles[0];
      expect("worker injection produces one file", ev.context.bootstrapFiles.length, 1);
      expect("worker file content has worker brief header", f?.content, (s) => typeof s === "string" && s.startsWith("# MC Runtime Brief — Worker"));
      expect("worker file content lists task title", f?.content, (s) => s.includes("Implement nav active state"));
      expect("worker file content shows task count", f?.content, (s) => s.includes("**2** task(s)"));
    },
    {
      items: [
        { id: "abc12345-aaaa-bbbb-cccc-dddddddddddd", title: "Implement nav active state", status: "in_progress", priority: "high" },
        { id: "def67890-aaaa-bbbb-cccc-dddddddddddd", title: "Fix locale gap", status: "review", priority: "medium" },
      ],
      total: 2,
    },
  );
}

// --- 6. HTTP error → no injection, no throw
{
  await withStubServer(
    async (baseUrl) => {
      const ev = makeBootstrapEvent({
        context: { cfg: { hooks: { internal: { entries: { "mc-bootstrap-context": { enabled: true, env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", MC_OPERATOR_TOKEN: "t", TIMEOUT_MS: "1500" } } } } } } },
      });
      await handler(ev);
      expect("HTTP 500 does not inject", ev.context.bootstrapFiles.length, 0);
    },
    "internal error",
    500,
  );
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
