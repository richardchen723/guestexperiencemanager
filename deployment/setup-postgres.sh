#!/bin/bash
# PostgreSQL setup script for Hostaway Messages
# Run this script as postgres user: sudo -u postgres ./deployment/setup-postgres.sh
# 
# Note: For EC2 deployments, ensure ec2-setup.sh has been run first to move
# PostgreSQL data directory to EBS volume (/opt/hostaway-messages/postgresql)

set -e  # Exit on error

echo "========================================="
echo "PostgreSQL Setup for Hostaway Messages"
echo "========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as postgres user
if [ "$USER" != "postgres" ]; then
    echo -e "${RED}Please run as postgres user: sudo -u postgres $0${NC}"
    exit 1
fi

# Verify PostgreSQL data directory (for EC2 with EBS)
if [ -L /var/lib/postgresql ] && [ -d /opt/hostaway-messages/postgresql ]; then
    echo -e "${GREEN}PostgreSQL data directory is on EBS volume (good for persistence)${NC}"
elif [ -d /opt/hostaway-messages/postgresql ]; then
    echo -e "${YELLOW}Note: EBS volume detected but PostgreSQL data directory may not be symlinked${NC}"
    echo -e "${YELLOW}For EC2 deployments, ensure ec2-setup.sh has been run${NC}"
fi

DB_NAME="hostaway_prod"
DB_USER="hostaway_user"

echo -e "${GREEN}Step 1: Creating database...${NC}"
if psql -lqt | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
    echo -e "${YELLOW}Database '$DB_NAME' already exists${NC}"
else
    createdb "$DB_NAME"
    echo -e "${GREEN}Database '$DB_NAME' created${NC}"
fi

echo -e "${GREEN}Step 2: Creating database user...${NC}"
# Generate a random password
DB_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)

# Check if user exists
if psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    echo -e "${YELLOW}User '$DB_USER' already exists${NC}"
    echo -e "${YELLOW}To reset password, run: ALTER USER $DB_USER WITH PASSWORD 'new_password';${NC}"
else
    psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';"
    echo -e "${GREEN}User '$DB_USER' created${NC}"
fi

echo -e "${GREEN}Step 3: Granting privileges...${NC}"
psql -d "$DB_NAME" -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
psql -d "$DB_NAME" -c "ALTER DATABASE $DB_NAME OWNER TO $DB_USER;"

# Grant schema privileges (for future schemas)
psql -d "$DB_NAME" -c "GRANT ALL ON SCHEMA public TO $DB_USER;"
psql -d "$DB_NAME" -c "GRANT ALL ON SCHEMA users TO $DB_USER;" 2>/dev/null || true
psql -d "$DB_NAME" -c "GRANT ALL ON SCHEMA tickets TO $DB_USER;" 2>/dev/null || true
psql -d "$DB_NAME" -c "GRANT ALL ON SCHEMA cache TO $DB_USER;" 2>/dev/null || true

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}PostgreSQL setup completed!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo -e "${YELLOW}IMPORTANT: Save these credentials securely!${NC}"
echo ""
echo "Database: $DB_NAME"
echo "User: $DB_USER"
echo "Password: $DB_PASSWORD"
echo ""
echo "DATABASE_URL format:"
echo "postgresql://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME"
echo ""
echo -e "${YELLOW}Add this to your .env file:${NC}"
echo "DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME"
echo ""

