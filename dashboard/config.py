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

# Database paths/URLs
# Supports both SQLite (file path) and PostgreSQL (connection URL)
# For PostgreSQL: postgresql://user:password@host:port/database
# Falls back to SQLite file paths if environment variables not set
MAIN_DATABASE_PATH = os.getenv("DATABASE_URL") or str(PROJECT_ROOT / "data" / "database" / "hostaway.db")
CACHE_DATABASE_PATH = os.getenv("CACHE_DATABASE_URL") or str(PROJECT_ROOT / "dashboard" / "data" / "ai_cache.db")
USERS_DATABASE_PATH = os.getenv("USERS_DATABASE_URL") or str(PROJECT_ROOT / "dashboard" / "data" / "users.db")
CONVERSATIONS_DIR = str(PROJECT_ROOT / "conversations")

# Analysis time windows
REVIEW_MONTHS = 6  # Analyze reviews from last 6 months
MESSAGE_MONTHS = 2  # Analyze messages from last 2 months

# OpenAI Model
OPENAI_MODEL = "gpt-3.5-turbo"  # Use cheaper model for cost optimization

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
