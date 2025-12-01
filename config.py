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
# PostgreSQL is required (no SQLite fallback in production)
# For local dev: postgresql://user@localhost:5432/hostaway_dev
# For production: postgresql://user@localhost:5432/hostaway_prod
DATABASE_URL = os.getenv("DATABASE_URL")  # Required - PostgreSQL connection string
if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL environment variable is required. "
        "PostgreSQL is required for this application. "
        "Example: postgresql://user@localhost:5432/hostaway_dev"
    )
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/database/hostaway.db")  # Kept for backward compatibility, not used

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

# Storage Configuration
# S3 storage is no longer used - all files stored locally in conversations/ directory
# These variables are kept for backward compatibility but are ignored
AWS_S3_BUCKET_NAME = None  # Explicitly disabled
AWS_S3_REGION = os.getenv("AWS_S3_REGION", "us-east-1")  # Kept for compatibility, not used
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")  # Kept for compatibility, not used
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")  # Kept for compatibility, not used
USE_S3_STORAGE = False  # Always use local filesystem