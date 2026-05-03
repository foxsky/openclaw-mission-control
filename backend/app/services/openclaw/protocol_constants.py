"""Lightweight protocol constants shared by the OpenClaw gateway RPC
client and the long-lived event subscriber.

Intentionally has NO dependencies on ``app.core.*`` (config, logging,
settings) so worker processes that consume only protocol constants —
e.g. ``mc_gateway_subscriber`` running under systemd with a minimal
env — can import them without instantiating the full FastAPI
``Settings`` object.

Keep this module dependency-free. If a constant needs computed
state, derive it lazily in the call site, not here.
"""

from __future__ import annotations

# Bumped historically; matches the gateway repo. Both
# ``gateway_rpc.py`` (one-shot RPC) and ``mc_gateway_subscriber``
# (long-lived) import from here so they cannot drift.
PROTOCOL_VERSION = 3

# Operator role + scopes for backend-style clients (MC backend, the
# gateway-event subscriber). Distinct from per-agent X-Agent-Token
# auth used by the agent-namespace HTTP routes.
OPERATOR_ROLE = "operator"
GATEWAY_OPERATOR_SCOPES: tuple[str, ...] = (
    "operator.read",
    "operator.admin",
    "operator.approvals",
    "operator.pairing",
)
