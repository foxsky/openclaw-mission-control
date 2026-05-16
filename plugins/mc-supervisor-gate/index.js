import { createSupervisorGate } from "./supervisor-gate.js";

const plugin = {
  id: "mc-supervisor-gate",
  name: "Mission Control Supervisor Gate",
  description:
    "Prevents Supervisor heartbeat OK shortcuts when Mission Control reported required action but no mutating action happened in the turn.",
  register(api) {
    const gate = createSupervisorGate(api.pluginConfig, api.logger);

    api.on("before_tool_call", (event, ctx) => gate.beforeToolCall(event, ctx), {
      name: "mc-supervisor-gate-before-tool-call",
      description: "Track Supervisor Mission Control tool-call intent.",
    });
    api.on("tool_result_persist", (event, ctx) => gate.toolResultPersist(event, ctx), {
      name: "mc-supervisor-gate-tool-result-persist",
      description: "Track Mission Control action-required signals in tool results.",
    });
    api.on("after_tool_call", (event, ctx) => gate.afterToolCall(event, ctx), {
      name: "mc-supervisor-gate-after-tool-call",
      description: "Track Mission Control tool-call results.",
    });
    api.on("before_agent_finalize", (event, ctx) => gate.beforeAgentFinalize(event, ctx), {
      name: "mc-supervisor-gate-before-agent-finalize",
      description: "Reject heartbeat OK shortcuts when required MC action was skipped.",
    });

    api.logger?.info?.("[mc-supervisor-gate] loaded");
  },
};

export default plugin;
