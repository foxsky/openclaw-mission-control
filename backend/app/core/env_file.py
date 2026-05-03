"""Shell-env-style file parser shared by ``mc_hooks.py`` and the
``mc_gateway_subscriber`` worker.

Behavior matches what an operator expects from a sourced shell file:

- Blank lines and ``#``-prefixed comment lines are skipped.
- Each non-comment line is ``KEY=VALUE``.
- Unquoted ``VALUE``: everything up to the first ``#`` (inline
  comment) is taken, then trimmed. ``KEY=abc # prod`` → ``abc``.
- Quoted ``VALUE`` (``"..."`` or ``'...'``): preserved verbatim
  inside the quotes, including ``#``. ``KEY="abc#1"`` → ``abc#1``.
- CRLF line endings are normalized so the trailing ``\\r`` doesn't
  bleed into the value.

Missing file → empty dict (callers decide whether to escalate).
"""

from __future__ import annotations


def load_env_file(path: str) -> dict[str, str]:
    """Parse a shell-env-style file. Returns ``{}`` if missing."""
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.rstrip("\r\n").strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key:
                    continue
                out[key] = parse_env_value(value)
    except OSError:
        return {}
    return out


def parse_env_value(raw: str) -> str:
    """Parse the right-hand side of a ``KEY=...`` line.

    Public so callers can reuse the same quoting/comment semantics on
    in-memory strings (e.g. when reading a token from elsewhere with
    matching expectations).
    """
    value = raw.lstrip()
    if value.startswith('"'):
        end = value.find('"', 1)
        if end != -1:
            return value[1:end]
        return value[1:]
    if value.startswith("'"):
        end = value.find("'", 1)
        if end != -1:
            return value[1:end]
        return value[1:]
    hash_idx = value.find("#")
    if hash_idx != -1:
        value = value[:hash_idx]
    return value.strip()
