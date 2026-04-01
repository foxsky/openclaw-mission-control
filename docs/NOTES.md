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

  │ 18      │ EST-SECTI │ "P15.5 aguardando validação" │ Dropped message — never processed, P15.5 still in next_action                                              │                    
  ├─────────┼───────────┼──────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────┤                    
  │ 19      │ EST-SECTI │ "SECI-P15.5"                 │ Dropped message — follow-up never processed                                                                │
  └─────────┴───────────┴──────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────┘ 