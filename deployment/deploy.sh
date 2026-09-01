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
PLAYWRIGHT_BROWSERS_DIR="$APP_DIR/data/ms-playwright"
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

set_env_value() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

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

echo -e "${GREEN}Step 3b: Installing Playwright Chromium...${NC}"
mkdir -p "$PLAYWRIGHT_BROWSERS_DIR"
chown -R hostaway:hostaway "$APP_DIR/data"
env PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_DIR" \
    "$VENV_DIR/bin/python" -m playwright install-deps chromium
sudo -u hostaway env PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_DIR" \
    "$VENV_DIR/bin/python" -m playwright install chromium

echo -e "${GREEN}Step 4: Enabling one-time stay outcome analysis and syncing .env...${NC}"
set_env_value "KPI_ENABLE_STAY_OUTCOME_CLASSIFICATION" "True"
set_env_value "KPI_STAY_OUTCOME_MODEL" "gpt-5.6-luna"
STAY_OUTCOME_ACTIVATED_AT="$(sed -n 's/^KPI_STAY_OUTCOME_ACTIVATED_AT=//p' "$ENV_FILE" | head -n 1)"
if [ -z "$STAY_OUTCOME_ACTIVATED_AT" ]; then
    STAY_OUTCOME_ACTIVATED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    set_env_value "KPI_STAY_OUTCOME_ACTIVATED_AT" "$STAY_OUTCOME_ACTIVATED_AT"
fi
# Keep the application-local dotenv copy identical to the systemd EnvironmentFile.
cp "$ENV_FILE" "$APP_DIR/.env"
chown hostaway:hostaway "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

echo -e "${GREEN}Step 5: Running database migrations...${NC}"
# Run migrations explicitly before service restart
sudo -u hostaway "$VENV_DIR/bin/python" "$APP_DIR/database/migrations.py" || {
    echo -e "${YELLOW}Warning: Migration script failed, but continuing. Migrations will run on app startup.${NC}"
}

echo -e "${GREEN}Step 5b: Migrating listing-quality audit tables...${NC}"
sudo -u hostaway "$VENV_DIR/bin/python" -c \
    "from brain.models import init_listing_audit_tables; init_listing_audit_tables()"

echo -e "${GREEN}Step 6: Installing/updating systemd services...${NC}"
# Install disk-usage safeguards before restarting the application services.
if [ -f "$APP_DIR/deployment/logrotate-hostaway" ]; then
    install -o root -g root -m 0644 \
        "$APP_DIR/deployment/logrotate-hostaway" /etc/logrotate.d/hostaway
fi
if [ -f "$APP_DIR/deployment/journald-hostaway.conf" ]; then
    install -d -o root -g root -m 0755 /etc/systemd/journald.conf.d
    install -o root -g root -m 0644 \
        "$APP_DIR/deployment/journald-hostaway.conf" \
        /etc/systemd/journald.conf.d/hostaway.conf
    systemctl restart systemd-journald
fi

# Copy service files
if [ -f "$APP_DIR/deployment/hostaway-dashboard.service" ]; then
    cp "$APP_DIR/deployment/hostaway-dashboard.service" /etc/systemd/system/
    systemctl daemon-reload
fi
if [ -f "$APP_DIR/deployment/hostaway-kpi-worker.service" ]; then
    cp "$APP_DIR/deployment/hostaway-kpi-worker.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable hostaway-kpi-worker
    systemctl restart hostaway-kpi-worker
    echo -e "${GREEN}KPI refresh worker enabled${NC}"
fi
if [ -f "$APP_DIR/deployment/hostaway-recurring-tasks.service" ]; then
    cp "$APP_DIR/deployment/hostaway-recurring-tasks.service" /etc/systemd/system/
    cp "$APP_DIR/deployment/hostaway-recurring-tasks.timer" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable hostaway-recurring-tasks.timer
    systemctl start hostaway-recurring-tasks.timer
    echo -e "${GREEN}Recurring tasks timer enabled${NC}"
fi
if [ -f "$APP_DIR/deployment/hostaway-review-sync.service" ]; then
    cp "$APP_DIR/deployment/hostaway-review-sync.service" /etc/systemd/system/
    cp "$APP_DIR/deployment/hostaway-review-sync.timer" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable hostaway-review-sync.timer
    systemctl restart hostaway-review-sync.timer
    echo -e "${GREEN}Review status sync timer enabled${NC}"
fi
if compgen -G "$APP_DIR/deployment/str-signal-brain*.service" > /dev/null || compgen -G "$APP_DIR/deployment/str-signal-brain*.timer" > /dev/null; then
    cp "$APP_DIR"/deployment/str-signal-brain*.service /etc/systemd/system/ 2>/dev/null || true
    cp "$APP_DIR"/deployment/str-signal-brain*.timer /etc/systemd/system/ 2>/dev/null || true
    systemctl daemon-reload
    for timer in "$APP_DIR"/deployment/str-signal-brain*.timer; do
        [ -f "$timer" ] || continue
        timer_name="$(basename "$timer")"
        case "$timer_name" in
            str-signal-brain-listing-audit-daily.timer|str-signal-brain-listing-audit-weekly.timer)
                systemctl disable --now "$timer_name" >/dev/null 2>&1 || true
                echo -e "${GREEN}Listing audit timer disabled; Codex automation owns this schedule${NC}"
                continue
                ;;
        esac
        systemctl enable "$timer_name"
        systemctl start "$timer_name"
    done
    echo -e "${GREEN}STR Signal Brain services/timers installed${NC}"
fi

echo -e "${GREEN}Step 7: Restarting systemd services...${NC}"
if systemctl is-active --quiet hostaway-dashboard; then
    systemctl restart hostaway-dashboard
    echo -e "${GREEN}Service restarted${NC}"
else
    echo -e "${YELLOW}Service not running. Start it with: sudo systemctl start hostaway-dashboard${NC}"
fi

echo -e "${GREEN}Step 8: Waiting for service to start...${NC}"
sleep 3

echo -e "${GREEN}Step 9: Health check...${NC}"
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
