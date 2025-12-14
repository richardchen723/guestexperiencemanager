#!/bin/bash
# Wrapper script to run incremental sync
# This script is designed to be run from cron or systemd timer

# Don't use set -e here - we want to handle errors gracefully
set -u  # Exit on undefined variable

# Set up environment
APP_DIR="/opt/hostaway-messages/app"
VENV_DIR="/opt/hostaway-messages/venv"
ENV_FILE="/opt/hostaway-messages/.env"
LOG_FILE="/opt/hostaway-messages/logs/incremental-sync.log"
MAX_RUNTIME=3600  # Maximum runtime: 1 hour (3600 seconds)

# Create log directory if it doesn't exist
mkdir -p "$(dirname "$LOG_FILE")"

# Function to log messages
log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

# Load environment variables
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
else
    log_message "ERROR: .env file not found at $ENV_FILE"
    exit 1
fi

# Change to app directory
cd "$APP_DIR" || {
    log_message "ERROR: Failed to change to app directory: $APP_DIR"
    exit 1
}

# Check if Python executable exists
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    log_message "ERROR: Python executable not found: $VENV_DIR/bin/python3"
    exit 1
fi

# Run incremental sync with logging and timeout
log_message "Starting incremental sync (max runtime: ${MAX_RUNTIME}s)"

# Use timeout to prevent the sync from running indefinitely
# If timeout command is available, use it; otherwise run without timeout
if command -v timeout >/dev/null 2>&1; then
    timeout "$MAX_RUNTIME" "$VENV_DIR/bin/python3" -m sync.sync_manager --incremental >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
    
    if [ $EXIT_CODE -eq 124 ]; then
        log_message "ERROR: Incremental sync timed out after ${MAX_RUNTIME}s"
        EXIT_CODE=1
    fi
else
    # Fallback: run without timeout if timeout command not available
    "$VENV_DIR/bin/python3" -m sync.sync_manager --incremental >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
fi

if [ $EXIT_CODE -eq 0 ]; then
    log_message "Incremental sync completed successfully"
else
    log_message "ERROR: Incremental sync failed with exit code $EXIT_CODE"
    # Log last 20 lines of output for debugging
    log_message "Last 20 lines of output:"
    tail -20 "$LOG_FILE" >> "$LOG_FILE" 2>&1 || true
fi

exit $EXIT_CODE

