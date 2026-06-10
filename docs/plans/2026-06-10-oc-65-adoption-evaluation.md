# OpenClaw 2026.5.28 → 2026.6.5 Adoption Evaluation (MC fleet, gateway already on 2026.6.5)

Duplicate scored candidates are merged: the three MiniMax items, the two Workboard items, the two Skill Workshop items, the two reply-payload-hook items, the two dispatch-UUID items, the two ClawHub-plugin-publish items, and the three doctor items each appear once below. Where scored verdicts conflicted, the resolution and reason are stated.

---

## 1. Adopt now (ranked by ROI)

### 1. Prune the dead MiniMax fallback entries (P2) — effort S
*Merges: `ollama-toolcall-promotion-fallback-cleanup`, `minimax-m3-fallback-refresh`, `minimax-m3-fallback-repair` — all three scored adopt-now and converge on the same operation.*

- **What:** One-off gateway config change scrubbing every `minimax/*` candidate (minimax-m2.1, minimax-m2.7) from `agents.defaults.models` and the ~6-7 per-agent fallback chains in `agents.list`. 6.1's MiniMax M3 + account OAuth (#88860, #88512) is the decision trigger, not the mechanism — the prune needs nothing from the releases. The 5.28 Ollama plain-text tool-call promotion is free tailwind already active.
- **Pain solved:** P2 — startup `MINIMAX_API_KEY` warning gone, dead chain hops gone, fallback chains become honest (real redundancy = ollama `*:cloud` only).
- **First step:** Ask the operator one question: "Do we have a live MiniMax account or valid MINIMAX_API_KEY?" Default answer (no): `GET /api/v1/gateways/<id>/config` from .64 with `LOCAL_AUTH_TOKEN` (not the lead bearer — it suppresses wakes), extract refs with `jq '[.. | strings | select(startswith("minimax/"))]'`, then issue one `config.patch` with baseHash (same CAS shape as `provisioning.py:920-970`) scrubbing chain refs. Drop the `models.providers.minimax` block only after refs are zero, and only with an explicit operator prune-vs-revive decision (6.1 made M3 revival a live option). If the operator *does* have credentials: set the key in the gateway EnvironmentFile, repoint chains to `minimax/minimax-m3`, and run one in-gateway heartbeat-sized smoke before trusting it — the historical 100% in-gateway timeout was never root-caused; 5.28 only bounds it.
- **Constraints:** Schedule in a quiet window — a real config.patch SIGUSR1-restarts the gateway (P4 token rotation + P10 set-heartbeats reset; reconcile self-heals in ~4-5 min, verified 2026-06-09). Patch model arrays only — never delete/recreate agent entries. Validate via gateway logs, not Prometheus (P12: per-candidate failures don't fire `model_failover_total`).

### 2. Retire `deploy-skills.sh` — fold .60 skill deploys into CI (P1) — effort S/M
*This is the convergent redirect from three rejected candidates (`clawhub-github-skill-install`, `skill-workshop-governed-skill-deploys`, `clawhub-skill-plugin-publish`), not a registry adoption. The changelog contribution is 6.5 #90478: `openclaw skills install` on .60 is live-verified to support git and local-directory installs with pinned commits.*

- **What:** Replace the lone manual rsync path with a CI step. Two viable shapes: (a) preferred — a `deploy.yml` step on the self-hosted runner (mc-prod-64) that runs git-pinned `openclaw skills install` against the foxsky repo at the pushed SHA, if monorepo-subpath installs work; (b) fallback — the same runner runs the existing rsync from `backend/skills/deploy-skills.sh` to `root@192.168.2.60:/root/.openclaw/skills/`, gated on `backend/skills/**` path changes. Either retires the manual script; CI shipping the exact pushed commit gives de facto pinning.
- **Pain solved:** P1 — the last non-CI/CD deploy path.
- **First step:** Get operator sign-off on reversing the deliberate .60 CI exclusion (`plugins/README.md:18`; the open question is SSH-key custody for the runner). If cleared: run `openclaw skills install --help` on .60 to check git-subpath support, then edit `.github/workflows/deploy.yml` to replace the line-~150 ".60 deploy is out of CI/CD scope" echo with the chosen step, and authorize the runner's SSH key on .60.
- **Do NOT** route this through ClawHub registry listings (publishes 17 internal ops skills with internal IPs/token patterns publicly, adds an external registry to an internal deploy loop) or Skill Workshop (agent-authoring tool, wrong direction).

---

## 2. Adopt next

### 1. P9 early-warning auth-health probe — probe-gated doctor poller, with models.authStatus fallback — effort M
*Merges all three doctor candidates. The 5.28 (`doctor-per-agent-auth-health`) and 6.1 (`doctor-json-health-poller`) framings are rejected as standalone adoptions — doctor is host-CLI with no RPC, and "resolve auth secrets during deep audits" is SecretRef resolution, not a live provider probe. The 6.5-scored `doctor-auth-health-poller` adopt-next survives because it gates building on verification.*

- **What:** Scheduled health probe converting Ollama-cloud auth expiry (429 storms, manual restart) into a pre-failure alert.
- **Pain solved:** P9 (zero automation today; the Prometheus poller only catches storms reactively).
- **First step:** One-shot probe — run `openclaw doctor --json` and `openclaw status --deep --json` on .60 AND from .64 via the existing `runtime_status.py` wrapper; grep for per-agent auth-health fields and any ollama entry with expiry/credential state. Decision gate: if the field exists and is reachable from .64, build `backend/app/services/openclaw/doctor_poller.py` as a sibling of `observability_poller.py` (low cadence, 15-30 min — doctor issues live auth probes). If it's .60-only or Ollama is absent from the labels (it's not named in any release line), **stop** and instead extend `observability_poller.py` to call the already-wired `models_auth_status()` RPC helper (`gateway_rpc.py:734`) and alert on expired/error states — failing test first.
- **Free item regardless:** add doctor config preflight to the upgrade runbook (6.5 #90072 ran the cron-store SQLite migration through it).

### 2. Post-sync `tools.effective` smoke for the Supervisor message tool (P11 residual) — effort S
*Derived from the `policy-conformance-checks` reject — not a changelog feature, but the only real P11 gap closer.* Add an assertion in the `heartbeat_sweep.py` reconcile iteration calling the existing `get_tools_effective()` helper (`gateway_rpc.py:879`) for the lead-Supervisor session; alert if `message` is absent from the effective set. Failing test first.

---

## 3. Watch (one line each)

- **Workboard coordination tools / orchestration primitives** (5.28+6.1, plugin present-but-disabled on .60): enumerate tool ids on the .160 dev gateway via `plugins info workboard` + `tools.effective` (never enable on .60 — restart-kind change), record in a memory note; revisit only if the OK-shortcut recurs without legitimate PARKED cover or Workboard gains an external-store/webhook surface MC can project — its task-backed board runs are a growing split-brain risk against MC's Postgres board, and the P6 claim is disproven by the 5-model A/B test.
- **Plugin SDK reply-payload hook** (5.28 #82823/#87165): does NOT fix P11 (whatsapp-scheduler never used the message tool; daily reports are agent crons) — file as hardening of the fragile 4-way runtime send probe (`index.ts:88-102`), to be done when next touching the plugin or when an upgrade breaks delivery; first action then is extracting the hook signature from the installed 2026.6.5 SDK declarations.
- **Skill Workshop** (6.1 #88734): rejected as a P1 deploy fix (agent→gateway authoring, the inverse direction; dual-writer clobber with deploy-skills.sh/git canon), but the governed agent-authored-skill capability is worth one revisit AFTER P1 migrates to git-pinned installs — then consider `alsoAllow: ["skill_workshop"]` for the Supervisor only, review kept on gateway CLI.
- **npm/ClawHub packaging for MC-authored plugins** (5.28/6.1): blocked on private-source install support that doesn't exist; do the cheap `make deploy-plugins` Makefile target promised in `plugins/README.md:63` now, and re-score if plugins gain local-path/git install sources the way skills did in 6.5 — also watch whether the "conflicting plugin install metadata" ledger warning (acpx, codex, diagnostics-prometheus, lossless-claw) recurs on the next upgrade.

---

## 4. Rejected with reason (one line each)

- **codex-supervisor-plugin** — wrong session namespace: its one documented capability lists OpenAI Codex app-server MCP sessions from the *real* CODEX_HOME, which can't see gateway `acp:<uuid>` children (and ACPX uses an isolated home); P8 is already double-covered by non-overlapping layers.
- **status-active-subagent-details** — redundant subset of `tasks.list`, which MC's RPC catalog has exposed since gateway 5.9 with zero callers; building on it would couple MC's task-graph orphan layer to gateway session liveness, the exact overlap the layer-separation memory warns against.
- **policy-conformance-checks** — no RPC/CLI surface MC can reach, and the checks compare gateway state to *gateway* intent, not MC-Postgres intent; the residual P11 need is closed by Adopt-next #2.
- **doctor candidates as standalone adoptions** (5.28 per-agent labels, 6.1 JSON poller) — host-CLI only, no RPC, vantage gap from .64; folded into the probe-gated Adopt-next #1.
- **clawhub-github-skill-install** (ClawHub-registry route) — publishing 17 internal ops skills to a public registry and inserting registry availability into an internal deploy loop; the redirect (CI step) is Adopt-now #2.
- **skill-workshop-governed-skill-deploys** (P1 framing) — operator/CI proposal *submission* surface unconfirmed (only review actions are documented); subsumed by the Skill Workshop watch line.
- **dispatch-event-session-UUIDs** (both scored variants) — the UUID rides interactive dispatch events, not the `sessions.changed` wire MC subscribes to; MC already extracts and persists `sessionId`, and the `acp:<uuid>` drop is deliberate cardinality control; the genuine benefit (phantom `sessions.list` rows hidden) is passive and already in effect.
- **codex-acp-lifecycle-hardening** — all gateway-internal Fixes with no surface; re-baselining `cancel_orphan_child` would measure the wrong layer (task graph vs sessions) and produce false causal conclusions; only action is a dated note in the orphan-layers memory file.
- **plugin-sdk-sqlite-state** — the 6.5 line is an internal migration of the SDK's *own* dedupe bookkeeping, no public plugin-facing state API exists; whatsapp-scheduler's atomic-rename store works, and mc-supervisor-gate's restart-volatile per-run Maps are correct-by-design (persistence would risk stale-runId carryover).
- **clawhub-skill-plugin-publish** (as scored: hard reject) — agreed on substance; merged into the plugin-packaging watch line above since the local/git-install trajectory (proven for skills in 6.5) gives it a concrete revisit trigger.

No scored verdict was overturned outright; the three intra-data conflicts (Skill Workshop reject-vs-watch, ClawHub plugin publish reject-vs-watch, doctor reject-vs-adopt-next) were resolved as noted by keeping the rejection of the claimed pain mapping while preserving the named revisit trigger.

---

## 5. Pain points with NO changelog answer (5.28 / 6.1 / 6.5)

**Addressed:** P1 (6.5 #90478 git-pinned `skills install`), P2 (6.1 MiniMax M3/OAuth as the prune-or-revive decision trigger).

**Partially / conditionally addressed:**
- **P8** — no MC-adoptable surface, but the 6.1 Codex/ACP lifecycle hardening passively shrinks the wedged-child population at the source (already in effect on 6.5).
- **P9** — conditional on the Adopt-next probe: doctor's auth-health labels never name Ollama in any release line, and stale-connection 429s may read as healthy auth.

**Unaddressed — still MC's problem:**
- **P3** (PF ~90k-token heartbeat echo) — nothing; the ranked MC-side mitigation list in memory remains the only path. Note: enabling new agent tools (Workboard, skill_workshop) would make this *worse*.
- **P4** (SIGUSR1 restart per config.patch) — nothing in 5.28-6.5; skip-if-unchanged + touch-reload remains the workaround.
- **P5** (157KB config.get per heartbeat sync) — no path-selector or partial-read appears in any of the three releases' notes.
- **P6** (Supervisor OK-shortcut) — no real answer; Workboard's claimed affordance theory is disproven; the SystemExit(2) gate stays the mitigation. Thin thread: 6.5's heartbeat-metadata-to-context-engine-hooks (unscored) might someday feed mc-supervisor-gate.
- **P7** (eval HTTP API pairing) — unaddressed; direct RPC bypass remains the workaround (the unscored 5.28 device-token gating item was low-promise).
- **P10** (no per-agent gateway-side heartbeat disable) — nothing; the ~5-min reconcile bound remains the mitigation.
- **P11** (message-tool availability) — no release change; MC-side seeding + the Adopt-next smoke is the full answer.
- **P12** (per-candidate fallback failure visibility) — nothing; `model_failover_total` still doesn't fire for Codex-harness 404s, gateway logs remain the only source.
