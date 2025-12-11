#!/bin/bash
# Update existing EC2 deployment with latest code
# This script updates the application on an already-deployed EC2 instance

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "========================================="
echo "Hostaway Messages - EC2 Update"
echo "========================================="
echo ""

# Get instance IP from Terraform
cd terraform
INSTANCE_IP=$(terraform output -raw ec2_elastic_ip 2>/dev/null || terraform output -raw ec2_instance_public_ip 2>/dev/null)
cd ..

if [ -z "$INSTANCE_IP" ]; then
    echo -e "${RED}Error: Could not get instance IP from Terraform${NC}"
    echo "Please ensure Terraform is initialized and the instance is deployed."
    exit 1
fi

echo -e "${GREEN}Instance IP: $INSTANCE_IP${NC}"
echo ""

# Get instance ID for Session Manager
INSTANCE_ID=$(cd terraform && terraform output -raw ec2_instance_id 2>/dev/null || echo "")

# Test SSH connectivity (try with key or Session Manager)
echo -e "${BLUE}Testing connectivity...${NC}"

# Try to find SSH key
SSH_KEY=""
if [ -f ~/.ssh/ychen-key.pem ]; then
    SSH_KEY="-i ~/.ssh/ychen-key.pem"
elif [ -f ~/.ssh/ychen-key ]; then
    SSH_KEY="-i ~/.ssh/ychen-key"
fi

# Try SSH first
if [ -n "$SSH_KEY" ]; then
    if ssh $SSH_KEY -o ConnectTimeout=5 -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP "echo 'Connected'" 2>/dev/null; then
        SSH_CMD="ssh $SSH_KEY -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP"
        echo -e "${GREEN}✓ SSH connection successful (using key)${NC}"
    else
        echo -e "${YELLOW}SSH with key failed, trying Session Manager...${NC}"
        SSH_CMD=""
    fi
fi

# Try Session Manager if SSH failed
if [ -z "$SSH_CMD" ] && [ -n "$INSTANCE_ID" ]; then
    if aws ssm start-session --target $INSTANCE_ID --document-name AWS-StartNonInteractiveCommand --parameters command="echo 'Connected'" 2>/dev/null; then
        echo -e "${GREEN}✓ Session Manager connection successful${NC}"
        SSH_CMD="aws ssm start-session --target $INSTANCE_ID"
        USE_SESSION_MANAGER=true
    else
        echo -e "${RED}Error: Could not connect to instance${NC}"
        echo "Please check:"
        echo "  1. Instance is running"
        echo "  2. Security group allows SSH (port 22) or SSM"
        echo "  3. SSH key is correct, or SSM agent is installed"
        echo "  4. IAM role has SSM permissions (if using Session Manager)"
        exit 1
    fi
else
    if [ -z "$SSH_CMD" ]; then
        echo -e "${RED}Error: Could not connect to instance${NC}"
        echo "Please check:"
        echo "  1. Instance is running"
        echo "  2. Security group allows SSH (port 22)"
        echo "  3. SSH key is correct"
        exit 1
    fi
fi

echo -e "${GREEN}✓ SSH connection successful${NC}"
echo ""

# Update application on remote instance
echo -e "${BLUE}Updating application on EC2 instance...${NC}"
echo "This will:"
echo "  1. Pull latest code from git"
echo "  2. Update dependencies"
echo "  3. Restart the application service"
echo ""
read -p "Press Enter to continue or Ctrl+C to cancel..."

# Create remote script
REMOTE_SCRIPT=$(cat << 'ENDSSH'
    set -e
    
    echo "========================================="
    echo "Updating Hostaway Messages Application"
    echo "========================================="
    
    # Navigate to app directory
        cd /opt/hostaway-messages/app
        
        # Pull latest code
        echo "Pulling latest code from git..."
        sudo -u hostaway git fetch origin
        sudo -u hostaway git checkout main
        sudo -u hostaway git pull origin main
        
        # Run deployment script
        echo "Running deployment script..."
        sudo ./deployment/deploy.sh
        
    # Restart service
    echo "Restarting service..."
    sudo systemctl restart hostaway-dashboard
    
    # Wait a moment
    sleep 3
    
    # Check status
        echo ""
        echo "Service status:"
    sudo systemctl status hostaway-dashboard --no-pager -l || true
    
    echo ""
    echo "========================================="
    echo "Update completed!"
    echo "========================================="
    echo ""
    echo "To view logs: sudo journalctl -u hostaway-dashboard -f"
    echo "To test: curl http://127.0.0.1:5001/health"
ENDSSH
)

# Execute based on connection method
if [ "$USE_SESSION_MANAGER" = "true" ]; then
    # For Session Manager, we need to use a different approach
    echo "$REMOTE_SCRIPT" | aws ssm send-command \
        --instance-ids "$INSTANCE_ID" \
        --document-name "AWS-RunShellScript" \
        --parameters "commands=['$REMOTE_SCRIPT']" \
        --output text > /dev/null
    echo -e "${GREEN}Command sent via Session Manager${NC}"
    echo "Check command status in AWS Console or use: aws ssm list-command-invocations"
else
    # Use SSH
    echo "$REMOTE_SCRIPT" | $SSH_CMD
fi
    set -e
    
    echo "========================================="
    echo "Updating Hostaway Messages Application"
    echo "========================================="
    
    # Navigate to app directory
    cd /opt/hostaway-messages/app
    
    # Pull latest code
    echo "Pulling latest code from git..."
    sudo -u hostaway git fetch origin
    sudo -u hostaway git checkout main
    sudo -u hostaway git pull origin main
    
    # Run deployment script
    echo "Running deployment script..."
    sudo ./deployment/deploy.sh
    
    # Restart service
    echo "Restarting service..."
    sudo systemctl restart hostaway-dashboard
    
    # Wait a moment
    sleep 3
    
    # Check status
    echo ""
    echo "Service status:"
    sudo systemctl status hostaway-dashboard --no-pager -l || true
    
    echo ""
    echo "========================================="
    echo "Update completed!"
    echo "========================================="
    echo ""
    echo "To view logs: sudo journalctl -u hostaway-dashboard -f"
    echo "To test: curl http://127.0.0.1:5001/health"
ENDSSH

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Update completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Instance: $INSTANCE_IP"
echo ""
echo "Next steps:"
echo "1. Check service: ssh ubuntu@$INSTANCE_IP 'sudo systemctl status hostaway-dashboard'"
echo "2. View logs: ssh ubuntu@$INSTANCE_IP 'sudo journalctl -u hostaway-dashboard -f'"
echo "3. Test health: ssh ubuntu@$INSTANCE_IP 'curl http://127.0.0.1:5001/health'"
echo ""
