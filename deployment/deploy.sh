#!/bin/bash
# Deployment script for Hostaway Messages
# Run this script to deploy/update the application
# Usage: sudo ./deployment/deploy.sh [branch-name]

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

APP_DIR="/opt/hostaway-messages/app"
VENV_DIR="/opt/hostaway-messages/venv"
ENV_FILE="/opt/hostaway-messages/.env"
BRANCH="${1:-main}"  # Default to main branch

echo "========================================="
echo "Hostaway Messages - Deployment"
echo "========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

# Check if app directory exists
if [ ! -d "$APP_DIR" ]; then
    echo -e "${RED}Error: App directory not found: $APP_DIR${NC}"
    echo "Please clone the repository first:"
    echo "  git clone <repository-url> $APP_DIR"
    exit 1
fi

# Check if .env file exists
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}Error: .env file not found: $ENV_FILE${NC}"
    echo "Please run: sudo ./deployment/setup-env.sh"
    exit 1
fi

echo -e "${GREEN}Step 1: Pulling latest code from git...${NC}"
cd "$APP_DIR"
sudo -u hostaway git fetch origin
sudo -u hostaway git checkout "$BRANCH"
sudo -u hostaway git pull origin "$BRANCH"

echo -e "${GREEN}Step 2: Creating/updating virtual environment...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    sudo -u hostaway python3 -m venv "$VENV_DIR"
    echo -e "${GREEN}Virtual environment created${NC}"
fi

echo -e "${GREEN}Step 3: Installing/updating dependencies...${NC}"
sudo -u hostaway "$VENV_DIR/bin/pip" install --upgrade pip
sudo -u hostaway "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
sudo -u hostaway "$VENV_DIR/bin/pip" install -r "$APP_DIR/dashboard/requirements.txt"
# Install Gunicorn for production
sudo -u hostaway "$VENV_DIR/bin/pip" install gunicorn

echo -e "${GREEN}Step 4: Copying .env file to app directory (if needed)...${NC}"
# Copy .env to app directory for local access (app loads from project root)
if [ -f "$ENV_FILE" ] && [ ! -f "$APP_DIR/.env" ]; then
    sudo -u hostaway cp "$ENV_FILE" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
fi

echo -e "${GREEN}Step 5: Running database migrations...${NC}"
# Run migrations explicitly before service restart
sudo -u hostaway "$VENV_DIR/bin/python" "$APP_DIR/database/migrations.py" || {
    echo -e "${YELLOW}Warning: Migration script failed, but continuing. Migrations will run on app startup.${NC}"
}

echo -e "${GREEN}Step 6: Restarting systemd services...${NC}"
if systemctl is-active --quiet hostaway-dashboard; then
    systemctl restart hostaway-dashboard
    echo -e "${GREEN}Service restarted${NC}"
else
    echo -e "${YELLOW}Service not running. Start it with: sudo systemctl start hostaway-dashboard${NC}"
fi

echo -e "${GREEN}Step 7: Waiting for service to start...${NC}"
sleep 3

echo -e "${GREEN}Step 8: Health check...${NC}"
if systemctl is-active --quiet hostaway-dashboard; then
    echo -e "${GREEN}Service is running${NC}"
    
    # Try to check health endpoint (if available)
    if curl -f -s http://127.0.0.1:5001/health > /dev/null 2>&1; then
        echo -e "${GREEN}Health check passed${NC}"
    else
        echo -e "${YELLOW}Health endpoint not available (this is OK if not yet implemented)${NC}"
    fi
else
    echo -e "${RED}Service failed to start. Check logs:${NC}"
    echo "  sudo journalctl -u hostaway-dashboard -n 50"
    exit 1
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Deployment completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Service status:"
systemctl status hostaway-dashboard --no-pager -l
echo ""
echo "View logs:"
echo "  sudo journalctl -u hostaway-dashboard -f"
echo ""

