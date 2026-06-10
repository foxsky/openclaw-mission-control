# MC-authored OpenClaw gateway plugins

This directory holds the source of truth for plugins MC contributes to the
OpenClaw gateway runtime on `.60`. Until 2026-05-16 these lived only at
`/root/.openclaw/plugins/` on `.60` with no git remote — losing the host
would lose the plugins. Brought into this repo as backup + version
control + visible diffs.

## Plugins

| Directory                | Live path on `.60`                                  | What it does |
|--------------------------|-----------------------------------------------------|--------------|
| `mc-supervisor-gate/`    | `/root/.openclaw/plugins/mc-supervisor-gate/`       | Hooks the Supervisor's tool-call lifecycle to reject heartbeat `OK` replies when MC reported `action_required` but no mutating tool ran that turn. The "Fix 2" referenced in `project_supervisor_action_enforcement_pending.md`. |
| `whatsapp-scheduler/`    | `/root/.openclaw/plugins/whatsapp-scheduler/`       | Detects scheduling intent in WhatsApp messages, creates Google Calendar events via `gcal`. Imports from `openclaw/plugin-sdk`. TypeScript source under `src/`, builds to `dist/`. |

## Deploy

Plugins on `.60` are deployed manually via rsync. (Skills are no longer in
this bucket: since 2026-06-10 the Deploy workflow ships `backend/skills/**`
to `.60` automatically — see `.github/workflows/deploy.yml`.) Workflow:

```bash
# from this directory on the operator's Mac:
rsync -avz --delete \
  --exclude node_modules --exclude '.env' --exclude contexts.json \
  mc-supervisor-gate/ root@192.168.2.60:/root/.openclaw/plugins/mc-supervisor-gate/

# whatsapp-scheduler — build dist/ locally first, then rsync src + dist:
(cd whatsapp-scheduler && npm install && npm run build)
rsync -avz --delete \
  --exclude node_modules --exclude '.env' --exclude contexts.json \
  whatsapp-scheduler/ root@192.168.2.60:/root/.openclaw/plugins/whatsapp-scheduler/

# After either rsync, prompt the gateway to reload its plugin registry:
ssh root@192.168.2.60 'openclaw doctor --fix'   # rebuilds plugin index
# Or restart for a full reload:
ssh root@192.168.2.60 'systemctl reload-or-restart openclaw'   # if applicable
```

## SDK version

Both plugins target the OpenClaw gateway SDK shipped with `2026.5.12`
(`@openclaw/plugin-sdk` root barrel via `openclaw/plugin-sdk`).
Deprecation warnings about `openclaw/plugin-sdk` subpaths landed in 5.12
(see CHANGELOG entries about deprecating low-use subpaths); both plugins
currently use the still-supported root barrel for `emptyPluginConfigSchema`.
Migration to a focused subpath is a future cleanup, not blocking.

## Tests

- `mc-supervisor-gate`: `node --test supervisor-gate.test.js` (pure JS,
  no build step).
- `whatsapp-scheduler`: `npm test` from `whatsapp-scheduler/` (Jest + ts-jest).

Neither is currently wired into the MC CI pipeline. Adding them would
require installing the gateway SDK type-defs as a dev-dep in the MC repo,
which is out of scope for the backup-and-version-control move.

## Future work

- Wire plugin tests into `ci.yml` (after deciding whether to vendor the
  gateway SDK type-defs or stub them).
- Add a small `make deploy-plugins` target that wraps the rsync + reload
  flow above.
- Consider whether the plugins should publish to ClawHub/npm instead of
  staying as local-only extensions.
