# Deployment Guide

This guide covers deploying the Hostaway Messages Dashboard to production.

## Prerequisites

- Python 3.8 or higher
- pip package manager
- Access to Hostaway API credentials
- OpenAI API key (for AI features)
- Google OAuth credentials (optional, for user authentication)

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

## Security Checklist

- [ ] All API keys are set in `.env` file (never commit `.env` to git)
- [ ] `SECRET_KEY` is a strong random string (32+ characters)
- [ ] `FLASK_DEBUG=False` in production
- [ ] `.env` file is in `.gitignore` (verify it's not tracked)
- [ ] Database files are in `.gitignore`
- [ ] Log files are in `.gitignore`
- [ ] Google OAuth redirect URI matches your deployment URL

## Database Initialization

The database will be automatically created on first run. Ensure the database directory exists:

```bash
mkdir -p data/database
```

## Running the Application

### Development Mode

```bash
cd dashboard
python3 app.py
```

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
- Check database directory permissions
- Review logs in `logs/dashboard.log`

### OAuth not working
- Verify `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set
- Check redirect URI matches Google Cloud Console configuration
- Ensure `FLASK_HOST` and `FLASK_PORT` match your deployment URL

### Database errors
- Check database file permissions
- Ensure database directory exists
- Verify SQLite is installed

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

