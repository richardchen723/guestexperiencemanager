#!/bin/bash
# Setup script to install cron job for incremental sync every 12 hours
# Usage: sudo ./deployment/setup-incremental-sync-cron.sh

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

APP_DIR="/opt/hostaway-messages/app"
SCRIPT_PATH="$APP_DIR/deployment/run-incremental-sync.sh"
CRON_USER="hostaway"

echo "========================================="
echo "Setting up incremental sync cron job"
echo "========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

# Check if script exists
if [ ! -f "$SCRIPT_PATH" ]; then
    echo -e "${RED}Error: Sync script not found: $SCRIPT_PATH${NC}"
    exit 1
fi

# Make script executable
chmod +x "$SCRIPT_PATH"
echo -e "${GREEN}Made script executable${NC}"

# Create cron job entry
# Runs at 00:00 and 12:00 every day
CRON_ENTRY="0 0,12 * * * $SCRIPT_PATH"

# Check if cron job already exists
if sudo -u "$CRON_USER" crontab -l 2>/dev/null | grep -q "$SCRIPT_PATH"; then
    echo -e "${YELLOW}Cron job already exists. Updating...${NC}"
    # Remove existing entry
    sudo -u "$CRON_USER" crontab -l 2>/dev/null | grep -v "$SCRIPT_PATH" | sudo -u "$CRON_USER" crontab -
fi

# Add new cron job
(sudo -u "$CRON_USER" crontab -l 2>/dev/null; echo "$CRON_ENTRY") | sudo -u "$CRON_USER" crontab -

echo -e "${GREEN}Cron job installed successfully!${NC}"
echo ""
echo "Cron job details:"
echo "  Schedule: Every 12 hours (00:00 and 12:00 UTC)"
echo "  User: $CRON_USER"
echo "  Script: $SCRIPT_PATH"
echo "  Log file: /opt/hostaway-messages/logs/incremental-sync.log"
echo ""
echo "To view the cron job:"
echo "  sudo -u $CRON_USER crontab -l"
echo ""
echo "To remove the cron job:"
echo "  sudo -u $CRON_USER crontab -e"
echo "  (then delete the line with $SCRIPT_PATH)"

