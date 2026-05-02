// Standalone smoke test for mc-bootstrap-context handler. No pytest /
// vitest harness — runs as `node handler.test.mjs` from the plugin dir
// or any cwd. Covers the pure logic (agent-id inference, role split,
// markdown rendering, per-agent token reading) and exercises the
// handler's side-effect contract against a stub gateway-event payload.
//
// Per project memory feedback_tdd_discipline: this test file expresses
// the contract the handler MUST satisfy. The handler is allowed to
// change so long as these assertions still pass.

import handler from "./handler.js";
import http from "node:http";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

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

// Build a synthetic agent:bootstrap event matching the gateway contract.
// `cfg` is the gateway config snapshot the handler walks for env.
function makeBootstrapEvent({ agentId, sessionKey, env, workspaceDir }) {
  return {
    type: "agent",
    action: "bootstrap",
    sessionKey: sessionKey ?? `agent:${agentId}:session`,
    timestamp: Date.now(),
    messages: [],
    context: {
      sessionKey: sessionKey ?? `agent:${agentId}:session`,
      workspaceDir: workspaceDir ?? `/root/.openclaw/agents/${agentId}`,
      bootstrapFiles: [],
      cfg: {
        hooks: {
          internal: {
            entries: {
              "mc-bootstrap-context": { enabled: true, env },
            },
          },
        },
      },
    },
  };
}

// Spin a temp dir with a workspace-<openclawAgentId>/TOOLS.md file
// holding the AUTH_TOKEN and AGENT_ID lines the handler expects. The
// `mcAgentId` is the MC `agents.id` UUID (no `mc-`/`lead-` prefix) —
// it differs from the OpenClaw agent ID for both lead and worker
// agents. Returns the WORKSPACE_ROOT path.
async function makeTempWorkspaceWithToolsMd(openclawAgentId, token, mcAgentId) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "mc-bootstrap-test-"));
  const wsDir = path.join(root, `workspace-${openclawAgentId}`);
  await fs.mkdir(wsDir, { recursive: true });
  const toolsContent = [
    "# TOOLS.md",
    "",
    "- `BASE_URL=http://test:8000`",
    `- \`AUTH_TOKEN=${token}\``,
    `- \`AGENT_ID=${mcAgentId ?? openclawAgentId}\``,
    "",
  ].join("\n");
  await fs.writeFile(path.join(wsDir, "TOOLS.md"), toolsContent);
  return root;
}

// HTTP stub that captures request headers/url and returns a configured body/status.
async function withStubServer(handlerFn, body, status = 200) {
  return await new Promise((resolve, reject) => {
    const captured = { headers: null, url: null, method: null };
    const server = http.createServer((req, res) => {
      captured.headers = req.headers;
      captured.url = req.url;
      captured.method = req.method;
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(typeof body === "string" ? body : JSON.stringify(body));
    });
    server.listen(0, "127.0.0.1", async () => {
      const addr = server.address();
      try {
        const result = await handlerFn(`http://127.0.0.1:${addr.port}`, captured);
        server.close(() => resolve(result));
      } catch (err) {
        server.close(() => reject(err));
      }
    });
  });
}

const TOK = "test-agent-token-abc123";

// --- 1. ignores non-bootstrap events
{
  const ev = makeBootstrapEvent({
    agentId: "lead-x",
    env: { MC_BASE_URL: "http://x", BOARD_ID: "b" },
  });
  ev.type = "command";
  ev.action = "new";
  await handler(ev);
  expect("non-bootstrap event leaves bootstrapFiles untouched", ev.context.bootstrapFiles.length, 0);
}

// --- 2. ignores non-MC agents
{
  for (const id of ["main", "repro-test-agent", "eval-lead-050", "mc-gateway-3821a85a"]) {
    const ev = makeBootstrapEvent({
      agentId: id,
      env: { MC_BASE_URL: "http://x", BOARD_ID: "b" },
    });
    await handler(ev);
    expect(`non-MC agent ${id} leaves bootstrapFiles empty`, ev.context.bootstrapFiles.length, 0);
  }
}

// --- 3. missing config → no-op
{
  const ev = makeBootstrapEvent({
    agentId: "lead-x",
    env: undefined,
  });
  ev.context.cfg = { hooks: { internal: { entries: {} } } };
  await handler(ev);
  expect("missing config is a clean no-op", ev.context.bootstrapFiles.length, 0);
}

// --- 4. lead bootstrap with token from TOOLS.md → injects MC_RUNTIME_BRIEF.md
{
  const agentId = "lead-aaaa1234-1111-2222-3333-444444444444";
  const mcAgentId = "11111111-2222-3333-4444-555555555555";
  const wsRoot = await makeTempWorkspaceWithToolsMd(agentId, TOK, mcAgentId);
  await withStubServer(
    async (baseUrl, captured) => {
      const ev = makeBootstrapEvent({
        agentId,
        env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", WORKSPACE_ROOT: wsRoot, TIMEOUT_MS: "1500" },
      });
      await handler(ev);
      const f = ev.context.bootstrapFiles[0];
      expect("lead injection produces one file", ev.context.bootstrapFiles.length, 1);
      expect("lead file is MC_RUNTIME_BRIEF.md", f?.name, "MC_RUNTIME_BRIEF.md");
      expect("lead file content is markdown lead brief", f?.content, (s) => typeof s === "string" && s.startsWith("# MC Runtime Brief — Lead"));
      expect("lead file content surfaces action", f?.content, (s) => s.includes("inspect_review_gates"));

      // ── New T5/T6 contracts ──
      expect("request used X-Agent-Token header (not Bearer)", captured.headers, (h) => h?.["x-agent-token"] === TOK);
      expect("request URL hit /api/v1/agent/boards/.../lead/next-action", captured.url, (u) => typeof u === "string" && u.includes("/api/v1/agent/boards/b/lead/next-action"));
    },
    {
      action: "inspect_review_gates",
      reason_code: "approved_review_needs_done_gate",
      action_required: true,
      task_id: "c611f081",
      task_title: "Bug: agents.update RPC wipes",
    },
  );
  await fs.rm(wsRoot, { recursive: true, force: true });
}

// --- 5. worker bootstrap with token from TOOLS.md → injects task table
//
// MC's /agent/boards/.../tasks?assigned_agent_id=<uuid> endpoint
// validates the query param as a UUID. The OpenClaw agent ID
// (`mc-<uuid>`) carries a prefix and is rejected (HTTP 422). The
// MC `agents.id` UUID lives in TOOLS.md as `AGENT_ID=<bare-uuid>`
// alongside the AUTH_TOKEN, and that's what the URL must use.
{
  const openclawAgentId = "mc-aaaa1234-1111-2222-3333-444444444444";
  const mcAgentId = "abcd1234-5678-9012-3456-7890abcdefab";
  const wsRoot = await makeTempWorkspaceWithToolsMd(openclawAgentId, TOK, mcAgentId);
  await withStubServer(
    async (baseUrl, captured) => {
      const ev = makeBootstrapEvent({
        agentId: openclawAgentId,
        env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", WORKSPACE_ROOT: wsRoot, TIMEOUT_MS: "1500" },
      });
      await handler(ev);
      const f = ev.context.bootstrapFiles[0];
      expect("worker injection produces one file", ev.context.bootstrapFiles.length, 1);
      expect("worker file content has worker brief header", f?.content, (s) => typeof s === "string" && s.startsWith("# MC Runtime Brief — Worker"));
      expect("worker file content lists task title", f?.content, (s) => s.includes("Implement nav active state"));
      expect("worker file content shows task count", f?.content, (s) => s.includes("**2** task(s)"));

      // X-Agent-Token + URL uses MC UUID (not OpenClaw agent ID).
      expect("worker request used X-Agent-Token", captured.headers, (h) => h?.["x-agent-token"] === TOK);
      expect("worker URL uses MC UUID from TOOLS.md, not OpenClaw agent id", captured.url, (u) => typeof u === "string" && u.includes(`/api/v1/agent/boards/b/tasks?assigned_agent_id=${mcAgentId}`));
      expect("worker URL does NOT contain mc- prefix", captured.url, (u) => typeof u === "string" && !u.includes("assigned_agent_id=mc-"));
    },
    {
      items: [
        { id: "abc12345-aaaa-bbbb-cccc-dddddddddddd", title: "Implement nav active state", status: "in_progress", priority: "high" },
        { id: "def67890-aaaa-bbbb-cccc-dddddddddddd", title: "Fix locale gap", status: "review", priority: "medium" },
      ],
      total: 2,
    },
  );
  await fs.rm(wsRoot, { recursive: true, force: true });
}

// --- 5b. worker without AGENT_ID in TOOLS.md → no-op (can't build a
// valid query without the MC UUID)
{
  const openclawAgentId = "mc-bbbb0000-1111-2222-3333-444444444444";
  const wsRoot = await fs.mkdtemp(path.join(os.tmpdir(), "mc-bootstrap-test-"));
  const wsDir = path.join(wsRoot, `workspace-${openclawAgentId}`);
  await fs.mkdir(wsDir, { recursive: true });
  await fs.writeFile(
    path.join(wsDir, "TOOLS.md"),
    `# TOOLS.md\n\n- \`AUTH_TOKEN=${TOK}\`\n`,
  );
  const ev = makeBootstrapEvent({
    agentId: openclawAgentId,
    env: { MC_BASE_URL: "http://unreachable.invalid", BOARD_ID: "b", WORKSPACE_ROOT: wsRoot },
  });
  await handler(ev);
  expect("worker without AGENT_ID is a clean no-op", ev.context.bootstrapFiles.length, 0);
  await fs.rm(wsRoot, { recursive: true, force: true });
}

// --- 6. HTTP error → no injection, no throw
{
  const agentId = "lead-eeee0000-1111-2222-3333-444444444444";
  const wsRoot = await makeTempWorkspaceWithToolsMd(agentId, TOK);
  await withStubServer(
    async (baseUrl) => {
      const ev = makeBootstrapEvent({
        agentId,
        env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", WORKSPACE_ROOT: wsRoot, TIMEOUT_MS: "1500" },
      });
      await handler(ev);
      expect("HTTP 500 does not inject", ev.context.bootstrapFiles.length, 0);
    },
    "internal error",
    500,
  );
  await fs.rm(wsRoot, { recursive: true, force: true });
}

// --- 7. missing TOOLS.md → no-op (no throw, no injection)
{
  const agentId = "lead-cccc9999-1111-2222-3333-444444444444";
  const wsRoot = await fs.mkdtemp(path.join(os.tmpdir(), "mc-bootstrap-test-"));
  // intentionally no workspace-<id>/TOOLS.md created
  const ev = makeBootstrapEvent({
    agentId,
    env: { MC_BASE_URL: "http://unreachable.invalid", BOARD_ID: "b", WORKSPACE_ROOT: wsRoot },
  });
  await handler(ev);
  expect("missing TOOLS.md is a clean no-op", ev.context.bootstrapFiles.length, 0);
  await fs.rm(wsRoot, { recursive: true, force: true });
}

// --- 8. TOOLS.md without AUTH_TOKEN line → no-op
{
  const agentId = "lead-dddd8888-1111-2222-3333-444444444444";
  const wsRoot = await fs.mkdtemp(path.join(os.tmpdir(), "mc-bootstrap-test-"));
  const wsDir = path.join(wsRoot, `workspace-${agentId}`);
  await fs.mkdir(wsDir, { recursive: true });
  await fs.writeFile(path.join(wsDir, "TOOLS.md"), "# TOOLS.md\n\n- `BASE_URL=http://x`\n");
  const ev = makeBootstrapEvent({
    agentId,
    env: { MC_BASE_URL: "http://unreachable.invalid", BOARD_ID: "b", WORKSPACE_ROOT: wsRoot },
  });
  await handler(ev);
  expect("TOOLS.md without AUTH_TOKEN is a clean no-op", ev.context.bootstrapFiles.length, 0);
  await fs.rm(wsRoot, { recursive: true, force: true });
}

// --- 9. successful injection emits an observability log line
//
// Why: in production the only journal evidence we have today is the
// generic ``bootstrap-context:18ms`` stage timing — that doesn't tell
// us whether the hook injected MC content or no-op'd. A success log
// like "injected MC_RUNTIME_BRIEF.md for <agent_id> (lead, 215ms)"
// makes deploy / drift verification a journalctl-grep away.
{
  const agentId = "lead-a0a0a0a0-1111-2222-3333-444444444444";
  const mcAgentId = "11111111-2222-3333-4444-555555555555";
  const wsRoot = await makeTempWorkspaceWithToolsMd(agentId, TOK, mcAgentId);
  const calls = [];
  const origLog = console.log;
  console.log = (...args) => {
    calls.push(args.map(String).join(" "));
    origLog.apply(console, args);
  };
  try {
    await withStubServer(
      async (baseUrl) => {
        const ev = makeBootstrapEvent({
          agentId,
          env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", WORKSPACE_ROOT: wsRoot, TIMEOUT_MS: "1500" },
        });
        await handler(ev);
      },
      { action: "x", reason_code: "y", action_required: false },
    );
  } finally {
    console.log = origLog;
  }
  expect("success path emits an observability log line", calls, (c) => c.some((line) => line.includes("[mc-bootstrap-context]") && line.includes("injected") && line.includes(agentId)));
  expect("success log includes role tag", calls, (c) => c.some((line) => line.includes("(lead")));
  expect("success log includes elapsed time", calls, (c) => c.some((line) => /\d+ms/.test(line)));
  await fs.rm(wsRoot, { recursive: true, force: true });
}

// --- 10. (codex #4) path traversal via agent id refuses to read victim TOOLS.md
//
// Stand up a victim workspace inside the same WORKSPACE_ROOT, give it
// a valid TOOLS.md the handler would happily use if it reached it,
// and then bootstrap an attacker agent whose id contains `..`. The
// rejection contract: handler MUST NOT make any HTTP request even
// though a victim file exists at the relative path.
{
  const wsRoot = await fs.mkdtemp(path.join(os.tmpdir(), "mc-bootstrap-test-"));
  // Real victim: workspace-mc-victim with valid creds.
  const victimDir = path.join(wsRoot, "workspace-mc-victim");
  await fs.mkdir(victimDir, { recursive: true });
  await fs.writeFile(
    path.join(victimDir, "TOOLS.md"),
    `# TOOLS.md\n- \`AUTH_TOKEN=victim-token-DO-NOT-LEAK\`\n- \`AGENT_ID=victim-uuid\`\n`,
  );
  // Real attacker: empty workspace, valid TOOLS.md path is via traversal.
  await fs.mkdir(path.join(wsRoot, "workspace-mc-x"), { recursive: true });
  // No legitimate TOOLS.md in attacker's workspace.

  // Spin a stub HTTP server but ASSERT it gets zero requests. If the
  // handler fails to reject the id, it would call MC with the victim's
  // token via path traversal — captured.calls would go up.
  let httpCallCount = 0;
  const server = http.createServer((req, res) => {
    httpCallCount++;
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end("{}");
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const addr = server.address();
  const baseUrl = `http://127.0.0.1:${addr.port}`;

  const bogusIds = [
    "mc-x/../workspace-mc-victim",
    "lead-x/../workspace-mc-victim",
    "mc-aaa\nmc-bbb",
    "mc-aaa\tmc-bbb",
    "mc-",
    "mc-/etc/passwd",
    "lead- ",
    "mc-aaa%00bbb",
    "mc-../etc",
  ];
  for (const id of bogusIds) {
    const ev = makeBootstrapEvent({
      agentId: id,
      env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", WORKSPACE_ROOT: wsRoot, TIMEOUT_MS: "1500" },
    });
    await handler(ev);
    expect(`bogus id rejected: ${JSON.stringify(id)}`, ev.context.bootstrapFiles.length, 0);
  }
  expect("path-traversal attempts produce ZERO HTTP requests", httpCallCount, 0);

  server.close();
  await fs.rm(wsRoot, { recursive: true, force: true });
}

// --- 11. (codex #6) AUTH_TOKEN with surrounding quotes parses cleanly
//
// `AUTH_TOKEN="real-token"` previously captured `"real-token"`
// (quotes included), which MC then rejected as a bad token. Strip
// matching quotes.
{
  const agentId = "lead-b1b1b1b1-1111-2222-3333-444444444444";
  const wsRoot = await fs.mkdtemp(path.join(os.tmpdir(), "mc-bootstrap-test-"));
  const wsDir = path.join(wsRoot, `workspace-${agentId}`);
  await fs.mkdir(wsDir, { recursive: true });
  await fs.writeFile(
    path.join(wsDir, "TOOLS.md"),
    `# TOOLS.md\n\n- \`AUTH_TOKEN="${TOK}"\`\n- \`AGENT_ID="11111111-2222-3333-4444-555555555555"\`\n`,
  );
  await withStubServer(
    async (baseUrl, captured) => {
      const ev = makeBootstrapEvent({
        agentId,
        env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", WORKSPACE_ROOT: wsRoot, TIMEOUT_MS: "1500" },
      });
      await handler(ev);
      expect("quoted AUTH_TOKEN strips quotes from header", captured.headers, (h) => h?.["x-agent-token"] === TOK);
    },
    { action: "x", reason_code: "y", action_required: false },
  );
  await fs.rm(wsRoot, { recursive: true, force: true });
}

// --- 12. (codex #5) duplicate AUTH_TOKEN= lines pick the LAST value
//
// Matches MC backend behavior (which treats TOOLS.md as an env-style
// file where later assignments win). Avoids silent 401s when an old
// token line lingers near the top of TOOLS.md.
{
  const agentId = "lead-c2c2c2c2-1111-2222-3333-444444444444";
  const wsRoot = await fs.mkdtemp(path.join(os.tmpdir(), "mc-bootstrap-test-"));
  const wsDir = path.join(wsRoot, `workspace-${agentId}`);
  await fs.mkdir(wsDir, { recursive: true });
  await fs.writeFile(
    path.join(wsDir, "TOOLS.md"),
    `# TOOLS.md\n\n- \`AUTH_TOKEN=stale-old-token\`\n- \`AUTH_TOKEN=${TOK}\`\n- \`AGENT_ID=11111111-2222-3333-4444-555555555555\`\n`,
  );
  await withStubServer(
    async (baseUrl, captured) => {
      const ev = makeBootstrapEvent({
        agentId,
        env: { MC_BASE_URL: baseUrl, BOARD_ID: "b", WORKSPACE_ROOT: wsRoot, TIMEOUT_MS: "1500" },
      });
      await handler(ev);
      expect("duplicate AUTH_TOKEN selects the LAST value", captured.headers, (h) => h?.["x-agent-token"] === TOK);
    },
    { action: "x", reason_code: "y", action_required: false },
  );
  await fs.rm(wsRoot, { recursive: true, force: true });
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
