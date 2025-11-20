"""
Hostaway API Configuration

Get your API credentials from: https://dashboard.hostaway.com/settings/api

For production use, set these as environment variables:
- HOSTAWAY_ACCOUNT_ID
- HOSTAWAY_API_KEY
"""

import os

# API Configuration
# MUST be set via environment variables in production
HOSTAWAY_ACCOUNT_ID = os.getenv("HOSTAWAY_ACCOUNT_ID")
HOSTAWAY_API_KEY = os.getenv("HOSTAWAY_API_KEY")
HOSTAWAY_BASE_URL = os.getenv("HOSTAWAY_BASE_URL", "https://api.hostaway.com/v1")

# Validate required API credentials
if not HOSTAWAY_ACCOUNT_ID or not HOSTAWAY_API_KEY:
    raise ValueError(
        "HOSTAWAY_ACCOUNT_ID and HOSTAWAY_API_KEY environment variables are required. "
        "Please set them in .env file or export them. "
        "Get your credentials from: https://dashboard.hostaway.com/settings/api"
    )

# Database Configuration
# Supports both SQLite (file path) and PostgreSQL (connection URL)
# For PostgreSQL: postgresql://user:password@host:port/database
DATABASE_PATH = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PATH", "data/database/hostaway.db")

# Photo Storage Configuration
# Note: We store photo URLs and metadata only, not download images
STORE_PHOTO_METADATA = os.getenv("STORE_PHOTO_METADATA", "True").lower() == "true"

# Sync Configuration
SYNC_FULL_ON_START = os.getenv("SYNC_FULL_ON_START", "True").lower() == "true"
SYNC_INCREMENTAL_DAILY = os.getenv("SYNC_INCREMENTAL_DAILY", "True").lower() == "true"
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "24"))

# Optional: Set to True to download attachments
DOWNLOAD_ATTACHMENTS = os.getenv("DOWNLOAD_ATTACHMENTS", "False").lower() == "true"

# Optional: Set to True to show detailed progress
VERBOSE = os.getenv("VERBOSE", "True").lower() == "true"

# Message Sync Optimization Configuration
MESSAGE_SYNC_PARALLEL_WORKERS = int(os.getenv("MESSAGE_SYNC_PARALLEL_WORKERS", "10"))