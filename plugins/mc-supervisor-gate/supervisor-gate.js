const DEFAULT_SUPERVISOR_AGENT_IDS = ["lead-05002170-201b-4c66-bae1-26c0c833f206"];

const DEFAULT_ACTION_REQUIRED_PATTERNS = [
  String.raw`"action_required"\s*:\s*true`,
  String.raw`\baction_required\s*[:=]\s*true\b`,
  String.raw`\bLEAD_NEXT_ACTION_REQUIRED\b`,
];

const DEFAULT_MUTATION_PATTERNS = [
  String.raw`\bcurl\b[\s\S]*(?:^|\s)-X\s*(POST|PATCH|PUT|DELETE)\b`,
  String.raw`\b(method|request|httpMethod)\s*[:=]\s*["']?(POST|PATCH|PUT|DELETE)\b`,
  String.raw`\b(create-task|assign-task|add-comment|approve-task|block-task|close-task|return-to-todo|submit-to-review|promote-to-review|record-review|record-signoff|record-artifact|update-heartbeat|ack-mention)\b`,
  String.raw`\b(api/mission-control|api/tasks|api/approvals|api/reviews|api/lead)\b[\s\S]*\b(POST|PATCH|PUT|DELETE)\b`,
];

const DEFAULT_OK_REPLY_PATTERNS = [
  String.raw`^\s*HEARTBEAT_OK\s*$`,
  String.raw`^\s*NO_REPLY\s*$`,
  String.raw`^\s*Heartbeat OK[.!]?\s*$`,
  String.raw`^\s*Heartbeat OK,\s*no action required\.?\s*$`,
];

function compilePatterns(configPatterns, defaults) {
  const source = Array.isArray(configPatterns) && configPatterns.length > 0 ? configPatterns : defaults;
  return source.map((pattern) => new RegExp(pattern, "i"));
}

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function compactString(value) {
  return value.replace(/\s+/g, " ").trim();
}

export function extractText(value, depth = 0) {
  if (value == null || depth > 6) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map((item) => extractText(item, depth + 1)).filter(Boolean).join("\n");
  if (typeof value !== "object") return "";

  const parts = [];
  const record = value;
  for (const key of ["text", "content", "message", "result", "output", "stdout", "stderr", "body", "json"]) {
    if (key in record) parts.push(extractText(record[key], depth + 1));
  }
  if (parts.some(Boolean)) return parts.filter(Boolean).join("\n");

  try {
    return JSON.stringify(record);
  } catch {
    return String(record);
  }
}

export function hasActionRequiredSignal(value, patterns = compilePatterns(undefined, DEFAULT_ACTION_REQUIRED_PATTERNS)) {
  const text = extractText(value);
  if (!text) return false;
  return patterns.some((pattern) => pattern.test(text));
}

export function hasMutationIntent(toolName, params, patterns = compilePatterns(undefined, DEFAULT_MUTATION_PATTERNS)) {
  const text = `${toolName ?? ""}\n${extractText(params)}`;
  if (!text) return false;
  return patterns.some((pattern) => pattern.test(text));
}

export function isOkShortcut(value, patterns = compilePatterns(undefined, DEFAULT_OK_REPLY_PATTERNS)) {
  const text = compactString(extractText(value));
  if (!text) return false;
  if (text.length > 120) return false;
  return patterns.some((pattern) => pattern.test(text));
}

function makeStateKey(event, ctx) {
  return event?.runId ?? ctx?.runId ?? ctx?.sessionKey ?? ctx?.sessionId ?? "unknown";
}

function makeSessionKey(ctx) {
  return ctx?.sessionKey ?? ctx?.sessionId ?? ctx?.agentId ?? "unknown";
}

function readSupervisorAgentIds(config) {
  const configured = Array.isArray(config?.supervisorAgentIds) ? config.supervisorAgentIds.filter(Boolean) : [];
  return new Set(configured.length > 0 ? configured : DEFAULT_SUPERVISOR_AGENT_IDS);
}

export function createSupervisorGate(configInput = {}, logger = undefined) {
  const config = asRecord(configInput);
  const supervisorAgentIds = readSupervisorAgentIds(config);
  const maxRevisionsPerRun = Number.isInteger(config.maxRevisionsPerRun) ? Math.max(0, config.maxRevisionsPerRun) : 1;
  const actionRequiredPatterns = compilePatterns(config.actionRequiredPatterns, DEFAULT_ACTION_REQUIRED_PATTERNS);
  const mutationPatterns = compilePatterns(config.mutationPatterns, DEFAULT_MUTATION_PATTERNS);
  const okReplyPatterns = compilePatterns(config.okReplyPatterns, DEFAULT_OK_REPLY_PATTERNS);
  const states = new Map();
  const toolCallToStateKey = new Map();

  function isSupervisor(ctx) {
    return supervisorAgentIds.has(ctx?.agentId);
  }

  function getState(event, ctx) {
    const stateKey = makeStateKey(event, ctx);
    let state = states.get(stateKey);
    if (!state) {
      state = {
        stateKey,
        sessionKey: makeSessionKey(ctx),
        actionRequired: false,
        mutationObserved: false,
        revisions: 0,
      };
      states.set(stateKey, state);
    }
    return state;
  }

  function beforeToolCall(event, ctx) {
    if (!isSupervisor(ctx)) return undefined;
    const state = getState(event, ctx);
    if (event?.toolCallId) toolCallToStateKey.set(event.toolCallId, state.stateKey);
    if (hasMutationIntent(event?.toolName, event?.params, mutationPatterns)) {
      state.mutationObserved = true;
    }
    return undefined;
  }

  function stateForToolResult(event, ctx) {
    const stateKey = event?.runId ?? (event?.toolCallId ? toolCallToStateKey.get(event.toolCallId) : undefined) ?? ctx?.runId;
    if (stateKey && states.has(stateKey)) return states.get(stateKey);
    return getState(event, ctx);
  }

  function toolResultPersist(event, ctx) {
    if (!isSupervisor(ctx)) return undefined;
    const state = stateForToolResult(event, ctx);
    if (hasActionRequiredSignal(event?.message, actionRequiredPatterns)) {
      state.actionRequired = true;
    }
    return undefined;
  }

  function afterToolCall(event, ctx) {
    if (!isSupervisor(ctx)) return undefined;
    const state = stateForToolResult(event, ctx);
    if (hasMutationIntent(event?.toolName, event?.params, mutationPatterns)) {
      state.mutationObserved = true;
    }
    if (hasActionRequiredSignal(event?.result, actionRequiredPatterns)) {
      state.actionRequired = true;
    }
    return undefined;
  }

  function beforeAgentFinalize(event, ctx) {
    if (!isSupervisor(ctx)) return undefined;
    const stateKey = makeStateKey(event, ctx);
    const state = states.get(stateKey) ?? states.get(ctx?.runId) ?? states.get(ctx?.sessionKey);
    if (!state) return undefined;

    const finalMessage = event?.lastAssistantMessage ?? event?.message ?? event?.content ?? event;
    if (!state.actionRequired || state.mutationObserved || !isOkShortcut(finalMessage, okReplyPatterns)) {
      states.delete(state.stateKey);
      return undefined;
    }

    if (state.revisions >= maxRevisionsPerRun) {
      logger?.warn?.(
        `[mc-supervisor-gate] allowing Supervisor final reply after revision budget exhausted: run=${state.stateKey}`,
      );
      states.delete(state.stateKey);
      return undefined;
    }

    state.revisions += 1;
    logger?.warn?.(
      `[mc-supervisor-gate] rejected Supervisor OK shortcut after action_required without mutation: run=${state.stateKey}`,
    );
    return {
      action: "revise",
      reason:
        "Mission Control reported action_required=true during this heartbeat, but no mutating Mission Control action was observed. Complete the required POST/PATCH/PUT/DELETE or hqctl action before finalizing HEARTBEAT_OK.",
    };
  }

  return {
    beforeToolCall,
    toolResultPersist,
    afterToolCall,
    beforeAgentFinalize,
    _states: states,
    _toolCallToStateKey: toolCallToStateKey,
  };
}
