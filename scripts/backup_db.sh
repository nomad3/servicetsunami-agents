#!/usr/bin/env bash
# Daily PostgreSQL backup for ServiceTsunami local database
# Usage: ./scripts/backup_db.sh
# Cron:  0 3 * * * /Users/nomade/Documents/GitHub/servicetsunami-agents/scripts/backup_db.sh

set -euo pipefail

BACKUP_DIR="/Users/nomade/Documents/GitHub/servicetsunami-agents/backups"
CONTAINER="servicetsunami-agents-db-1"
DB_NAME="servicetsunami"
DB_USER="postgres"
KEEP_DAYS=7
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/servicetsunami_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[backup] Starting backup at $(date)"

# Dump and compress
docker exec "$CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" --no-owner --no-acl | gzip > "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[backup] Created: $BACKUP_FILE ($SIZE)"

# Cleanup old backups
DELETED=0
find "$BACKUP_DIR" -name "servicetsunami_*.sql.gz" -mtime +${KEEP_DAYS} -delete -print | while read f; do
    echo "[backup] Deleted old: $f"
    DELETED=$((DELETED + 1))
done

TOTAL=$(ls "$BACKUP_DIR"/servicetsunami_*.sql.gz 2>/dev/null | wc -l | tr -d ' ')
echo "[backup] Done. $TOTAL backups on disk (keeping last ${KEEP_DAYS} days)"
