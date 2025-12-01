#!/bin/bash
# Automated deployment script that reads .env and deploys to Lightsail
# This script handles the entire deployment process

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "========================================="
echo "Hostaway Messages - Automated Deployment"
echo "========================================="
echo ""

# Check prerequisites
echo -e "${BLUE}Checking prerequisites...${NC}"

# Check if .env exists
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${RED}Error: .env file not found at $PROJECT_ROOT/.env${NC}"
    exit 1
fi
echo -e "${GREEN}✓ .env file found${NC}"

# Check if terraform is installed
if ! command -v terraform &> /dev/null; then
    echo -e "${RED}Error: Terraform is not installed${NC}"
    echo "Install from: https://www.terraform.io/downloads"
    exit 1
fi
echo -e "${GREEN}✓ Terraform installed${NC}"

# Check if AWS CLI is configured
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${YELLOW}Warning: AWS CLI not configured or credentials not set${NC}"
    echo "You may need to run: aws configure"
    read -p "Continue anyway? (yes/no): " continue_anyway
    if [ "$continue_anyway" != "yes" ]; then
        exit 1
    fi
else
    echo -e "${GREEN}✓ AWS credentials configured${NC}"
fi

# Step 1: Create Lightsail instance
echo ""
echo -e "${BLUE}Step 1: Creating Lightsail instance with Terraform...${NC}"
cd "$PROJECT_ROOT/terraform"

# Initialize Terraform if needed
if [ ! -d ".terraform" ]; then
    echo "Initializing Terraform..."
    terraform init
fi

# Plan deployment (skip RDS and S3)
echo "Planning Terraform deployment..."
terraform plan -out=tfplan \
  -var="lightsail_instance_name=hostaway-messages" \
  -var="lightsail_bundle_id=micro_2_0" \
  -var="s3_bucket_name="

echo ""
echo -e "${YELLOW}Review the Terraform plan above.${NC}"
read -p "Proceed with creating the Lightsail instance? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Deployment cancelled."
    exit 0
fi

# Apply Terraform
echo "Creating Lightsail instance..."
terraform apply tfplan

# Get instance details
INSTANCE_IP=$(terraform output -raw lightsail_instance_public_ip 2>/dev/null || terraform output lightsail_instance_public_ip | tr -d '"' | head -1)
if [ -z "$INSTANCE_IP" ] || [ "$INSTANCE_IP" = "null" ]; then
    echo -e "${RED}Error: Could not get instance IP address${NC}"
    terraform output
    exit 1
fi

echo ""
echo -e "${GREEN}Lightsail instance created!${NC}"
echo "Instance IP: $INSTANCE_IP"
echo ""

# Step 2: Wait for instance to be ready
echo -e "${BLUE}Step 2: Waiting for instance to be ready...${NC}"
echo "Waiting 45 seconds for instance to fully boot..."
sleep 45

# Step 3: Test SSH connection
echo -e "${BLUE}Step 3: Testing SSH connection...${NC}"
MAX_RETRIES=15
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes ubuntu@$INSTANCE_IP "echo 'Connection successful'" 2>/dev/null; then
        echo -e "${GREEN}✓ SSH connection successful!${NC}"
        break
    else
        RETRY_COUNT=$((RETRY_COUNT + 1))
        echo "Waiting for SSH... (attempt $RETRY_COUNT/$MAX_RETRIES)"
        sleep 10
    fi
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo -e "${RED}Failed to connect via SSH after $MAX_RETRIES attempts.${NC}"
    echo "Please check:"
    echo "1. Instance is running in AWS console"
    echo "2. Your SSH key is configured in Lightsail"
    echo "3. Network connectivity"
    echo ""
    echo "You can manually connect with: ssh ubuntu@$INSTANCE_IP"
    exit 1
fi

# Step 4: Copy deployment files and .env
echo -e "${BLUE}Step 4: Copying deployment files to instance...${NC}"
scp -r "$PROJECT_ROOT/deployment" ubuntu@$INSTANCE_IP:/tmp/
# Copy .env file securely
scp "$PROJECT_ROOT/.env" ubuntu@$INSTANCE_IP:/tmp/.env

# Step 5: Run deployment on instance
echo -e "${BLUE}Step 5: Running deployment on instance...${NC}"
echo "This will take several minutes..."

ssh ubuntu@$INSTANCE_IP << 'REMOTE_SCRIPT'
set -e

# Move files to proper location
sudo mkdir -p /opt/hostaway-messages
sudo mv /tmp/deployment /opt/hostaway-messages/deployment
sudo mv /tmp/.env /opt/hostaway-messages/.env
sudo chmod 600 /opt/hostaway-messages/.env

cd /opt/hostaway-messages

# Clone repository
if [ ! -d "app" ]; then
    sudo git clone https://github.com/richardchen723/guestexperiencemanager.git app
    sudo chown -R ubuntu:ubuntu app
fi

cd app
sudo chmod +x ../deployment/*.sh

# Run initial setup
echo "Running initial server setup..."
sudo ../deployment/lightsail-setup.sh

# Set up PostgreSQL
echo "Setting up PostgreSQL..."
sudo -u postgres ../deployment/setup-postgres.sh

# Copy .env and configure (non-interactive using existing .env)
echo "Configuring environment..."
sudo cp /opt/hostaway-messages/.env /opt/hostaway-messages/app/.env
sudo chmod 600 /opt/hostaway-messages/app/.env

# Deploy application
echo "Deploying application..."
sudo ../deployment/deploy.sh

# Install systemd service
echo "Installing systemd service..."
sudo cp deployment/hostaway-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hostaway-dashboard
sudo systemctl start hostaway-dashboard

# Wait for service to start
sleep 5

# Check status
echo "Service status:"
sudo systemctl status hostaway-dashboard --no-pager -l || true
REMOTE_SCRIPT

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Deployment completed!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Instance IP: $INSTANCE_IP"
echo "SSH command: ssh ubuntu@$INSTANCE_IP"
echo ""
echo "Next steps:"
echo "1. SSH to instance: ssh ubuntu@$INSTANCE_IP"
echo "2. Check service: sudo systemctl status hostaway-dashboard"
echo "3. View logs: sudo journalctl -u hostaway-dashboard -f"
echo "4. Test health: curl http://127.0.0.1:5001/health"
echo "5. Configure Nginx (optional): See deployment/LIGHTSAIL_DEPLOYMENT.md"
echo ""

