#!/bin/bash
# Initial server setup script for AWS EC2
# Run this script on a fresh Ubuntu 22.04 LTS instance
# This script should be run AFTER setup-ebs.sh has mounted the EBS volume

set -e  # Exit on error

echo "========================================="
echo "Hostaway Messages - EC2 Setup"
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

# Check if EBS volume is mounted
MOUNT_POINT="/opt/hostaway-messages"
if ! mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    echo -e "${RED}Error: EBS volume is not mounted at $MOUNT_POINT${NC}"
    echo "Please run setup-ebs.sh first:"
    echo "  sudo ./deployment/setup-ebs.sh"
    exit 1
fi

echo -e "${GREEN}EBS volume is mounted at $MOUNT_POINT${NC}"

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

echo -e "${GREEN}Step 7: Installing AWS CLI (for future S3 backups)...${NC}"
apt-get install -y awscli

echo -e "${GREEN}Step 8: Creating application user...${NC}"
if ! id "hostaway" &>/dev/null; then
    useradd -m -s /bin/bash hostaway
    echo -e "${YELLOW}User 'hostaway' created. You may want to set a password: sudo passwd hostaway${NC}"
else
    echo -e "${YELLOW}User 'hostaway' already exists${NC}"
fi

echo -e "${GREEN}Step 9: Ensuring directory structure exists on EBS volume...${NC}"
mkdir -p "$MOUNT_POINT"/{app,venv,conversations,logs,backups,postgresql}
chown -R hostaway:hostaway "$MOUNT_POINT"

echo -e "${GREEN}Step 10: Configuring PostgreSQL to use EBS volume...${NC}"
# Stop PostgreSQL before moving data directory
systemctl stop postgresql || true

# Check if PostgreSQL data is already on EBS
if [ -L /var/lib/postgresql ]; then
    echo -e "${YELLOW}PostgreSQL data directory is already symlinked${NC}"
elif [ -d /var/lib/postgresql ]; then
    # Move PostgreSQL data to EBS volume
    echo -e "${GREEN}Moving PostgreSQL data directory to EBS volume...${NC}"
    
    # Backup existing data if it exists and has content
    if [ "$(ls -A /var/lib/postgresql 2>/dev/null)" ]; then
        echo -e "${YELLOW}Existing PostgreSQL data found. Backing up...${NC}"
        mv /var/lib/postgresql "$MOUNT_POINT/postgresql.backup"
    fi
    
    # Create new directory on EBS
    mkdir -p "$MOUNT_POINT/postgresql"
    chown postgres:postgres "$MOUNT_POINT/postgresql"
    
    # Create symlink
    mv /var/lib/postgresql /var/lib/postgresql.old
    ln -s "$MOUNT_POINT/postgresql" /var/lib/postgresql
    chown postgres:postgres /var/lib/postgresql
    
    echo -e "${GREEN}PostgreSQL data directory moved to EBS volume${NC}"
fi

# Start PostgreSQL
systemctl start postgresql
systemctl enable postgresql

echo -e "${GREEN}Step 11: Configuring firewall (UFW)...${NC}"
# Allow SSH
ufw allow 22/tcp
# Allow HTTP (for Nginx)
ufw allow 80/tcp
# Allow HTTPS (for Nginx)
ufw allow 443/tcp
# Enable firewall (non-interactive)
echo "y" | ufw enable

echo -e "${GREEN}Step 12: Starting and enabling services...${NC}"
systemctl start nginx
systemctl enable nginx

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}EC2 setup completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Next steps:"
echo "1. Run: sudo -u postgres ./deployment/setup-postgres.sh"
echo "2. Clone your repository to $MOUNT_POINT/app"
echo "3. Run: sudo ./deployment/setup-env.sh"
echo "4. Run: sudo ./deployment/deploy.sh"
echo ""




