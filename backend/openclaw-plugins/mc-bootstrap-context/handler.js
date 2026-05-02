// mc-bootstrap-context — OpenClaw internal hook for agent:bootstrap events.
//
// Injects a synthetic MC_RUNTIME_BRIEF.md into context.bootstrapFiles so
// MC-managed agents start each session with live board state. See HOOK.md
// for design notes.
//
// Token model: each MC agent has its own AUTH_TOKEN written into its
// workspace TOOLS.md by the agent-provisioning flow. The hook reads the
// per-agent token at bootstrap and uses X-Agent-Token to call MC's
// /api/v1/agent/* routes (which do NOT accept the operator Bearer).
// This keeps agent_id attribution correct on any side-effects MC infers
// from the read (e.g., "agent X looked at next-action").

import { readFile } from "node:fs/promises";

const HOOK_KEY = "mc-bootstrap-context";
const DEFAULT_TIMEOUT_MS = 2000;
const DEFAULT_WORKSPACE_ROOT = "/root/.openclaw/workspace";
const SYNTHETIC_NAME = "MC_RUNTIME_BRIEF.md";

const handler = async (event) => {
  if (event?.type !== "agent" || event?.action !== "bootstrap") return;
  const ctx = event.context;
  if (!ctx || !Array.isArray(ctx.bootstrapFiles)) return;

  const cfg = readHookEnv(ctx.cfg);
  if (!cfg) return;

  const agentId = inferMcAgentId(ctx.sessionKey, ctx.workspaceDir);
  if (!agentId) return;

  const role = inferRole(agentId);
  if (role === "skip") return;

  const creds = await readPerAgentCreds(agentId, cfg.workspaceRoot);
  if (!creds?.token) return;
  // Workers need the MC `agents.id` UUID (not the OpenClaw `mc-<uuid>`
  // prefixed id) for the assigned_agent_id query param. Lead's
  // /next-action endpoint doesn't use a query param so mcAgentId is
  // optional for that path.
  if (role === "worker" && !creds.mcAgentId) return;

  let content;
  const startedAt = Date.now();
  try {
    if (role === "lead") {
      const data = await fetchJSON(
        `${cfg.baseUrl}/api/v1/agent/boards/${cfg.boardId}/lead/next-action`,
        creds.token,
        cfg.timeoutMs,
      );
      content = renderLeadBrief(data);
    } else {
      const data = await fetchJSON(
        `${cfg.baseUrl}/api/v1/agent/boards/${cfg.boardId}/tasks?assigned_agent_id=${creds.mcAgentId}&limit=50`,
        creds.token,
        cfg.timeoutMs,
      );
      content = renderWorkerQueue(agentId, data);
    }
  } catch (err) {
    log("warn", `MC fetch failed for ${agentId} (${role}): ${stringifyError(err)}`);
    return;
  }

  ctx.bootstrapFiles.push({
    name: SYNTHETIC_NAME,
    path: `<mc-bootstrap-context:${agentId}>`,
    content,
    missing: false,
  });
  log("info", `injected ${SYNTHETIC_NAME} for ${agentId} (${role}, ${Date.now() - startedAt}ms)`);
};

// Read hook config from gateway snapshot. MC_OPERATOR_TOKEN intentionally
// not required; per-agent tokens come from TOOLS.md, not the env block.
function readHookEnv(gatewayCfg) {
  const entry = gatewayCfg?.hooks?.internal?.entries?.[HOOK_KEY];
  const env = entry?.env;
  if (!env) return null;
  const baseUrl = trimTrailingSlash(env.MC_BASE_URL);
  const boardId = env.BOARD_ID;
  if (!baseUrl || !boardId) return null;
  const workspaceRoot = typeof env.WORKSPACE_ROOT === "string" && env.WORKSPACE_ROOT
    ? env.WORKSPACE_ROOT
    : DEFAULT_WORKSPACE_ROOT;
  const timeoutRaw = Number.parseInt(env.TIMEOUT_MS, 10);
  const timeoutMs = Number.isFinite(timeoutRaw) && timeoutRaw > 0 ? timeoutRaw : DEFAULT_TIMEOUT_MS;
  return { baseUrl, boardId, workspaceRoot, timeoutMs };
}

// Read AUTH_TOKEN and AGENT_ID from {workspaceRoot}/workspace-{agentId}/TOOLS.md.
// Returns { token, mcAgentId } where either may be null. Never throws.
//
// AGENT_ID is the MC `agents.id` UUID (no OpenClaw prefix), needed for
// the worker query path; lead doesn't need it. Returns null only when
// the file itself is unreadable.
async function readPerAgentCreds(agentId, workspaceRoot) {
  const toolsPath = `${workspaceRoot}/workspace-${agentId}/TOOLS.md`;
  let body;
  try {
    body = await readFile(toolsPath, "utf8");
  } catch {
    return null;
  }
  return {
    token: _extractEnvLikeValue(body, "AUTH_TOKEN"),
    mcAgentId: _extractEnvLikeValue(body, "AGENT_ID"),
  };
}

// Extract the LAST value of ``<KEY>=...`` in TOOLS.md-style content.
// MC's TOOLS.md is shell-env-shaped lines wrapped in markdown list/
// backtick decoration. Both ``- `KEY=value` `` and bare ``KEY=value``
// are supported. Surrounding double or single quotes on the value are
// stripped. Picking the LAST occurrence matches the env-style "later
// assignment wins" contract MC's own parser uses, so a stale token
// line lingering near the top of the file doesn't shadow the fresh one.
function _extractEnvLikeValue(body, key) {
  const re = new RegExp(`^[\\s-]*\`?${key}=([^\\s\`\\n]+)\`?`, "gm");
  let last = null;
  for (const m of body.matchAll(re)) {
    last = m[1];
  }
  if (last == null) return null;
  // Strip a single matching pair of surrounding quotes, if present.
  if ((last.startsWith('"') && last.endsWith('"') && last.length >= 2) ||
      (last.startsWith("'") && last.endsWith("'") && last.length >= 2)) {
    return last.slice(1, -1);
  }
  return last;
}

function inferMcAgentId(sessionKey, workspaceDir) {
  if (typeof sessionKey === "string") {
    const m = sessionKey.match(/^agent:([^:]+):/);
    if (m && isMcAgentId(m[1])) return m[1];
  }
  if (typeof workspaceDir === "string") {
    const segs = workspaceDir.split("/").filter(Boolean);
    for (let i = segs.length - 1; i >= 0; i--) {
      const candidate = segs[i].replace(/^workspace-/, "");
      if (isMcAgentId(candidate)) return candidate;
    }
  }
  return null;
}

// MC openclaw agent IDs are exactly ``<prefix>-<uuid>``. Anything with
// path separators, control chars, percent encoding, or whitespace is
// rejected — this blocks ``mc-x/../workspace-mc-victim``-style path
// traversal where ``readPerAgentCreds`` would otherwise read a
// neighboring agent's TOOLS.md.
const _MC_AGENT_ID_PATTERN = /^(lead|mc)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isMcAgentId(id) {
  if (!id || typeof id !== "string") return false;
  if (id.startsWith("mc-gateway-")) return false;
  return _MC_AGENT_ID_PATTERN.test(id);
}

function inferRole(agentId) {
  if (agentId.startsWith("lead-")) return "lead";
  if (agentId.startsWith("mc-")) return "worker";
  return "skip";
}

function trimTrailingSlash(s) {
  if (typeof s !== "string") return null;
  return s.replace(/\/+$/, "");
}

async function fetchJSON(url, token, timeoutMs) {
  const { default: http } = await import("node:http");
  const { default: https } = await import("node:https");
  return await new Promise((resolve, reject) => {
    const u = new URL(url);
    const lib = u.protocol === "https:" ? https : http;
    const req = lib.request(
      {
        method: "GET",
        hostname: u.hostname,
        port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: `${u.pathname}${u.search}`,
        headers: {
          "X-Agent-Token": token,
          "Accept": "application/json",
        },
        timeout: timeoutMs,
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const body = Buffer.concat(chunks).toString("utf8");
          if (!res.statusCode || res.statusCode >= 400) {
            reject(new Error(`HTTP ${res.statusCode}: ${body.slice(0, 200)}`));
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (err) {
            reject(new Error(`JSON decode failed: ${stringifyError(err)}`));
          }
        });
      },
    );
    req.on("timeout", () => {
      req.destroy(new Error(`timeout after ${timeoutMs}ms`));
    });
    req.on("error", reject);
    req.end();
  });
}

function renderLeadBrief(nextAction) {
  if (!nextAction || typeof nextAction !== "object") {
    return "# MC Runtime Brief — Lead\n\n_No data from MC._\n";
  }
  const lines = ["# MC Runtime Brief — Lead", ""];
  lines.push(`**Action**: \`${nextAction.action ?? "?"}\``);
  lines.push(`**Reason**: \`${nextAction.reason_code ?? "?"}\``);
  lines.push(`**Required**: ${nextAction.action_required === true ? "yes" : "no"}`);
  if (nextAction.task_id) {
    lines.push("");
    lines.push("## Target task");
    lines.push(`- id: \`${nextAction.task_id}\``);
    if (nextAction.task_title) lines.push(`- title: ${nextAction.task_title}`);
    if (nextAction.task_status) lines.push(`- status: \`${nextAction.task_status}\``);
    if (nextAction.assigned_agent_id) lines.push(`- assigned_agent_id: \`${nextAction.assigned_agent_id}\``);
  }
  const details = nextAction.details && typeof nextAction.details === "object" ? nextAction.details : null;
  if (details && Object.keys(details).length > 0) {
    lines.push("");
    lines.push("## Details");
    lines.push("```json");
    lines.push(JSON.stringify(details, null, 2));
    lines.push("```");
  }
  lines.push("");
  lines.push("_Captured at agent bootstrap. Stale within seconds — re-fetch via API for fresh state._");
  return lines.join("\n");
}

function renderWorkerQueue(agentId, page) {
  const items = Array.isArray(page) ? page : Array.isArray(page?.items) ? page.items : [];
  const lines = [`# MC Runtime Brief — Worker (${agentId})`, ""];
  if (items.length === 0) {
    lines.push("_No tasks currently assigned._");
    lines.push("");
    lines.push("_Captured at agent bootstrap._");
    return lines.join("\n");
  }
  lines.push(`**${items.length}** task(s) assigned (top 50):`);
  lines.push("");
  lines.push("| status | priority | id | title |");
  lines.push("|---|---|---|---|");
  for (const t of items.slice(0, 50)) {
    const id = (t?.id ?? "?").slice(0, 8);
    const title = (t?.title ?? "").replace(/\|/g, "\\|").slice(0, 80);
    lines.push(`| ${t?.status ?? "?"} | ${t?.priority ?? "?"} | \`${id}\` | ${title} |`);
  }
  lines.push("");
  lines.push("_Captured at agent bootstrap. For live state, use mc_client.py or the typed API directly._");
  return lines.join("\n");
}

function stringifyError(err) {
  if (!err) return "unknown";
  if (err instanceof Error) return err.message || err.name || "Error";
  try {
    return JSON.stringify(err).slice(0, 200);
  } catch {
    return String(err);
  }
}

function log(level, msg) {
  const tag = `[${HOOK_KEY}]`;
  if (level === "warn" && typeof console !== "undefined" && console.warn) {
    console.warn(`${tag} ${msg}`);
  } else if (typeof console !== "undefined" && console.log) {
    console.log(`${tag} ${msg}`);
  }
}

export default handler;
