# Agent Activity Panel — Design Document

**Date**: 2026-04-08
**Status**: Design complete, ready for implementation

## Overview

A collapsible right panel on the MC Dashboard that shows real-time agent activity — live thinking, tool calls, command output, and status — similar to a Claude Code terminal per agent.

## Architecture

```
Browser (SSE) ←→ MC Backend (proxy) ←→ OpenClaw Gateway (WebSocket)
```

- MC backend connects to gateway via a single WebSocket
- Subscribes to `sessions.subscribe` for all board agents
- Forwards `session.message` and `session.tool` events as SSE to browser
- Enriches events with agent name, task title from MC database

### Why SSE over WebSocket to browser

SSE is simpler (read-only panel), auto-reconnects, works through proxies. The MC backend holds one gateway WebSocket and fans out to multiple browser tabs.

## Backend

### New endpoint

`GET /api/v1/agents/activity/stream?board_id={id}`

Returns an SSE stream of `AgentEvent` objects.

### Event types

| Gateway event | UI event type | What it shows |
|---|---|---|
| `session.message` role=assistant + text | `thinking` | Blue italic text |
| `session.message` role=assistant + toolCall | `tool_call` | `▶ exec curl ...` |
| `session.tool` with result | `tool_result` | Indented output |
| Heartbeat check-in | `status` | Status dot update |

### AgentActivityBroker (singleton)

Manages gateway WebSocket lifecycle:
- Lazy connect on first SSE client
- Subscription dedup across browser tabs
- Reconnect with exponential backoff (1s → 30s max)
- Backpressure: drop oldest events for slow clients

## Frontend

### Components

**AgentActivityPanel** — outer shell
- 420px wide, collapsible to 0px with smooth transition
- Header: "Agent Activity" + count badge + connection dot
- Tab bar: "All" + per-agent tabs
- State persisted in localStorage

**AgentStream** — per-agent scrollable feed
- Auto-scroll with "scroll lock" on manual scroll-up
- Agent header: name, status, model, task, elapsed
- Footer: turns, tokens, cost

**StreamEvent** — single event line
- Thinking: blue italic, truncated, expandable
- Tool call: `▶ name` with args, collapsible (blue=running, green=ok, red=fail)
- Tool result: indented monospace, 3 lines, expandable
- Status: dimmed heartbeat/transition lines

### React hook: useAgentActivityStream

```typescript
function useAgentActivityStream(boardId: string): AgentActivityState
```

- Connects to SSE endpoint
- Buffers 200 events per agent (drops oldest)
- Auto-reconnects with backoff
- Discards stale events (>5min) on reconnect

### State shape

```typescript
type AgentActivityState = {
  connected: boolean;
  agents: Record<string, {
    name: string;
    status: "online" | "offline" | "error";
    model: string;
    task_id: string | null;
    task_title: string | null;
    events: AgentEvent[];
    turns: number;
    tokens: number;
    cost: number;
    last_event_at: string;
  }>;
};
```

### Event format

```typescript
interface AgentEvent {
  type: "thinking" | "tool_call" | "tool_result" | "status";
  agent_id: string;
  agent_name: string;
  timestamp: string;
  model: string;
  task_id: string | null;
  task_title: string | null;
  content?: string;         // thinking text (max 500 chars)
  tool_name?: string;       // "exec", "sessions_spawn"
  tool_args?: string;       // first 200 chars
  exit_code?: number;       // tool result
  output?: string;          // first 300 chars
  duration_ms?: number;
  status?: string;          // "online", "heartbeat_ok", "idle"
  session_turns?: number;
  session_tokens?: number;
  session_cost?: number;
}
```

## Error handling

- **Gateway disconnect**: yellow "Reconnecting..." banner, exponential backoff
- **Agent offline**: gray dot after 2x heartbeat interval, "Last seen Xm ago"
- **Large output**: truncated to 300 chars, "Show full" fetches via REST
- **Tab backgrounded**: reconnect on visibility, discard stale events
- **Board switch**: disconnect + reconnect with new board_id
- **Cost**: accumulated in memory, resets on backend restart

## Implementation plan

### Backend (PB)
1. `backend/app/api/agent_activity.py` — SSE endpoint
2. `backend/app/services/openclaw/activity_stream.py` — AgentActivityBroker
3. Update `gateway_rpc.py` — add `subscribe_session()` / `unsubscribe_session()` helpers
4. Register router in `main.py`
5. Deploy to .64

### Frontend (PF)
1. `frontend/src/components/agents/AgentActivityPanel.tsx` — panel shell
2. `frontend/src/components/agents/AgentStream.tsx` — per-agent feed
3. `frontend/src/components/agents/StreamEvent.tsx` — event line
4. `frontend/src/hooks/useAgentActivityStream.ts` — SSE hook
5. Integrate into Dashboard layout with collapse toggle
6. Deploy to .63

### Depends on
- OpenClaw 2026.4.8 `sessions.subscribe` + `session.message` events (available)
- MC backend WebSocket to gateway (gateway_rpc.py already has connection code)
- SSE library: `sse-starlette` for backend, native `EventSource` for browser
