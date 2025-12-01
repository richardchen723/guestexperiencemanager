#!/bin/bash
# Quick deployment script - Run this on your Lightsail instance
# This script combines all deployment steps into one

set -e

echo "========================================="
echo "Hostaway Messages - Quick Deployment"
echo "========================================="
echo ""
echo "This script will:"
echo "1. Clone the repository"
echo "2. Run initial server setup"
echo "3. Set up PostgreSQL"
echo "4. Configure environment variables (interactive)"
echo "5. Deploy the application"
echo "6. Install and start systemd service"
echo ""
read -p "Press Enter to continue or Ctrl+C to cancel..."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Step 1: Clone repository
echo -e "${GREEN}Step 1: Cloning repository...${NC}"
if [ -d "/opt/hostaway-messages/app" ]; then
    echo -e "${YELLOW}Repository already exists. Skipping clone.${NC}"
    echo "To update, run: cd /opt/hostaway-messages/app && git pull"
else
    sudo mkdir -p /opt/hostaway-messages
    sudo git clone https://github.com/richardchen723/guestexperiencemanager.git /opt/hostaway-messages/app
    sudo chown -R hostaway:hostaway /opt/hostaway-messages/app
fi

# Step 2: Run initial setup
echo -e "${GREEN}Step 2: Running initial server setup...${NC}"
cd /opt/hostaway-messages/app
sudo chmod +x deployment/*.sh
sudo ./deployment/lightsail-setup.sh

# Step 3: Set up PostgreSQL
echo -e "${GREEN}Step 3: Setting up PostgreSQL...${NC}"
echo "You will need to save the database credentials shown below!"
sudo -u postgres ./deployment/setup-postgres.sh

# Step 4: Configure environment variables
echo -e "${GREEN}Step 4: Configuring environment variables...${NC}"
echo "You will be prompted to enter your credentials."
sudo ./deployment/setup-env.sh

# Step 5: Deploy application
echo -e "${GREEN}Step 5: Deploying application...${NC}"
sudo ./deployment/deploy.sh

# Step 6: Install systemd service
echo -e "${GREEN}Step 6: Installing systemd service...${NC}"
sudo cp deployment/hostaway-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hostaway-dashboard
sudo systemctl start hostaway-dashboard

# Wait a moment for service to start
sleep 3

# Check status
echo -e "${GREEN}Checking service status...${NC}"
sudo systemctl status hostaway-dashboard --no-pager -l

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Deployment completed!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Next steps:"
echo "1. Check service logs: sudo journalctl -u hostaway-dashboard -f"
echo "2. Test health endpoint: curl http://127.0.0.1:5001/health"
echo "3. Configure Nginx (optional): See LIGHTSAIL_DEPLOYMENT.md Step 8"
echo ""

