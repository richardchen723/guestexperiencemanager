# EC2 Update Instructions

## Quick Update (If SSH is configured)

If you have SSH access configured, run:

```bash
./deployment/update-ec2.sh
```

## Manual Update Steps

If SSH is not configured, follow these steps:

### Step 1: Connect to EC2 Instance

**Option A: Using SSH Key**
```bash
# Find your SSH key first
ls -la ~/.ssh/*.pem ~/.ssh/*key*

# Connect (replace with your actual key path)
ssh -i ~/.ssh/your-key.pem ubuntu@52.6.90.97
```

**Option B: Using AWS Systems Manager Session Manager**
```bash
# Get instance ID from Terraform
cd terraform
INSTANCE_ID=$(terraform output -raw ec2_instance_id)

# Connect via Session Manager
aws ssm start-session --target $INSTANCE_ID
```

**Option C: Using AWS Console**
1. Go to AWS EC2 Console
2. Select your instance
3. Click "Connect"
4. Choose "Session Manager" or "EC2 Instance Connect"

### Step 2: Once Connected, Run Update Commands

```bash
# Navigate to app directory
cd /opt/hostaway-messages/app

# Pull latest code
sudo -u hostaway git fetch origin
sudo -u hostaway git checkout main
sudo -u hostaway git pull origin main

# Run deployment script
sudo ./deployment/deploy.sh

# Restart service
sudo systemctl restart hostaway-dashboard

# Check status
sudo systemctl status hostaway-dashboard

# View logs
sudo journalctl -u hostaway-dashboard -f
```

### Step 3: Verify Update

```bash
# Test health endpoint
curl http://127.0.0.1:5001/health

# Check service status
sudo systemctl is-active hostaway-dashboard
```

## Troubleshooting

### If app directory doesn't exist

The instance might not be fully deployed yet. Run the initial deployment:

```bash
# Clone repository
git clone https://github.com/richardchen723/guestexperiencemanager.git ~/hostaway-messages
cd ~/hostaway-messages
chmod +x deployment/*.sh

# Set up EBS volume (if not done)
sudo ./deployment/setup-ebs.sh

# Move to EBS volume
sudo mv ~/hostaway-messages /opt/hostaway-messages/app
cd /opt/hostaway-messages/app

# Run full setup
sudo ./deployment/ec2-setup.sh
sudo -u postgres ./deployment/setup-postgres.sh
sudo ./deployment/setup-env.sh
sudo ./deployment/deploy.sh

# Install service
sudo cp deployment/hostaway-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hostaway-dashboard
sudo systemctl start hostaway-dashboard
```

### If SSH key is missing

1. Check AWS Console for the key pair name (should be "ychen-key")
2. Download the key from where you originally saved it
3. Or create a new key pair and update Terraform configuration

### If Session Manager doesn't work

1. Ensure SSM agent is installed on the instance
2. Ensure IAM role has SSM permissions
3. Check security group allows outbound HTTPS (for SSM)

