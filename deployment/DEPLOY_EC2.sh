#!/bin/bash
# Complete EC2 Deployment Script
# This script automates the entire EC2 deployment process
# Run this from your local machine after Terraform has created the infrastructure

set -e

echo "========================================="
echo "Hostaway Messages - EC2 Deployment"
echo "========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Check prerequisites
echo -e "${BLUE}Checking prerequisites...${NC}"

if ! command -v terraform &> /dev/null; then
    echo -e "${RED}Error: Terraform is not installed${NC}"
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI is not installed${NC}"
    exit 1
fi

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}Error: AWS credentials not configured${NC}"
    echo "Run: aws configure"
    exit 1
fi

echo -e "${GREEN}✓ Prerequisites met${NC}"
echo ""

# Step 1: Terraform deployment
echo -e "${BLUE}Step 1: Deploying EC2 infrastructure with Terraform...${NC}"
echo -e "${YELLOW}This will create:${NC}"
echo "  - EC2 instance (t4g.medium, Ubuntu 22.04 LTS ARM64)"
echo "  - EBS volume (50GB gp3)"
echo "  - Security group (SSH, HTTP, HTTPS)"
echo "  - Elastic IP (if enabled)"
echo ""
read -p "Press Enter to continue or Ctrl+C to cancel..."

cd terraform

if [ ! -f "terraform.tfvars" ]; then
    echo -e "${YELLOW}terraform.tfvars not found. Using defaults.${NC}"
    echo "You can create terraform.tfvars to customize settings."
fi

echo -e "${GREEN}Initializing Terraform...${NC}"
terraform init

echo -e "${GREEN}Planning deployment...${NC}"
terraform plan

echo ""
read -p "Review the plan above. Continue with apply? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Deployment cancelled."
    exit 0
fi

echo -e "${GREEN}Applying Terraform configuration...${NC}"
terraform apply -auto-approve

echo ""
echo -e "${GREEN}✓ Infrastructure deployed${NC}"
echo ""

# Get instance details
echo -e "${BLUE}Retrieving instance details...${NC}"
INSTANCE_IP=$(terraform output -raw ec2_elastic_ip 2>/dev/null || terraform output -raw ec2_instance_public_ip 2>/dev/null)
INSTANCE_ID=$(terraform output -raw ec2_instance_id 2>/dev/null)
SSH_CMD=$(terraform output -raw ec2_ssh_command 2>/dev/null || echo "ssh ubuntu@$INSTANCE_IP")

echo -e "${GREEN}Instance IP: $INSTANCE_IP${NC}"
echo -e "${GREEN}Instance ID: $INSTANCE_ID${NC}"
echo ""

# Wait for instance to be ready
echo -e "${BLUE}Waiting for instance to be ready...${NC}"
sleep 10

# Check SSH connectivity
echo -e "${BLUE}Checking SSH connectivity...${NC}"
MAX_RETRIES=30
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP "echo 'Connected'" 2>/dev/null; then
        echo -e "${GREEN}✓ SSH connection successful${NC}"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    echo -e "${YELLOW}Waiting for SSH... ($RETRY_COUNT/$MAX_RETRIES)${NC}"
    sleep 5
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo -e "${RED}Error: Could not connect to instance via SSH${NC}"
    echo "Please check:"
    echo "  1. Security group allows SSH (port 22)"
    echo "  2. Instance is running"
    echo "  3. SSH key is correct (if using key pair)"
    exit 1
fi

cd ..

# Step 2: Prepare deployment files
echo ""
echo -e "${BLUE}Step 2: Preparing deployment files...${NC}"

# Create a temporary directory for deployment files
TEMP_DIR=$(mktemp -d)
echo "Using temporary directory: $TEMP_DIR"

# Copy deployment scripts
cp -r deployment/* "$TEMP_DIR/"
chmod +x "$TEMP_DIR"/*.sh

echo -e "${GREEN}✓ Deployment files prepared${NC}"

# Step 3: Upload deployment files
echo ""
echo -e "${BLUE}Step 3: Uploading deployment files to EC2 instance...${NC}"

scp -o StrictHostKeyChecking=no -r "$TEMP_DIR" ubuntu@$INSTANCE_IP:~/deployment 2>/dev/null || {
    echo -e "${YELLOW}SCP failed, trying alternative method...${NC}"
    # Alternative: Clone from git on the instance
    ssh -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP "git clone https://github.com/richardchen723/guestexperiencemanager.git ~/hostaway-messages || true"
}

echo -e "${GREEN}✓ Files uploaded${NC}"

# Step 4: Run deployment on instance
echo ""
echo -e "${BLUE}Step 4: Running deployment on EC2 instance...${NC}"
echo -e "${YELLOW}This will:${NC}"
echo "  1. Set up EBS volume"
echo "  2. Install dependencies (PostgreSQL, Python, Nginx)"
echo "  3. Set up PostgreSQL database"
echo "  4. Configure environment variables (interactive)"
echo "  5. Deploy application"
echo "  6. Start services"
echo ""
read -p "Press Enter to continue..."

# Create deployment script to run on remote
cat > "$TEMP_DIR/remote-deploy.sh" << 'REMOTE_SCRIPT'
#!/bin/bash
set -e

cd ~/hostaway-messages || cd ~/deployment

# Make scripts executable
chmod +x deployment/*.sh 2>/dev/null || chmod +x *.sh

# Step 1: Set up EBS volume
echo "Setting up EBS volume..."
sudo ./deployment/setup-ebs.sh || sudo ./setup-ebs.sh

# Step 2: Move repository to EBS volume
if [ -d ~/hostaway-messages ]; then
    sudo mv ~/hostaway-messages /opt/hostaway-messages/app
    cd /opt/hostaway-messages/app
fi

# Step 3: Run EC2 setup
echo "Running EC2 setup..."
sudo ./deployment/ec2-setup.sh

# Step 4: Set up PostgreSQL
echo "Setting up PostgreSQL..."
echo "IMPORTANT: Save the database credentials shown below!"
sudo -u postgres ./deployment/setup-postgres.sh

# Step 5: Configure environment variables
echo "Configuring environment variables..."
sudo ./deployment/setup-env.sh

# Step 6: Deploy application
echo "Deploying application..."
sudo ./deployment/deploy.sh

# Step 7: Install systemd service
echo "Installing systemd service..."
sudo cp deployment/hostaway-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hostaway-dashboard
sudo systemctl start hostaway-dashboard

# Wait for service to start
sleep 5

# Check status
echo ""
echo "Service status:"
sudo systemctl status hostaway-dashboard --no-pager -l || true

echo ""
echo "========================================="
echo "Deployment completed!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Check service: sudo systemctl status hostaway-dashboard"
echo "2. View logs: sudo journalctl -u hostaway-dashboard -f"
echo "3. Test: curl http://127.0.0.1:5001/health"
echo "4. Configure Nginx (optional): See EC2_DEPLOYMENT.md"
REMOTE_SCRIPT

chmod +x "$TEMP_DIR/remote-deploy.sh"

# Upload and run remote deployment
if [ -d "$TEMP_DIR" ]; then
    scp -o StrictHostKeyChecking=no "$TEMP_DIR/remote-deploy.sh" ubuntu@$INSTANCE_IP:~/remote-deploy.sh
    ssh -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP "chmod +x ~/remote-deploy.sh && bash ~/remote-deploy.sh"
else
    # Fallback: Run commands directly via SSH
    echo -e "${YELLOW}Running deployment commands directly...${NC}"
    ssh -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
        # Clone repository
        git clone https://github.com/richardchen723/guestexperiencemanager.git ~/hostaway-messages || true
        cd ~/hostaway-messages
        chmod +x deployment/*.sh
        
        # Set up EBS volume
        sudo ./deployment/setup-ebs.sh
        
        # Move to EBS volume
        sudo mv ~/hostaway-messages /opt/hostaway-messages/app
        cd /opt/hostaway-messages/app
        
        # Run setup
        sudo ./deployment/ec2-setup.sh
        sudo -u postgres ./deployment/setup-postgres.sh
        sudo ./deployment/setup-env.sh
        sudo ./deployment/deploy.sh
        
        # Install service
        sudo cp deployment/hostaway-dashboard.service /etc/systemd/system/
        sudo systemctl daemon-reload
        sudo systemctl enable hostaway-dashboard
        sudo systemctl start hostaway-dashboard
ENDSSH
fi

# Cleanup
rm -rf "$TEMP_DIR"

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Deployment completed!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Instance details:"
echo "  IP Address: $INSTANCE_IP"
echo "  Instance ID: $INSTANCE_ID"
echo "  SSH Command: $SSH_CMD"
echo ""
echo "Next steps:"
echo "1. SSH to instance: $SSH_CMD"
echo "2. Check service: sudo systemctl status hostaway-dashboard"
echo "3. View logs: sudo journalctl -u hostaway-dashboard -f"
echo "4. Test health: curl http://127.0.0.1:5001/health"
echo "5. Configure Nginx and SSL (see EC2_DEPLOYMENT.md)"
echo "6. Update DNS to point to: $INSTANCE_IP"
echo ""

