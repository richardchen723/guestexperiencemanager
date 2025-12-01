# AWS Lightsail Deployment Guide

This guide provides step-by-step instructions for deploying the Hostaway Messages application to AWS Lightsail.

## Prerequisites

- AWS account with Lightsail access
- Domain name (optional, for HTTPS)
- Hostaway API credentials (for sync operations)
- OpenAI API key (for AI features)
- Google OAuth credentials (optional, for authentication)

## Architecture Overview

The deployment uses:
- **Ubuntu 22.04 LTS** on Lightsail instance
- **PostgreSQL 15** installed locally on the instance
- **Python 3.10+** with virtual environment
- **Gunicorn** as WSGI server
- **systemd** for process management
- **Nginx** as reverse proxy (optional, for HTTPS)
- **Local filesystem** for conversation file storage

## Step 1: Create Lightsail Instance

1. Log in to AWS Lightsail console
2. Click "Create instance"
3. Choose:
   - **Platform**: Linux/Unix
   - **Blueprint**: Ubuntu 22.04 LTS
   - **Instance plan**: Minimum 2GB RAM, 1 vCPU (recommended: 4GB RAM, 2 vCPU)
4. Name your instance (e.g., `hostaway-messages`)
5. Click "Create instance"

## Step 2: Connect to Instance

1. Wait for instance to be running
2. Click on your instance
3. Click "Connect using SSH" or use SSH from your terminal:
   ```bash
   ssh ubuntu@<your-instance-ip>
   ```

## Step 3: Initial Server Setup

1. Update system packages:
   ```bash
   sudo apt-get update
   sudo apt-get upgrade -y
   ```

2. Clone the repository (or upload deployment files):
   ```bash
   # Option 1: Clone from git
   sudo git clone <your-repo-url> /opt/hostaway-messages/app
   
   # Option 2: Upload files via SCP
   # scp -r deployment/ ubuntu@<instance-ip>:~/deployment/
   ```

3. Run the initial setup script:
   ```bash
   cd /opt/hostaway-messages/app
   sudo chmod +x deployment/*.sh
   sudo ./deployment/lightsail-setup.sh
   ```

   This script will:
   - Install PostgreSQL, Python, Nginx, and dependencies
   - Create application user (`hostaway`)
   - Create directory structure
   - Configure firewall

## Step 4: PostgreSQL Setup

1. Run PostgreSQL setup script:
   ```bash
   sudo -u postgres ./deployment/setup-postgres.sh
   ```

2. **IMPORTANT**: Save the database credentials displayed at the end. You'll need them for the `.env` file.

## Step 5: Configure Environment Variables

1. Run the interactive setup script:
   ```bash
   sudo ./deployment/setup-env.sh
   ```

   This will prompt you for:
   - `DATABASE_URL` (use the credentials from Step 4)
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

## Step 6: Deploy Application

1. Run the deployment script:
   ```bash
   sudo ./deployment/deploy.sh
   ```

   This script will:
   - Pull latest code from git
   - Create/update virtual environment
   - Install dependencies
   - Initialize database (automatic on first startup)

## Step 7: Install Systemd Service

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

## Step 8: Configure Nginx (Optional, for HTTPS)

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

## Step 9: Set Up SSL Certificate (Optional, for HTTPS)

1. Install Certbot:
   ```bash
   sudo apt-get install certbot python3-certbot-nginx
   ```

2. Obtain SSL certificate:
   ```bash
   sudo certbot --nginx -d your-domain.com
   ```

3. Certbot will automatically configure Nginx and set up auto-renewal.

## Step 10: Set Up Backups

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

3. Set up log rotation:
   ```bash
   sudo cp /opt/hostaway-messages/app/deployment/logrotate-hostaway /etc/logrotate.d/hostaway
   ```

## Step 11: Verify Deployment

1. Check if service is running:
   ```bash
   sudo systemctl status hostaway-dashboard
   ```

2. Test health endpoint (if implemented):
   ```bash
   curl http://127.0.0.1:5001/health
   ```

3. Access the application:
   - If using Nginx: `https://your-domain.com`
   - If not using Nginx: `http://<instance-ip>:5001` (only if firewall allows)

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

## Backup and Restore

### Manual Backup

```bash
sudo -u hostaway /opt/hostaway-messages/app/deployment/backup-database.sh
```

### Restore from Backup

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
df -h
du -sh /opt/hostaway-messages/*
```

### Check Service Health

```bash
# Check if service is running
sudo systemctl is-active hostaway-dashboard

# Check service status
sudo systemctl status hostaway-dashboard
```

## Security Checklist

- [ ] Firewall (UFW) is enabled and configured
- [ ] `.env` file has 600 permissions
- [ ] Application runs as non-root user (`hostaway`)
- [ ] PostgreSQL is not accessible from outside (localhost only)
- [ ] Flask app binds to 127.0.0.1 (not 0.0.0.0)
- [ ] SSL certificate is installed (if using HTTPS)
- [ ] Regular backups are configured
- [ ] Log rotation is configured
- [ ] Strong `SECRET_KEY` is set
- [ ] Database password is strong

## Cost Optimization

- **Instance Size**: Start with 2GB RAM, 1 vCPU (~$10/month)
- **Storage**: Use instance storage (included) for conversations
- **Backups**: Store backups on instance initially
- **Monitoring**: Use CloudWatch (included with Lightsail)

## Next Steps

1. Set up monitoring and alerting
2. Configure automated backups to S3 (optional)
3. Set up CI/CD pipeline for automated deployments
4. Configure additional security hardening
5. Set up log aggregation (optional)

## Support

For issues or questions:
1. Check logs first: `sudo journalctl -u hostaway-dashboard -n 100`
2. Review this deployment guide
3. Check application logs in `/opt/hostaway-messages/logs/`
4. Verify environment variables in `/opt/hostaway-messages/.env`

