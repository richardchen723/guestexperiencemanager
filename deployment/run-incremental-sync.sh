#!/bin/bash
# Wrapper script to run incremental sync
# This script is designed to be run from cron or systemd timer

set -e  # Exit on error

# Set up environment
APP_DIR="/opt/hostaway-messages/app"
VENV_DIR="/opt/hostaway-messages/venv"
ENV_FILE="/opt/hostaway-messages/.env"
LOG_FILE="/opt/hostaway-messages/logs/incremental-sync.log"

# Create log directory if it doesn't exist
mkdir -p "$(dirname "$LOG_FILE")"

# Load environment variables
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# Change to app directory
cd "$APP_DIR"

# Run incremental sync with logging
echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting incremental sync" >> "$LOG_FILE"
"$VENV_DIR/bin/python3" -m sync.sync_manager --incremental >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Incremental sync completed successfully" >> "$LOG_FILE"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Incremental sync failed with exit code $EXIT_CODE" >> "$LOG_FILE"
fi

exit $EXIT_CODE

