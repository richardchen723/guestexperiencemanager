#!/bin/bash
# Complete deployment script: Create Lightsail instance and deploy application
# This script automates the entire deployment process

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "========================================="
echo "Hostaway Messages - Complete Deployment"
echo "========================================="
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Please create a .env file with your credentials first."
    exit 1
fi

# Step 1: Create Lightsail instance with Terraform
echo -e "${GREEN}Step 1: Creating Lightsail instance with Terraform...${NC}"
cd terraform

# Check if terraform is initialized
if [ ! -d ".terraform" ]; then
    echo "Initializing Terraform..."
    terraform init
fi

# Plan and apply
echo "Planning Terraform deployment..."
terraform plan -out=tfplan

echo ""
echo -e "${YELLOW}Review the Terraform plan above.${NC}"
read -p "Do you want to proceed with creating the Lightsail instance? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Deployment cancelled."
    exit 0
fi

echo "Applying Terraform configuration..."
terraform apply tfplan

# Get instance IP
INSTANCE_IP=$(terraform output -raw lightsail_instance_public_ip 2>/dev/null || terraform output lightsail_instance_public_ip | tr -d '"')
SSH_COMMAND=$(terraform output -raw lightsail_ssh_command 2>/dev/null || echo "ssh ubuntu@$INSTANCE_IP")

echo ""
echo -e "${GREEN}Lightsail instance created!${NC}"
echo "Instance IP: $INSTANCE_IP"
echo ""

# Step 2: Wait for instance to be ready
echo -e "${GREEN}Step 2: Waiting for instance to be ready...${NC}"
echo "Waiting 30 seconds for instance to fully boot..."
sleep 30

# Step 3: Test SSH connection
echo -e "${GREEN}Step 3: Testing SSH connection...${NC}"
MAX_RETRIES=10
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 ubuntu@$INSTANCE_IP "echo 'Connection successful'" 2>/dev/null; then
        echo -e "${GREEN}SSH connection successful!${NC}"
        break
    else
        RETRY_COUNT=$((RETRY_COUNT + 1))
        echo "Waiting for SSH... (attempt $RETRY_COUNT/$MAX_RETRIES)"
        sleep 10
    fi
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo -e "${RED}Failed to connect via SSH. Please check:${NC}"
    echo "1. Instance is running"
    echo "2. Security group allows SSH (port 22)"
    echo "3. You have the correct SSH key"
    exit 1
fi

# Step 4: Copy deployment files
echo -e "${GREEN}Step 4: Copying deployment files to instance...${NC}"
cd ..
scp -r deployment/ ubuntu@$INSTANCE_IP:/tmp/deployment/

# Step 5: Run deployment on instance
echo -e "${GREEN}Step 5: Running deployment on instance...${NC}"
echo ""
echo "This will:"
echo "1. Clone the repository"
echo "2. Set up PostgreSQL"
echo "3. Configure environment variables"
echo "4. Deploy the application"
echo ""

# Create a deployment script that will be run on the instance
cat > /tmp/remote-deploy.sh << 'REMOTE_SCRIPT'
#!/bin/bash
set -e

# Move deployment files to proper location
sudo mkdir -p /opt/hostaway-messages
sudo mv /tmp/deployment /opt/hostaway-messages/app/deployment
cd /opt/hostaway-messages/app

# Clone repository if not already there
if [ ! -d ".git" ]; then
    sudo git clone https://github.com/richardchen723/guestexperiencemanager.git /tmp/app-temp
    sudo mv /tmp/app-temp/* /tmp/app-temp/.* /opt/hostaway-messages/app/ 2>/dev/null || true
    sudo rm -rf /tmp/app-temp
fi

# Make scripts executable
sudo chmod +x deployment/*.sh

# Run quick deployment
sudo bash deployment/QUICK_DEPLOY.sh
REMOTE_SCRIPT

scp /tmp/remote-deploy.sh ubuntu@$INSTANCE_IP:/tmp/
ssh ubuntu@$INSTANCE_IP "chmod +x /tmp/remote-deploy.sh && sudo bash /tmp/remote-deploy.sh"

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Deployment completed!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Instance IP: $INSTANCE_IP"
echo "SSH command: $SSH_COMMAND"
echo ""
echo "Next steps:"
echo "1. SSH to the instance: $SSH_COMMAND"
echo "2. Check service status: sudo systemctl status hostaway-dashboard"
echo "3. View logs: sudo journalctl -u hostaway-dashboard -f"
echo "4. Test health endpoint: curl http://127.0.0.1:5001/health"
echo ""

