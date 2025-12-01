# SSH Setup for Lightsail Instance

The Lightsail instance has been created, but SSH access needs to be configured.

## Option 1: Use Lightsail Browser-Based SSH (Easiest)

1. Go to AWS Lightsail Console: https://lightsail.aws.amazon.com/
2. Click on your instance: `hostaway-messages`
3. Click "Connect using SSH" button
4. This will open a browser-based terminal

## Option 2: Configure SSH Key Pair

1. Go to AWS Lightsail Console
2. Click on "Account" → "SSH keys"
3. Download or create a new SSH key pair
4. Attach the key pair to your instance:
   - Go to your instance
   - Click "Connect" tab
   - Click "Change SSH key pair"
   - Select your key pair

## Option 3: Use AWS CLI to Get SSH Command

```bash
aws lightsail get-instance-access-details \
  --instance-name hostaway-messages \
  --protocol ssh
```

## Once SSH is Working

After you can SSH into the instance, run the deployment:

```bash
# SSH into the instance
ssh ubuntu@98.81.104.240

# Then run these commands on the instance:
sudo mkdir -p /opt/hostaway-messages
sudo git clone https://github.com/richardchen723/guestexperiencemanager.git /opt/hostaway-messages/app
cd /opt/hostaway-messages/app
sudo chmod +x deployment/*.sh

# Copy .env file (you'll need to create it with your credentials)
# Or use the setup script which will prompt for credentials
sudo ./deployment/setup-env.sh

# Run deployment
sudo ./deployment/lightsail-setup.sh
sudo -u postgres ./deployment/setup-postgres.sh
sudo ./deployment/deploy.sh

# Install and start service
sudo cp deployment/hostaway-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hostaway-dashboard
sudo systemctl start hostaway-dashboard
```

## Instance Details

- **Instance Name**: hostaway-messages
- **Public IP**: 98.81.104.240
- **Static IP**: 44.196.144.24
- **SSH Command**: `ssh ubuntu@98.81.104.240`

