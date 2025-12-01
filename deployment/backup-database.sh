#!/bin/bash
# Database backup script for Hostaway Messages
# Run this daily via cron: 0 2 * * * /opt/hostaway-messages/deployment/backup-database.sh

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

BACKUP_DIR="/opt/hostaway-messages/backups"
ENV_FILE="/opt/hostaway-messages/.env"
RETENTION_DAYS=30

# Load database URL from .env
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}Error: .env file not found: $ENV_FILE${NC}"
    exit 1
fi

# Extract database connection details from DATABASE_URL
DATABASE_URL=$(grep "^DATABASE_URL=" "$ENV_FILE" | cut -d '=' -f2- | tr -d '"' | tr -d "'")

if [ -z "$DATABASE_URL" ]; then
    echo -e "${RED}Error: DATABASE_URL not found in .env file${NC}"
    exit 1
fi

# Parse DATABASE_URL: postgresql://user:password@host:port/database
# Extract database name
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's/.*\/\([^?]*\).*/\1/p')

if [ -z "$DB_NAME" ]; then
    echo -e "${RED}Error: Could not parse database name from DATABASE_URL${NC}"
    exit 1
fi

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Generate backup filename with timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/hostaway_backup_${TIMESTAMP}.sql"
BACKUP_FILE_COMPRESSED="$BACKUP_FILE.gz"

echo "========================================="
echo "Database Backup: $DB_NAME"
echo "========================================="
echo "Backup file: $BACKUP_FILE_COMPRESSED"

# Perform backup using pg_dump
echo -e "${GREEN}Creating backup...${NC}"
export PGPASSWORD=$(echo "$DATABASE_URL" | sed -n 's/.*:\/\/[^:]*:\([^@]*\)@.*/\1/p')
pg_dump "$DATABASE_URL" > "$BACKUP_FILE"

# Compress backup
echo -e "${GREEN}Compressing backup...${NC}"
gzip "$BACKUP_FILE"

# Get backup size
BACKUP_SIZE=$(du -h "$BACKUP_FILE_COMPRESSED" | cut -f1)
echo -e "${GREEN}Backup created: $BACKUP_FILE_COMPRESSED ($BACKUP_SIZE)${NC}"

# Clean up old backups (keep last 30 days)
echo -e "${GREEN}Cleaning up old backups (keeping last $RETENTION_DAYS days)...${NC}"
find "$BACKUP_DIR" -name "hostaway_backup_*.sql.gz" -type f -mtime +$RETENTION_DAYS -delete
DELETED_COUNT=$(find "$BACKUP_DIR" -name "hostaway_backup_*.sql.gz" -type f | wc -l)
echo -e "${GREEN}Backups retained: $DELETED_COUNT${NC}"

# Optional: Upload to S3 (uncomment and configure if needed)
# if command -v aws &> /dev/null; then
#     echo -e "${GREEN}Uploading to S3...${NC}"
#     aws s3 cp "$BACKUP_FILE_COMPRESSED" s3://your-backup-bucket/hostaway-backups/ --storage-class STANDARD_IA
#     echo -e "${GREEN}Upload complete${NC}"
# fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Backup completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""

# Exit successfully
exit 0

