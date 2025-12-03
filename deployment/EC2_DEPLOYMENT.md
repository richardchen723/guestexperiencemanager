# AWS EC2 Deployment Guide

This guide provides step-by-step instructions for deploying the Hostaway Messages application to AWS EC2 with EBS volume for persistent storage.

## Prerequisites

- AWS account with EC2 access
- Terraform installed (>= 1.0)
- AWS CLI configured with credentials
- Domain name (optional, for HTTPS)
- Hostaway API credentials (for sync operations)
- OpenAI API key (for AI features)
- Google OAuth credentials (optional, for authentication)
- EC2 Key Pair (optional, for SSH access - or use AWS Systems Manager Session Manager)

## Architecture Overview

The deployment uses:
- **EC2 Instance**: `t4g.medium` (4GB RAM, 2 vCPU, ARM-based) - On-Demand pricing
- **EBS Volume**: 50GB gp3 volume for persistent storage (PostgreSQL data, conversation files, logs)
- **Elastic IP**: Static IP address for DNS stability
- **Ubuntu 22.04 LTS** (ARM64) on EC2 instance
- **PostgreSQL 15** installed locally on the instance (data on EBS volume)
- **Python 3.10+** with virtual environment
- **Gunicorn** as WSGI server
- **systemd** for process management
- **Nginx** as reverse proxy (optional, for HTTPS)
- **Local filesystem** (on EBS volume) for conversation file storage

## Estimated Monthly Cost

- **EC2 t4g.medium**: ~$24.19/month (0.0336/hour × 730 hours)
- **EBS 50GB gp3**: ~$5/month (0.10/GB-month)
- **Elastic IP**: Free (when attached to running instance)
- **Data Transfer**: First 1GB free, then ~$0.09/GB
- **Total**: ~$29-35/month (depending on data transfer)

**Cost Optimization**: Consider Reserved Instances (1-year commitment) for ~40% savings (~$15/month for compute).

## Step 1: Create EC2 Infrastructure with Terraform

1. Navigate to the terraform directory:
   ```bash
   cd terraform
   ```

2. Initialize Terraform (if not already done):
   ```bash
   terraform init
   ```

3. Review and customize variables (optional):
   ```bash
   # Edit terraform.tfvars or use defaults
   # Key variables in terraform/ec2_variables.tf:
   # - ec2_instance_type (default: "t4g.medium")
   # - ebs_volume_size (default: 50)
   # - ec2_key_pair_name (optional, for SSH)
   # - allowed_ssh_cidr (default: "0.0.0.0/0" - restrict for security)
   ```

4. Plan the deployment:
   ```bash
   terraform plan
   ```

5. Apply the configuration:
   ```bash
   terraform apply
   ```

   This will create:
   - EC2 instance (t4g.medium, Ubuntu 22.04 LTS ARM64)
   - EBS volume (50GB gp3)
   - Security group (SSH, HTTP, HTTPS)
   - Elastic IP (if enabled)

6. Note the outputs:
   ```bash
   terraform output
   ```
   
   Save these values:
   - `ec2_elastic_ip` or `ec2_instance_public_ip` - for SSH access
   - `ec2_ebs_volume_id` - for reference
   - `ec2_ssh_command` - ready-to-use SSH command

## Step 2: Connect to EC2 Instance

1. Wait for instance to be running (check AWS Console or use `terraform output`)

2. Connect via SSH:
   ```bash
   # Using key pair (if configured)
   ssh -i ~/.ssh/your-key.pem ubuntu@<elastic-ip-or-public-ip>
   
   # Or use the output command
   terraform output -raw ec2_ssh_command
   ```

   **Alternative**: Use AWS Systems Manager Session Manager (no SSH key needed):
   ```bash
   aws ssm start-session --target <instance-id>
   ```

## Step 3: Set Up EBS Volume

1. Clone the repository (or upload deployment files):
   ```bash
   # Option 1: Clone from git (temporary location)
   git clone <your-repo-url> ~/hostaway-messages
   cd ~/hostaway-messages
   
   # Option 2: Upload files via SCP
   # scp -r deployment/ ubuntu@<instance-ip>:~/deployment/
   ```

2. Make scripts executable:
   ```bash
   chmod +x deployment/*.sh
   ```

3. **IMPORTANT**: Run EBS volume setup first:
   ```bash
   sudo ./deployment/setup-ebs.sh
   ```

   This script will:
   - Detect the attached EBS volume
   - Format it with ext4 (if not already formatted)
   - Mount it to `/opt/hostaway-messages`
   - Add to `/etc/fstab` for automatic mounting on reboot
   - Create directory structure
   - Set correct permissions

   **Note**: If the volume is already formatted, it will skip formatting and just mount it.

4. Verify EBS volume is mounted:
   ```bash
   df -h /opt/hostaway-messages
   mountpoint /opt/hostaway-messages
   ```

## Step 4: Initial Server Setup

1. Move repository to EBS volume:
   ```bash
   sudo mv ~/hostaway-messages /opt/hostaway-messages/app
   cd /opt/hostaway-messages/app
   ```

2. Run the EC2 setup script:
   ```bash
   sudo ./deployment/ec2-setup.sh
   ```

   This script will:
   - Install PostgreSQL, Python, Nginx, and dependencies
   - Install AWS CLI and filesystem tools
   - Create application user (`hostaway`)
   - Move PostgreSQL data directory to EBS volume (via symlink)
   - Configure firewall (UFW)
   - Start and enable services

## Step 5: PostgreSQL Setup

1. Run PostgreSQL setup script:
   ```bash
   sudo -u postgres ./deployment/setup-postgres.sh
   ```

2. **IMPORTANT**: Save the database credentials displayed at the end. You'll need them for the `.env` file.

3. Verify PostgreSQL data is on EBS volume:
   ```bash
   ls -la /var/lib/postgresql
   # Should show a symlink to /opt/hostaway-messages/postgresql
   ```

## Step 6: Configure Environment Variables

1. Run the interactive setup script:
   ```bash
   sudo ./deployment/setup-env.sh
   ```

   This will prompt you for:
   - `DATABASE_URL` (use the credentials from Step 5)
   - `SECRET_KEY` (will be auto-generated if not provided)
   - `OPENAI_API_KEY` (required)
   - `HOSTAWAY_ACCOUNT_ID` (optional, for sync)
   - `HOSTAWAY_API_KEY` (optional, for sync)
   - `GOOGLE_CLIENT_ID` (optional, for OAuth)
   - `GOOGLE_CLIENT_SECRET` (optional, for OAuth)

2. Alternatively, manually create `/opt/hostaway-messages/.env`:
   ```bash
   sudo nano /opt/hostaway-messages/.env
   ```
   
   Use `deployment/env.production.example` as a template.

## Step 7: Deploy Application

1. Run the deployment script:
   ```bash
   sudo ./deployment/deploy.sh
   ```

   This script will:
   - Pull latest code from git
   - Create/update virtual environment
   - Install dependencies
   - Initialize database (automatic on first startup)

## Step 8: Install Systemd Service

1. Copy systemd service file:
   ```bash
   sudo cp /opt/hostaway-messages/app/deployment/hostaway-dashboard.service /etc/systemd/system/
   ```

2. Reload systemd and enable service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable hostaway-dashboard
   sudo systemctl start hostaway-dashboard
   ```

3. Check service status:
   ```bash
   sudo systemctl status hostaway-dashboard
   ```

4. View logs:
   ```bash
   sudo journalctl -u hostaway-dashboard -f
   ```

## Step 9: Configure Nginx (Optional, for HTTPS)

1. Copy Nginx configuration:
   ```bash
   sudo cp /opt/hostaway-messages/app/deployment/nginx-hostaway.conf /etc/nginx/sites-available/hostaway
   ```

2. Edit the configuration file:
   ```bash
   sudo nano /etc/nginx/sites-available/hostaway
   ```
   
   Replace `server_name _;` with your domain name.

3. Create symlink and remove default:
   ```bash
   sudo ln -s /etc/nginx/sites-available/hostaway /etc/nginx/sites-enabled/
   sudo rm /etc/nginx/sites-enabled/default
   ```

4. Test Nginx configuration:
   ```bash
   sudo nginx -t
   ```

5. Reload Nginx:
   ```bash
   sudo systemctl reload nginx
   ```

## Step 10: Set Up SSL Certificate (Optional, for HTTPS)

1. Install Certbot:
   ```bash
   sudo apt-get install certbot python3-certbot-nginx
   ```

2. Obtain SSL certificate:
   ```bash
   sudo certbot --nginx -d your-domain.com
   ```

3. Certbot will automatically configure Nginx and set up auto-renewal.

## Step 11: Update DNS

1. Update your domain's A record to point to the Elastic IP:
   ```
   Type: A
   Name: @ (or your subdomain)
   Value: <elastic-ip-address>
   TTL: 300 (or your preference)
   ```

2. Wait for DNS propagation (can take a few minutes to 48 hours).

## Step 12: Set Up Backups

1. Make backup script executable:
   ```bash
   sudo chmod +x /opt/hostaway-messages/app/deployment/backup-database.sh
   ```

2. Set up daily backup via cron:
   ```bash
   sudo crontab -e
   ```
   
   Add this line (runs daily at 2 AM):
   ```
   0 2 * * * /opt/hostaway-messages/app/deployment/backup-database.sh
   ```

3. **EBS Snapshots** (recommended for full backup):
   - Manual: Create snapshot in AWS Console
   - Automated: Use AWS Backup or Lifecycle Manager
   - Schedule: Daily or weekly snapshots

4. Set up log rotation:
   ```bash
   sudo cp /opt/hostaway-messages/app/deployment/logrotate-hostaway /etc/logrotate.d/hostaway
   ```

## Step 13: Verify Deployment

1. Check if service is running:
   ```bash
   sudo systemctl status hostaway-dashboard
   ```

2. Test health endpoint:
   ```bash
   curl http://127.0.0.1:5001/health
   ```

3. Access the application:
   - If using Nginx: `https://your-domain.com`
   - If not using Nginx: `http://<elastic-ip>:5001` (only if firewall allows)

## Updating the Application

To update the application after making changes:

1. SSH to the instance
2. Run the deployment script:
   ```bash
   cd /opt/hostaway-messages/app
   sudo ./deployment/deploy.sh
   ```

   Or deploy a specific branch:
   ```bash
   sudo ./deployment/deploy.sh develop
   ```

## EBS Volume Management

### Check Volume Status
```bash
# Check if mounted
mountpoint /opt/hostaway-messages
df -h /opt/hostaway-messages

# Check volume details
lsblk
sudo file -s /dev/nvme1n1  # or /dev/sdf on older instances
```

### Resize EBS Volume

1. **Increase volume size in Terraform**:
   ```hcl
   ebs_volume_size = 100  # Increase from 50 to 100
   ```

2. **Apply Terraform changes**:
   ```bash
   terraform apply
   ```

3. **Resize filesystem on instance**:
   ```bash
   # Grow the partition (if needed)
   sudo growpart /dev/nvme1n1 1  # or /dev/sdf 1
   
   # Resize filesystem
   sudo resize2fs /dev/nvme1n1  # or /dev/sdf
   
   # Verify
   df -h /opt/hostaway-messages
   ```

### Create EBS Snapshot

```bash
# Via AWS CLI
aws ec2 create-snapshot \
  --volume-id <volume-id> \
  --description "Backup before update $(date +%Y-%m-%d)"

# Or via AWS Console: EC2 > Volumes > Select volume > Actions > Create snapshot
```

## Troubleshooting

### Service Won't Start

1. Check service status:
   ```bash
   sudo systemctl status hostaway-dashboard
   ```

2. Check logs:
   ```bash
   sudo journalctl -u hostaway-dashboard -n 50
   ```

3. Check application logs:
   ```bash
   tail -f /opt/hostaway-messages/logs/dashboard.log
   ```

### EBS Volume Not Mounting

1. Check if volume is attached:
   ```bash
   lsblk
   ```

2. Check if device exists:
   ```bash
   ls -la /dev/nvme1n1  # or /dev/sdf
   ```

3. Check filesystem:
   ```bash
   sudo file -s /dev/nvme1n1
   ```

4. Manually mount:
   ```bash
   sudo mount /dev/nvme1n1 /opt/hostaway-messages
   ```

5. Check `/etc/fstab`:
   ```bash
   cat /etc/fstab | grep hostaway
   ```

### Database Connection Issues

1. Verify PostgreSQL is running:
   ```bash
   sudo systemctl status postgresql
   ```

2. Test database connection:
   ```bash
   sudo -u postgres psql -d hostaway_prod -U hostaway_user
   ```

3. Check `.env` file has correct `DATABASE_URL`:
   ```bash
   sudo cat /opt/hostaway-messages/.env | grep DATABASE_URL
   ```

4. Verify PostgreSQL data is on EBS:
   ```bash
   ls -la /var/lib/postgresql
   df -h /opt/hostaway-messages/postgresql
   ```

### Instance Reboot Issues

1. **EBS volume not mounting on reboot**:
   - Check `/etc/fstab` entry
   - Verify UUID is correct: `sudo blkid`
   - Check system logs: `sudo journalctl -b | grep mount`

2. **PostgreSQL not starting**:
   - Check if EBS volume is mounted first
   - Verify symlink exists: `ls -la /var/lib/postgresql`
   - Check PostgreSQL logs: `sudo journalctl -u postgresql`

### Port Already in Use

If port 5001 is already in use:

1. Find the process:
   ```bash
   sudo lsof -i :5001
   ```

2. Kill the process or change `FLASK_PORT` in `.env`

### Permission Issues

1. Ensure correct ownership:
   ```bash
   sudo chown -R hostaway:hostaway /opt/hostaway-messages
   ```

2. Ensure correct permissions on `.env`:
   ```bash
   sudo chmod 600 /opt/hostaway-messages/.env
   ```

3. Check EBS volume permissions:
   ```bash
   ls -la /opt/hostaway-messages
   ```

## Backup and Restore

### Manual Database Backup

```bash
sudo -u hostaway /opt/hostaway-messages/app/deployment/backup-database.sh
```

### Restore from Database Backup

1. Stop the service:
   ```bash
   sudo systemctl stop hostaway-dashboard
   ```

2. Restore database:
   ```bash
   gunzip < /opt/hostaway-messages/backups/hostaway_backup_YYYYMMDD_HHMMSS.sql.gz | \
   psql $DATABASE_URL
   ```

3. Start the service:
   ```bash
   sudo systemctl start hostaway-dashboard
   ```

### EBS Snapshot Restore

1. **Create new volume from snapshot** (via AWS Console or CLI):
   ```bash
   aws ec2 create-volume \
     --snapshot-id <snapshot-id> \
     --availability-zone <az> \
     --volume-type gp3
   ```

2. **Attach new volume to instance** (stop instance first if needed)

3. **Mount and verify data**

## Monitoring

### View Application Logs

```bash
# Systemd logs
sudo journalctl -u hostaway-dashboard -f

# Application logs
tail -f /opt/hostaway-messages/logs/dashboard.log

# Gunicorn logs
tail -f /opt/hostaway-messages/logs/gunicorn-*.log
```

### Check Disk Space

```bash
# Overall disk usage
df -h

# EBS volume usage
df -h /opt/hostaway-messages

# Detailed usage
du -sh /opt/hostaway-messages/*
```

### Check Service Health

```bash
# Check if service is running
sudo systemctl is-active hostaway-dashboard

# Check service status
sudo systemctl status hostaway-dashboard

# Check health endpoint
curl http://127.0.0.1:5001/health
```

### CloudWatch Monitoring (Optional)

1. Install CloudWatch agent:
   ```bash
   wget https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/arm64/latest/amazon-cloudwatch-agent.deb
   sudo dpkg -i -i ./amazon-cloudwatch-agent.deb
   ```

2. Configure and start agent (follow AWS documentation)

## Security Checklist

- [ ] Firewall (UFW) is enabled and configured
- [ ] SSH access is restricted to specific IPs (if possible)
- [ ] `.env` file has 600 permissions
- [ ] Application runs as non-root user (`hostaway`)
- [ ] PostgreSQL is not accessible from outside (localhost only)
- [ ] Flask app binds to 127.0.0.1 (not 0.0.0.0)
- [ ] SSL certificate is installed (if using HTTPS)
- [ ] Regular backups are configured (database + EBS snapshots)
- [ ] Log rotation is configured
- [ ] Strong `SECRET_KEY` is set
- [ ] Database password is strong
- [ ] Security groups only allow necessary ports
- [ ] EBS volume is encrypted

## Cost Optimization

- **Reserved Instances**: Save ~40% with 1-year commitment
- **EBS Snapshots**: Use lifecycle policies to delete old snapshots
- **Right-sizing**: Monitor usage and adjust instance type if needed
- **Data Transfer**: Minimize outbound data transfer
- **Stop Instance**: Stop instance when not in use (EBS volume persists)

## Comparison: EC2 vs Lightsail

| Feature | EC2 | Lightsail |
|---------|-----|-----------|
| **Cost (4GB RAM)** | ~$29-35/month | ~$24/month |
| **Flexibility** | High (many instance types) | Limited (fixed bundles) |
| **EBS Volumes** | Full control | Included in bundle |
| **Networking** | Full VPC control | Simplified |
| **Scaling** | Auto Scaling Groups | Manual |
| **Setup Complexity** | Higher | Lower |
| **Best For** | Production, scaling needs | Simple deployments |

## Next Steps

1. Set up monitoring and alerting (CloudWatch)
2. Configure automated EBS snapshots
3. Set up CI/CD pipeline for automated deployments
4. Configure additional security hardening
5. Set up log aggregation (optional)
6. Consider Reserved Instances for cost savings

## Support

For issues or questions:
1. Check logs first: `sudo journalctl -u hostaway-dashboard -n 100`
2. Review this deployment guide
3. Check application logs in `/opt/hostaway-messages/logs/`
4. Verify environment variables in `/opt/hostaway-messages/.env`
5. Check EBS volume status: `df -h /opt/hostaway-messages`



