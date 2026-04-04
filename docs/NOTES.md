
❯ Review the tasks waiting for approval, use Chome MCP if necessary, the expected behavior is fully compliance with the task spect
Review the tasks waiting for approval, use Chome MCP if necessary, the expected behavior is fully compliance with the task spect, only approve with full evidence check





The agents are "online" (they respond to pings) but not executing their HEARTBEAT.md checklist properly — they're not reading their task comments and acting on them. This could be because their sessions are stale or
  they're                                                                                                                                                                                                                         
    stuck in a loop. Want me to reset their sessions to force a fresh start? Yes  




      HEARTBEAT_OK Response Contract

  ┌──────────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────┐
  │                           Finding                            │                                         Status                                          │
  ├──────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ Agents respond with HEARTBEAT_OK                             │ PARTIAL — Architect/QA-Unit do, Supervisor doesn't always (posts board actions instead) │
  ├──────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ Docs: "reply with HEARTBEAT_OK when nothing needs attention" │ Supervisor correctly omits it when taking action                                        │
  └──────────────────────────────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────┘

  │ mc-3c920c2a (Supervisor MC) │ FAIL   │ Missing IDENTITY.md                          │
  ├─────────────────────────────┼────────┼──────────────────────────────────────────────┤
  │ mc-4209981c (orphan)        │ FAIL   │ Missing IDENTITY.md, HEARTBEAT.md, MEMORY.md │
  ├─────────────────────────────┼────────┼──────────────────────────────────────────────┤
  │ mc-d0f960ed (orphan)        │ FAIL   │ Missing IDENTITY.md, HEARTBEAT.md            │
  ├─────────────────────────────┼────────┼──────────────────────────────────────────────┤
  │ mc-e938ba1e (orphan)        │ FAIL   │ Missing IDENTITY.md, HEARTBEAT.md, MEMORY.md │
  └─────────────────────────────┴────────┴──────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────┬───────────────┬────────────────────────────────────────────────────────────────────────────────┐
  │                       Finding                       │    Status     │                                      Fix                                       │
  ├─────────────────────────────────────────────────────┼───────────────┼────────────────────────────────────────────────────────────────────────────────┤
  │ lightContext not set on any agent                   │ NON-COMPLIANT │ Docs recommend true — reduces context from ~100K to ~2-5K tokens per heartbeat │
  ├─────────────────────────────────────────────────────┼───────────────┼────────────────────────────────────────────────────────────────────────────────┤
  │ agents.defaults.heartbeat.every missing             │ NON-COMPLIANT │ Should be set (docs default: 30m)                                              │
  ├─────────────────────────────────────────────────────┼───────────────┼────────────────────────────────────────────────────────────────────────────────┤
  │ agents.defaults.heartbeat.target missing            │ NON-COMPLIANT │ Should be "last" or "none"                                                     │
  ├─────────────────────────────────────────────────────┼───────────────┼────────────────────────────────────────────────────────────────────────────────┤

  ❯ Read the https://docs.openclaw.ai/concepts/soul and evalute if our agents are in full compliance

  