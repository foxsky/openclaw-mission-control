// mc-bootstrap-context — OpenClaw internal hook for agent:bootstrap events.
//
// Injects a synthetic MC_RUNTIME_BRIEF.md into context.bootstrapFiles so
// MC-managed agents start each session with live board state. See HOOK.md
// for design notes.
//
// Stable contract this handler depends on:
//   - event.type === "agent" && event.action === "bootstrap"
//   - event.context.sessionKey: string (e.g. "agent:lead-…:lead-…")
//   - event.context.workspaceDir: string
//   - event.context.cfg: gateway config snapshot (for hooks.internal.entries.<name>.env)
//   - event.context.bootstrapFiles: mutable array of { name, path, content, missing }

const HOOK_KEY = "mc-bootstrap-context";
const DEFAULT_TIMEOUT_MS = 2000;
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

  let content;
  try {
    if (role === "lead") {
      const data = await fetchJSON(
        `${cfg.baseUrl}/api/v1/agent/boards/${cfg.boardId}/lead/next-action`,
        cfg.token,
        cfg.timeoutMs,
      );
      content = renderLeadBrief(data);
    } else {
      const data = await fetchJSON(
        `${cfg.baseUrl}/api/v1/agent/boards/${cfg.boardId}/tasks?assigned_agent_id=${agentId}&limit=50`,
        cfg.token,
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
};

// Read hook config from gateway snapshot. Returns null if required fields
// missing — handler must no-op rather than throw.
function readHookEnv(gatewayCfg) {
  const entry = gatewayCfg?.hooks?.internal?.entries?.[HOOK_KEY];
  const env = entry?.env;
  if (!env) return null;
  const baseUrl = trimTrailingSlash(env.MC_BASE_URL);
  const boardId = env.BOARD_ID;
  const token = env.MC_OPERATOR_TOKEN;
  if (!baseUrl || !boardId || !token) return null;
  const timeoutRaw = Number.parseInt(env.TIMEOUT_MS, 10);
  const timeoutMs = Number.isFinite(timeoutRaw) && timeoutRaw > 0 ? timeoutRaw : DEFAULT_TIMEOUT_MS;
  return { baseUrl, boardId, token, timeoutMs };
}

// MC agent ids show up in two recognizable shapes inside OpenClaw:
//   - sessionKey: "agent:<agentId>:<sessionLabel>"
//   - workspaceDir: ".../agents/<agentId>" (or workspace-<agentId>)
// Pull the first match; conservative — return null if neither parses.
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

function isMcAgentId(id) {
  if (!id || typeof id !== "string") return false;
  if (id.startsWith("lead-")) return true;
  if (id.startsWith("mc-") && !id.startsWith("mc-gateway-")) return true;
  return false;
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

// Plain HTTP via Node built-ins. No external deps so this handler runs
// as-is without an install step.
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
          "Authorization": `Bearer ${token}`,
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
