#!/usr/bin/env bash
# board-start.sh — Re-enable heartbeats for all MC board agents
#
# Usage: bash scripts/board-start.sh
#
# What it does:
#   1. Restores heartbeat_config from _heartbeat_backup table in MC database
#   2. Restores gateway openclaw.json from saved backup
#   3. Sessions are already clean (cleared on stop)

set -euo pipefail

MC_DB_HOST="192.168.2.66"
MC_DB="mission_control"
MC_DB_USER="postgres"
MC_DB_PASS="postgres"
GATEWAY_HOST="192.168.2.60"
GATEWAY_CONFIG="/root/.openclaw/openclaw.json"

PSQL="PGPASSWORD=$MC_DB_PASS psql -U $MC_DB_USER -h 127.0.0.1 -d $MC_DB -t -A"

echo "=== Board Start ==="

# Step 1: Restore heartbeats in MC database
echo ""
echo "--- Step 1: Restoring MC database ---"
ssh root@$MC_DB_HOST "$PSQL" << 'SQLEOF'
  UPDATE agents a
  SET heartbeat_config = b.heartbeat_config
  FROM _heartbeat_backup b
  WHERE a.id = b.agent_id;
SQLEOF
echo "  Restored heartbeat configs from backup"

# Verify
echo ""
echo "  Database state:"
ssh root@$MC_DB_HOST "$PSQL -c \"
  SELECT name, heartbeat_config->>'every' as every FROM agents
  WHERE heartbeat_config IS NOT NULL ORDER BY name;
\"" | while read line; do echo "    $line"; done

# Step 2: Restore gateway config
echo ""
echo "--- Step 2: Restoring gateway config ---"
ssh root@$GATEWAY_HOST "python3 -c \"
import json

backup_file = '$GATEWAY_CONFIG.heartbeat-backup'
try:
    with open(backup_file) as f:
        saved = json.load(f)
except FileNotFoundError:
    print('ERROR: No gateway backup found. Run board-stop.sh first.')
    exit(1)

with open('$GATEWAY_CONFIG') as f:
    data = json.load(f)

count = 0
for a in data.get('agents', {}).get('list', []):
    aid = a.get('id', '')
    if aid in saved:
        original = saved[aid]
        hb = a.get('heartbeat', {})
        if original == 'default':
            hb.pop('every', None)
        else:
            hb['every'] = original
        a['heartbeat'] = hb
        count += 1

with open('$GATEWAY_CONFIG', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')

print(f'  {count} agents restored in gateway config')
\""

echo ""
echo "=== Done. Heartbeats ON in both database and gateway. Fresh sessions. ==="
