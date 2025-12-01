#!/bin/bash
# Initial server setup script for AWS Lightsail
# Run this script on a fresh Ubuntu 22.04 LTS instance

set -e  # Exit on error

echo "========================================="
echo "Hostaway Messages - Lightsail Setup"
echo "========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

echo -e "${GREEN}Step 1: Updating system packages...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y

echo -e "${GREEN}Step 2: Installing PostgreSQL 15...${NC}"
apt-get install -y postgresql postgresql-contrib

echo -e "${GREEN}Step 3: Installing Python 3.10+ and pip...${NC}"
apt-get install -y python3 python3-pip python3-venv python3-dev

echo -e "${GREEN}Step 4: Installing build dependencies...${NC}"
apt-get install -y build-essential libpq-dev

echo -e "${GREEN}Step 5: Installing Nginx (optional, for reverse proxy)...${NC}"
apt-get install -y nginx

echo -e "${GREEN}Step 6: Installing Git...${NC}"
apt-get install -y git

echo -e "${GREEN}Step 7: Creating application user...${NC}"
if ! id "hostaway" &>/dev/null; then
    useradd -m -s /bin/bash hostaway
    echo -e "${YELLOW}User 'hostaway' created. You may want to set a password: sudo passwd hostaway${NC}"
else
    echo -e "${YELLOW}User 'hostaway' already exists${NC}"
fi

echo -e "${GREEN}Step 8: Creating directory structure...${NC}"
mkdir -p /opt/hostaway-messages/{app,venv,conversations,logs,backups}
chown -R hostaway:hostaway /opt/hostaway-messages

echo -e "${GREEN}Step 9: Configuring firewall (UFW)...${NC}"
# Allow SSH
ufw allow 22/tcp
# Allow HTTP (for Nginx)
ufw allow 80/tcp
# Allow HTTPS (for Nginx)
ufw allow 443/tcp
# Enable firewall (non-interactive)
echo "y" | ufw enable

echo -e "${GREEN}Step 10: Starting and enabling services...${NC}"
systemctl start postgresql
systemctl enable postgresql
systemctl start nginx
systemctl enable nginx

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Setup completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Next steps:"
echo "1. Run: sudo -u postgres ./deployment/setup-postgres.sh"
echo "2. Clone your repository to /opt/hostaway-messages/app"
echo "3. Run: sudo ./deployment/setup-env.sh"
echo "4. Run: sudo ./deployment/deploy.sh"
echo ""

