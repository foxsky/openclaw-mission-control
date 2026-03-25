#!/usr/bin/env bash
# agent-status.sh — Show what all agents are doing right now
#
# Usage: bash scripts/agent-status.sh [board_id]
#   If board_id is omitted, shows agents from ALL boards.

set -euo pipefail

MC_DB_HOST="${MC_DB_HOST:-192.168.2.66}"
MC_DB="${MC_DB:-mission_control}"
MC_DB_USER="${MC_DB_USER:-postgres}"
MC_DB_PASS="${MC_DB_PASS:-postgres}"
GATEWAY_HOST="${GATEWAY_HOST:-192.168.2.60}"

BOARD_FILTER=""
if [ -n "${1:-}" ]; then
  BOARD_FILTER="AND a.board_id = '$1'"
fi

PSQL="PGPASSWORD=$MC_DB_PASS psql -U $MC_DB_USER -h 127.0.0.1 -d $MC_DB"

echo "=== Agent Status — $(date -u '+%Y-%m-%d %H:%M UTC') ==="
echo ""

ssh root@$MC_DB_HOST "$PSQL" << SQLEOF
SELECT
  a.name AS agent,
  a.status,
  EXTRACT(EPOCH FROM (now() - a.last_seen_at))::int || 's' AS last_seen,
  a.heartbeat_config::jsonb->>'every' AS hb,
  COALESCE(t.status, '(idle)') AS task_status,
  COALESCE(LEFT(t.title, 45), '—') AS task
FROM agents a
LEFT JOIN LATERAL (
  SELECT t.title, t.status
  FROM tasks t
  WHERE t.assigned_agent_id = a.id
    AND t.status IN ('in_progress', 'review', 'inbox')
  ORDER BY
    CASE t.status WHEN 'in_progress' THEN 1 WHEN 'review' THEN 2 ELSE 3 END
  LIMIT 1
) t ON true
WHERE a.name != 'OpenClaw Primary Gateway Agent'
  $BOARD_FILTER
ORDER BY
  CASE WHEN a.is_board_lead THEN 0 ELSE 1 END,
  a.name;
SQLEOF

echo ""
echo "=== Recent Activity (last 5 min) ==="
ssh root@$GATEWAY_HOST "for d in /root/.openclaw/agents/mc-* /root/.openclaw/agents/lead-*; do
  name=\$(basename \$d)
  [[ \"\$name\" == *gateway* ]] && continue
  latest=\$(ls -t \$d/sessions/*.jsonl 2>/dev/null | head -1)
  [ -z \"\$latest\" ] && continue
  mod=\$(stat -c %Y \"\$latest\" 2>/dev/null)
  now=\$(date +%s)
  age=\$(( (now - mod) / 60 ))
  if [ \$age -lt 5 ]; then
    echo \"  \$name: active \${age}m ago\"
  fi
done" 2>/dev/null
