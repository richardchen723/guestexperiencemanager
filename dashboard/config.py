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
# Supports both PostgreSQL (via DATABASE_URL) and SQLite (fallback)
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL connection string (shared for all databases)

# Database paths (relative to project root) - used only for SQLite fallback
MAIN_DATABASE_PATH = str(PROJECT_ROOT / "data" / "database" / "hostaway.db")
CACHE_DATABASE_PATH = str(PROJECT_ROOT / "dashboard" / "data" / "ai_cache.db")
USERS_DATABASE_PATH = str(PROJECT_ROOT / "dashboard" / "data" / "users.db")
CONVERSATIONS_DIR = str(PROJECT_ROOT / "conversations")

# AWS S3 Storage Configuration
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")
AWS_S3_REGION = os.getenv("AWS_S3_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")  # Optional, for local testing
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")  # Optional, for local testing
USE_S3_STORAGE = bool(AWS_S3_BUCKET_NAME)  # Enable S3 if bucket name is set

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
