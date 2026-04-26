#!/usr/bin/env bash
# Daily PostgreSQL backup for AgentProvision local database.
# Usage: ./scripts/backup_db.sh
# Cron:  0 3 * * * /Users/nomade/Documents/GitHub/servicetsunami-agents/scripts/backup_db.sh

set -euo pipefail

# Resolve repo root from the script location so the cron entry works no
# matter where the repo is cloned, and survives the agentprovision ->
# servicetsunami-agents repo rename.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BACKUP_DIR="${REPO_ROOT}/backups"
CONTAINER="${BACKUP_CONTAINER:-servicetsunami-agents-db-1}"
DB_NAME="${BACKUP_DB_NAME:-agentprovision}"
DB_USER="${BACKUP_DB_USER:-postgres}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/agentprovision_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[backup] Starting backup at $(date)"

# Dump and compress
docker exec "$CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" --no-owner --no-acl | gzip > "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[backup] Created: $BACKUP_FILE ($SIZE)"

# Cleanup old backups
DELETED=0
find "$BACKUP_DIR" -name "agentprovision_*.sql.gz" -mtime +${KEEP_DAYS} -delete -print | while read f; do
    echo "[backup] Deleted old: $f"
    DELETED=$((DELETED + 1))
done

TOTAL=$(ls "$BACKUP_DIR"/agentprovision_*.sql.gz 2>/dev/null | wc -l | tr -d ' ')
echo "[backup] Done. $TOTAL backups on disk (keeping last ${KEEP_DAYS} days)"
