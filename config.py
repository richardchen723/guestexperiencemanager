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
# Validation is done lazily when HostawayAPIClient is instantiated
# This allows the dashboard to start without credentials (only needed for sync operations)
HOSTAWAY_ACCOUNT_ID = os.getenv("HOSTAWAY_ACCOUNT_ID")
HOSTAWAY_API_KEY = os.getenv("HOSTAWAY_API_KEY")
HOSTAWAY_BASE_URL = os.getenv("HOSTAWAY_BASE_URL", "https://api.hostaway.com/v1")

# Database Configuration
# Supports both SQLite (file path) and PostgreSQL (connection URL)
# For PostgreSQL: postgresql://user:password@host:port/database
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL connection string
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/database/hostaway.db")  # SQLite fallback

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
# Batch size for database commits (number of conversations to process before committing)
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))

# AWS S3 Storage Configuration
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")
AWS_S3_REGION = os.getenv("AWS_S3_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")  # Optional, for local testing
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")  # Optional, for local testing
USE_S3_STORAGE = bool(AWS_S3_BUCKET_NAME)  # Enable S3 if bucket name is set