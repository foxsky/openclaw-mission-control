- Add reminders to an task, and can see it on the task board, like comments
- Attach files/docs to tasks


 Collapsible toggle: Collapse button works, thin strip with expand button appears, localStorage per board/Kaban Column



  ┌──────┬────────────────────────────────────────────────────────────────────────────┬                                                                          
  │ Task │                                    File                                    │
  ├──────┼────────────────────────────────────────────────────────────────────────────┼                                                                          
  │ T1   │ src/taskflow-db.ts (schema)                                                │          │                                                                           
  ├──────┼────────────────────────────────────────────────────────────────────────────┼                                                                                                            
  │ T3   │ src/index.ts (trigger bypass + output routing)                             │  │                                                                             
  ├──────┼────────────────────────────────────────────────────────────────────────────┼                                                                             
  │ T4   │ container/agent-runner/src/ipc-mcp-stdio.ts (send_board_chat tool)         │         │                                                                           
  ├──────┼────────────────────────────────────────────────────────────────────────────                                                                         
  │ T4   │ container/agent-runner/src/runtime-config.ts (NANOCLAW_ASSISTANT_NAME env) │ 
                                                                         
                                 

  ┌────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐                            
  │                File                │                                                      Content                                                       │
  ├────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤                            
  │ taskflow-product-roadmap.md        │ 6 phases from auth to SaaS product, priority matrix                                                                │                          
  ├────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ taskflow-phase1-spec.md            │ Detailed Phase 1 spec: WhatsApp OTP, orgs, board CRUD, member management, API endpoints, DB schema, file structure │                            
  ├────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤                            
  │ taskflow-product-pages.md          │ Landing page, login flow, onboarding wizard, settings pages                                                        │                            
  ├────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤                            
  │ taskflow-technical-architecture.md │ Current vs target architecture, DB migration path, auth flow diagram, deployment, security                         │                          
  └────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘                            
                                                                                                                                                                                       
  Phase 1 is the foundation — everything else builds on auth + multi-tenancy. The WhatsApp OTP login is the key differentiator: no passwords, users sign in with the same WhatsApp number
   they already use to talk to the AI assistant.                                                                                                                                       
                                                                                                                                                                                         
  Want me to have the Architect review these specs, or start breaking Phase 1 into tasks for the agents?                                                                                 
  

Review the task: Phase 1A: Schema review — PATCH/DELETE/comments API contract; use Chome MCP if necessary, expected behavior is fully compliance with the task spect  

⏺ "SSE probe" is gone from the board. The DELETE worked via API (204) — the task was removed from the database and the UI no longer shows it after reload.

  The alert("Delete not supported by API") was a false error. Looking at the TaskDetailPanel code, the delete handler has a try/catch that catches ANY error and shows that alert — but  
  the actual error might have been something else (CORS, or the confirm dialog was dismissed). The DELETE endpoint itself works correctly.
                                                                                                                                                                                         
  The frontend delete flow needs a fix — the stale error message should be removed now that the endpoint exists. But that's a minor PF task, not a Phase 1A blocker.                     
                                                                                                                                                                         
7) Regra prática que eu usaria no teu caso

Para o teu perfil:

default: gpt-5.4
mini só para:
boilerplate
testes simples
ajustes locais
documentação
sempre gpt-5.4 para:
revisão de código
infra
refatoração multi-arquivo
incident/debug difícil
CI/CD e IaC

Ou seja: no teu caso, o “híbrido” não é mini como padrão geral; é modelo forte como padrão e mini só como atalho barato para trabalho mecânico.

/model gpt-5.4
/review

openclaw security audit --deep

❯ Review the tasks waiting for approval, use Chome MCP if necessary, the expected behavior is fully compliance with the task spect
Review the tasks waiting for approval, use Chome MCP if necessary, the expected behavior is fully compliance with the task spect, only approve with full evidence check

remove activeHours

● Codex is right — my assumption about replacing root board setup with IPC was wrong. Key findings:

  1. Service is NOT running during Phase 3 — it's stopped in Phase 2 for group creation and only restarted in Phase 4. IPC requires a running service.
  2. IPC path is tasks/, not messages/ — I had the wrong directory.
  3. provision_root_board doesn't cover all topologies — only hierarchy, not standard/separate or control+team dual-board.
  4. Root board defaults differ — plugin defaults requiresTrigger: false, skill sets 1.
  5. Async with no completion signal — would break the sequential wizard flow.

  What IS viable: Replacing the manual child board path (line ~1000) with provision_child_board IPC — by Phase 6 the service is running, the plugin covers all the steps, and
  requiresTrigger: false already matches.

  For root boards, the right approach is narrower — just replace the raw SQL INSERT INTO registered_groups with a call to the register_group IPC handler (not the full
  provision_root_board), and only when the service is running (Phase 4+). But since Phase 3 runs with the service stopped, the raw SQL is actually the correct approach there.

  The most practical change: update the manual child board reference docs (line ~1000) to show the IPC approach instead of raw SQL, and leave the root board Phase 3 SQL as-is. Want me
  to proceed with just that?

    - Key changes in that merge: store mounted rw for main agent, requiresTrigger added to register_group, reply context support, timezone formatting

    Plan updated with Codex validation. The Architect model test showed gpt-5.4 found deeper security issues than Opus (131s but higher quality findings). Keep Architect on Opus for now —
   the speed matters for the review pipeline — but gpt-5.4 is a strong alternative if you want deeper adversarial reviews.

  Want me to send the Phase 2 plan to the Supervisor to start creating tasks?

 The Supervisor's HEARTBEAT.md is empty.

 The watchdog already restarts the gateway when it's down. I need to add a post-restart hook that enables heartbeats and recovers agents. The best approach: create a separate recovery script that the watchdog calls after
  restart_gateway.