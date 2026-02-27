#!/bin/bash
# MySQL automatic backup script
# Purpose: Periodically export the crypto_signals database to /data/backups
# Schedule: Runs daily at 3am (cron: 0 3 * * *)

set -e

# Configuration
BACKUP_DIR="/data/backups"
DB_NAME="${MYSQL_DATABASE:-crypto_signals}"
DB_USER="${MYSQL_USER:-root}"
DB_PASSWORD="${MYSQL_ROOT_PASSWORD}"
DB_HOST="${MYSQL_HOST:-mysql}"
RETENTION_DAYS=7

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Generate backup filename (with timestamp)
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql"

echo "[$(date)] Starting backup for database: $DB_NAME"

# Execute backup
mysqldump -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASSWORD" \
    --single-transaction \
    --quick \
    --lock-tables=false \
    "$DB_NAME" > "$BACKUP_FILE"

# Compress backup file
gzip "$BACKUP_FILE"
BACKUP_FILE="${BACKUP_FILE}.gz"

echo "[$(date)] Backup complete: $BACKUP_FILE"

# Show backup file size
BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date)] Backup size: $BACKUP_SIZE"

# Clean up old backups (retain last 7 days)
echo "[$(date)] Removing backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -type f -mtime +$RETENTION_DAYS -delete

# Show current backup listing
echo "[$(date)] Current backup listing:"
ls -lh "$BACKUP_DIR"

echo "[$(date)] Backup job complete"
