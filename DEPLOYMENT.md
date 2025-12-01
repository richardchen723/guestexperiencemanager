# Deployment Guide

This guide covers deploying the Hostaway Messages Dashboard to production.

## Prerequisites

- Python 3.8 or higher
- pip package manager
- Access to Hostaway API credentials
- OpenAI API key (for AI features)
- Google OAuth credentials (optional, for user authentication)
- PostgreSQL installed locally or on deployment server
- For Lightsail deployment: Ubuntu 22.04 LTS instance

## Environment Setup

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd hostaway-messages
   ```

2. **Create a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -r dashboard/requirements.txt
   ```

4. **Create `.env` file:**
   ```bash
   cp .env.example .env
   ```

5. **Configure environment variables:**
   Edit `.env` and set all required values:
   - `HOSTAWAY_ACCOUNT_ID` - Your Hostaway account ID
   - `HOSTAWAY_API_KEY` - Your Hostaway API key
   - `OPENAI_API_KEY` - Your OpenAI API key
   - `SECRET_KEY` - Generate with: `python3 -c "import secrets; print(secrets.token_hex(32))"`
   - `FLASK_DEBUG=False` - **MUST be False in production**
   - `FLASK_HOST` - Set to `0.0.0.0` for external access, or `127.0.0.1` for local only
   - `FLASK_PORT` - Port number (default: 5001)
   - `DATABASE_URL` - PostgreSQL connection string (required)
     - Format: `postgresql://user:password@host:port/database`
     - Example (local): `postgresql://username@localhost:5432/hostaway_dev`
     - Example (production): `postgresql://username@localhost:5432/hostaway_prod`
   - Note: All files are stored locally in `conversations/` directory (S3 is no longer used)

## Security Checklist

- [ ] All API keys are set in `.env` file (never commit `.env` to git)
- [ ] `SECRET_KEY` is a strong random string (32+ characters)
- [ ] `FLASK_DEBUG=False` in production
- [ ] `.env` file is in `.gitignore` (verify it's not tracked)
- [ ] Database files are in `.gitignore`
- [ ] Log files are in `.gitignore`
- [ ] Google OAuth redirect URI matches your deployment URL

## Database Setup

### Local PostgreSQL Installation

**On macOS (using Homebrew):**
```bash
brew install postgresql@15
brew services start postgresql@15
createdb hostaway_dev
```

**On Linux (Ubuntu/Debian):**
```bash
sudo apt-get update
sudo apt-get install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql
sudo -u postgres createdb hostaway_dev
```

**On Lightsail (Ubuntu):**
See the comprehensive [Lightsail Deployment Guide](deployment/LIGHTSAIL_DEPLOYMENT.md) for detailed instructions.

Quick setup:
```bash
# Clone repository and run setup scripts
sudo ./deployment/lightsail-setup.sh
sudo -u postgres ./deployment/setup-postgres.sh
sudo ./deployment/setup-env.sh
sudo ./deployment/deploy.sh
```

### Database Initialization

The application will automatically create all required schemas and tables on first run:
- `public` schema: Main application data (listings, reservations, guests, etc.)
- `users` schema: User authentication data
- `tickets` schema: Ticket management data
- `cache` schema: AI analysis cache

## AWS Infrastructure Setup (DEPRECATED)

### Option 1: Using Terraform (DEPRECATED - No longer used)

1. **Navigate to terraform directory:**
   ```bash
   cd terraform
   ```

2. **Copy and configure variables:**
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   # Edit terraform.tfvars with your configuration
   ```

3. **Initialize Terraform:**
   ```bash
   terraform init
   ```

4. **Review the plan:**
   ```bash
   terraform plan
   ```

5. **Apply the configuration:**
   ```bash
   terraform apply
   ```

6. **Get database credentials:**
   ```bash
   # Get secret ARN
   terraform output database_secret_arn
   
   # Retrieve credentials
   aws secretsmanager get-secret-value --secret-id <secret-arn> --query SecretString --output text | jq
   ```

7. **Construct DATABASE_URL:**
   ```
   postgresql://<username>:<password>@<rds_endpoint>:<rds_port>/<database_name>
   ```

8. **Get S3 bucket name:**
   ```bash
   terraform output s3_bucket_name
   ```

See `terraform/README.md` for detailed instructions and cost optimization notes.

### Option 2: Manual AWS Setup

If not using Terraform, manually create:
- RDS PostgreSQL instance (db.t3.micro recommended for cost)
- S3 bucket with lifecycle policies
- IAM roles/policies for application access

## Database Initialization

### PostgreSQL (Production)

If `DATABASE_URL` is set, the application will use PostgreSQL. The database and tables will be automatically created on first run.

### SQLite (Local Development)

If `DATABASE_URL` is not set, the application falls back to SQLite. Ensure the database directory exists:

```bash
mkdir -p data/database
```

## Database Migration

To migrate existing SQLite data to PostgreSQL:

1. **Run migration script (dry-run first):**
   ```bash
   python3 scripts/migrate_to_postgresql.py \
     --sqlite-db data/database/hostaway.db \
     --postgres-url postgresql://user:password@host:port/database \
     --dry-run
   ```

2. **Execute migration:**
   ```bash
   python3 scripts/migrate_to_postgresql.py \
     --sqlite-db data/database/hostaway.db \
     --postgres-url postgresql://user:password@host:port/database
   ```

The script will:
- Create all tables in PostgreSQL
- Migrate data from SQLite to PostgreSQL
- Preserve all relationships and foreign keys
- Show progress and summary statistics

## Running the Application

### Local Development

1. **Create `.env` file** in project root:
   ```bash
   cp deployment/env.production.example .env
   # Edit .env with your local settings
   ```

2. **Set up local PostgreSQL** (see Database Setup section above)

3. **Run the application**:
   ```bash
   cd dashboard
   python3 app.py
   ```

   The application will be available at `http://localhost:5001`

### Production Mode (using Gunicorn)

1. **Install Gunicorn:**
   ```bash
   pip install gunicorn
   ```

2. **Run with Gunicorn:**
   ```bash
   cd dashboard
   gunicorn -w 4 -b 0.0.0.0:5001 --timeout 120 app:create_app()
   ```

   Or create a `gunicorn_config.py`:
   ```python
   bind = "0.0.0.0:5001"
   workers = 4
   timeout = 120
   worker_class = "sync"
   ```

   Then run:
   ```bash
   gunicorn -c gunicorn_config.py app:create_app()
   ```

### Production Mode (using systemd)

Create `/etc/systemd/system/hostaway-dashboard.service`:

```ini
[Unit]
Description=Hostaway Messages Dashboard
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/hostaway-messages/dashboard
Environment="PATH=/path/to/venv/bin"
ExecStart=/path/to/venv/bin/gunicorn -w 4 -b 127.0.0.1:5001 --timeout 120 app:create_app()
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable hostaway-dashboard
sudo systemctl start hostaway-dashboard
```

## Reverse Proxy (Nginx)

Example Nginx configuration:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
```

For HTTPS, add SSL configuration and redirect HTTP to HTTPS.

## Monitoring

- Logs are written to `logs/dashboard.log`
- Monitor disk space for database and log files
- Set up log rotation to prevent disk fill-up

## Backup

Regularly backup:
- `data/database/hostaway.db` - Main database
- `dashboard/data/users.db` - User database
- `dashboard/data/ai_cache.db` - AI cache database
- `.env` file (securely, with encryption)

## Troubleshooting

### Application won't start
- Check all environment variables are set
- Verify `SECRET_KEY` is set in production
- Check database directory permissions (for SQLite)
- Review logs in `logs/dashboard.log`

### Database connection issues
- **PostgreSQL**: Verify `DATABASE_URL` is correct and database is accessible
- **SQLite**: Check database file permissions and ensure directory exists
- Check network connectivity to RDS (security groups, VPC configuration)
- Verify database credentials from AWS Secrets Manager

### S3 storage issues
- Verify `AWS_S3_BUCKET_NAME` is set correctly
- Check AWS credentials (access key/secret or IAM role)
- Verify S3 bucket exists and is accessible
- Check IAM permissions for S3 access
- Review S3 bucket policies and CORS configuration

### OAuth not working
- Verify `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set
- Check redirect URI matches Google Cloud Console configuration
- Ensure `FLASK_HOST` and `FLASK_PORT` match your deployment URL

### Migration issues
- Run migration with `--dry-run` first to identify issues
- Ensure PostgreSQL database exists and is accessible
- Check that all required tables can be created
- Verify foreign key constraints are satisfied

## Performance Tuning

- Adjust `MESSAGE_SYNC_PARALLEL_WORKERS` based on API rate limits
- Set appropriate Gunicorn worker count (typically 2-4x CPU cores)
- Monitor database size and consider archiving old data
- Enable database connection pooling if using multiple workers

## Updates

1. Pull latest code
2. Activate virtual environment
3. Update dependencies: `pip install -r requirements.txt -r dashboard/requirements.txt`
4. Run database migrations if any
5. Restart the application

