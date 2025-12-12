#!/usr/bin/env python3
"""
Dashboard configuration.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
# Look for .env in project root (parent of dashboard directory)
PROJECT_ROOT = Path(__file__).parent.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    # Also try loading from current directory as fallback
    load_dotenv()

# OpenAI API Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is required. Please set it in .env file or export it.")

# Database Configuration
# PostgreSQL is required (no SQLite fallback)
# For local dev: postgresql://user@localhost:5432/hostaway_dev
# For production: postgresql://user@localhost:5432/hostaway_prod
DATABASE_URL = os.getenv("DATABASE_URL")  # Required - PostgreSQL connection string (shared for all databases)
if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL environment variable is required. "
        "PostgreSQL is required for this application. "
        "Example: postgresql://user@localhost:5432/hostaway_dev"
    )

# Database paths (relative to project root) - kept for backward compatibility, not used with PostgreSQL
MAIN_DATABASE_PATH = str(PROJECT_ROOT / "data" / "database" / "hostaway.db")
CACHE_DATABASE_PATH = str(PROJECT_ROOT / "dashboard" / "data" / "ai_cache.db")
USERS_DATABASE_PATH = str(PROJECT_ROOT / "dashboard" / "data" / "users.db")
CONVERSATIONS_DIR = str(PROJECT_ROOT / "conversations")

# Storage Configuration
# All files stored on local filesystem (no S3)
# S3 storage is no longer used - all files stored locally in conversations/ directory
USE_S3_STORAGE = False  # Always use local filesystem

# Analysis time windows
REVIEW_MONTHS = 3  # Analyze reviews from last 3 months
MESSAGE_MONTHS = 2  # Analyze messages from last 2 months

# OpenAI Model
OPENAI_MODEL = "gpt-4o-mini"  # Cost-effective model with 128K token context window

# Flask Configuration
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))  # Changed to 5001 to avoid macOS AirPlay conflict
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "False").lower() == "true"
# SECRET_KEY: Must be set in production for security
# Generate a secure random key: python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if FLASK_DEBUG:
        # Only allow auto-generation in debug mode
        SECRET_KEY = os.urandom(32).hex()
    else:
        raise ValueError(
            "SECRET_KEY environment variable is required in production. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET not set. Google OAuth will not work.")

# Owner Configuration
OWNER_EMAIL = "yunhang.chen@gmail.com"

# Ticket Image Storage
TICKET_IMAGES_DIR = str(PROJECT_ROOT / "data" / "tickets")

# Twilio WhatsApp Configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")  # e.g., "whatsapp:+14155238886"
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5001")  # Base URL for ticket links

# Debug Log Configuration
def get_debug_log_path():
    """
    Get the appropriate debug log path based on the environment.
    Returns a writable path for debug logs, creating the directory if needed.
    """
    # Check if we're on EC2 (production environment)
    ec2_logs_dir = Path("/opt/hostaway-messages/logs")
    if ec2_logs_dir.exists() and ec2_logs_dir.is_dir():
        # On EC2, use the logs directory
        debug_log_path = ec2_logs_dir / "debug.log"
        # Ensure directory exists and is writable
        ec2_logs_dir.mkdir(parents=True, exist_ok=True)
        return str(debug_log_path)
    else:
        # Local development - use .cursor directory in project root
        debug_log_dir = PROJECT_ROOT / ".cursor"
        debug_log_dir.mkdir(parents=True, exist_ok=True)
        return str(debug_log_dir / "debug.log")

DEBUG_LOG_PATH = get_debug_log_path()
