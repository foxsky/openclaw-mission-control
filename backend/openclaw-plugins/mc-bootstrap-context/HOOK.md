---
name: mc-bootstrap-context
description: "Inject live Mission Control runtime brief at agent:bootstrap so MC-managed agents start each session with current pipeline / next-action / assigned-task state instead of stale rendered templates."
homepage: https://github.com/Foxsky/openclaw-mission-control
metadata:
  {
    "openclaw":
      {
        "emoji": "📊",
        "events": ["agent:bootstrap"],
        "requires": { "bins": ["node"] }
      }
  }
---

# MC Bootstrap Context Hook

Calls Mission Control's API at agent bootstrap and injects a synthetic
`MC_RUNTIME_BRIEF.md` into the agent's `Project Context` so the freshest
view of board state arrives at session start.

## What gets injected

- **Lead agent** (id starts with `lead-`): the latest `/lead/next-action`
  response — current action, reason_code, target task, blocker counts.
- **Worker agent** (id starts with `mc-` and not `mc-gateway-`): the
  agent's assigned-task list with status/priority/blockers.
- **Other agents** (`main`, `repro-*`, `eval-*`, `mc-gateway-*`):
  hook returns silently — no MC context to inject.

## Why

MC's existing flow renders `BOARD_HEARTBEAT.md.j2` server-side and
syncs the rendered file into agent workspaces. That works but is
strictly poll-cadence stale: the agent sees whatever the last
heartbeat-render produced, not the live state at bootstrap.

The hook fires AT bootstrap, so the agent reads board state captured
within ~1-2 seconds of session start. Especially valuable for the
lead's next-action selection: the agent doesn't act on a stale frontier.

## Configuration

```jsonc
{
  "hooks": {
    "internal": {
      "enabled": true,
      "entries": {
        "mc-bootstrap-context": {
          "enabled": true,
          "env": {
            "MC_BASE_URL": "http://192.168.2.64:8000",
            "BOARD_ID": "05002170-201b-4c66-bae1-26c0c833f206",
            "MC_OPERATOR_TOKEN": "${MC_TOKEN}",
            "TIMEOUT_MS": "2000"
          }
        }
      }
    }
  }
}
```

| Key | Required | Notes |
|---|---|---|
| `MC_BASE_URL` | yes | MC backend HTTP base — e.g. `http://192.168.2.64:8000` |
| `BOARD_ID` | yes | Board UUID. One gateway typically maps to one board. |
| `MC_OPERATOR_TOKEN` | yes | Operator-level bearer token (not per-agent). Use a `SecretRef` or env-var substitution; do NOT inline plaintext in `openclaw.json`. |
| `TIMEOUT_MS` | no, default 2000 | Hard timeout per HTTP call. Bootstrap is hot path; fail fast. |

## Failure semantics

- Bootstrap NEVER fails because of this hook.
- HTTP timeout / network error / 5xx → log at `warn`, return without
  injection. Agent gets only the template-rendered files (existing
  behavior). The brief is best-effort context, not authoritative state.
- Unknown agent type → silent return (no log).

## Install

Copy this directory to `~/.openclaw/hooks/mc-bootstrap-context/` on
the gateway host (typically `.60`), then:

```bash
openclaw hooks enable mc-bootstrap-context
openclaw config set hooks.internal.entries.mc-bootstrap-context.enabled true
openclaw config set hooks.internal.entries.mc-bootstrap-context.env.MC_BASE_URL http://192.168.2.64:8000
openclaw config set hooks.internal.entries.mc-bootstrap-context.env.BOARD_ID <board-uuid>
openclaw config set hooks.internal.entries.mc-bootstrap-context.env.MC_OPERATOR_TOKEN '${MC_OPERATOR_TOKEN}'
# next planned restart picks it up
```

## Source of truth

This hook is maintained alongside MC backend at
`backend/openclaw-plugins/mc-bootstrap-context/` in the
`openclaw-mission-control` repo. Keep render logic in sync with MC
schema changes (`/lead/next-action` response shape,
`/agent/boards/.../tasks` shape).
