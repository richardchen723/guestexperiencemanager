#!/bin/bash
# Update existing EC2 instance with latest code
# Usage: ./deployment/update-ec2.sh [ssh-key-path]

set -e

INSTANCE_IP="52.6.90.97"
SSH_KEY="${1:-~/.ssh/ychen-key.pem}"

echo "========================================="
echo "Updating EC2 Instance"
echo "========================================="
echo "Instance IP: $INSTANCE_IP"
echo "SSH Key: $SSH_KEY"
echo ""

# Check if key exists
if [ ! -f "$SSH_KEY" ]; then
    echo "Error: SSH key not found at $SSH_KEY"
    echo "Please provide the path to your SSH key:"
    echo "  ./deployment/update-ec2.sh /path/to/key.pem"
    exit 1
fi

echo "Connecting to instance and updating application..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
    set -e
    
    # Check if app directory exists
    if [ -d "/opt/hostaway-messages/app" ]; then
        echo "✓ App directory found"
        cd /opt/hostaway-messages/app
        
        # Pull latest code
        echo "Pulling latest code from git..."
        sudo -u hostaway git fetch origin
        sudo -u hostaway git checkout main
        sudo -u hostaway git pull origin main
        
        # Run deployment script
        echo "Running deployment script..."
        sudo ./deployment/deploy.sh
        
        echo ""
        echo "✓ Deployment completed!"
        echo ""
        echo "Service status:"
        sudo systemctl status hostaway-dashboard --no-pager -l | head -20
    else
        echo "Error: App directory not found at /opt/hostaway-messages/app"
        echo "Please run full deployment first: ./deployment/DEPLOY_EC2.sh"
        exit 1
    fi
ENDSSH

echo ""
echo "========================================="
echo "Update completed successfully!"
echo "========================================="
echo ""
echo "Check service logs:"
echo "  ssh -i $SSH_KEY ubuntu@$INSTANCE_IP 'sudo journalctl -u hostaway-dashboard -f'"
echo ""
