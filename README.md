# Hostaway Data Sync System

A production-ready data synchronization system for Hostaway that stores listings, reservations, guests, and messages in a local SQLite database for analysis and AI training.

## Features

- **Full Data Sync**: Syncs listings, reservations, guests, and messages from Hostaway API
- **Incremental Updates**: Daily incremental syncs to keep data current
- **Progress Dashboard**: Real-time progress tracking with visual indicators
- **Idempotent Operations**: Safe to run multiple times without duplicating data
- **Error Handling**: Robust error handling with logging and recovery
- **Database Storage**: SQLite database for structured queries
- **Message Files**: Conversation files stored in organized folder structure

## Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd hostaway-messages
   ```

2. **Install dependencies**:
   ```bash
   pip3 install -r requirements.txt
   ```

3. **Configure API credentials**:
   
   Edit `config.py` or set environment variables:
   ```bash
   export HOSTAWAY_ACCOUNT_ID="your_account_id"
   export HOSTAWAY_API_KEY="your_api_key"
   ```

   Get your credentials from: https://dashboard.hostaway.com/settings/api

## Usage

### Full Sync

Sync all data from Hostaway:

```bash
python3 -m sync.sync_manager --full
```

### Incremental Sync

Sync only changed data:

```bash
python3 -m sync.sync_manager --incremental
```

### Auto-Detect

Let the system decide based on last sync time:

```bash
python3 -m sync.sync_manager
```

## Configuration

Edit `config.py` or set environment variables:

### Required
- `HOSTAWAY_ACCOUNT_ID`: Your Hostaway account ID
- `HOSTAWAY_API_KEY`: Your Hostaway API key

### Database (PostgreSQL or SQLite)
- `DATABASE_URL`: PostgreSQL connection string (optional, format: `postgresql://user:password@host:port/database`)
- `DATABASE_PATH`: Path to SQLite database (default: `data/database/hostaway.db`, used if `DATABASE_URL` not set)

### AWS S3 Storage (Optional)
- `AWS_S3_BUCKET_NAME`: S3 bucket name for file storage (optional, falls back to local filesystem if not set)
- `AWS_S3_REGION`: AWS region for S3 bucket (default: `us-east-1`)
- `AWS_ACCESS_KEY_ID`: AWS access key (optional, uses IAM role in production)
- `AWS_SECRET_ACCESS_KEY`: AWS secret key (optional, uses IAM role in production)

### Sync Configuration
- `STORE_PHOTO_METADATA`: Store photo URLs/metadata (default: `True`)
- `SYNC_FULL_ON_START`: Perform full sync on first run (default: `True`)
- `SYNC_INCREMENTAL_DAILY`: Enable daily incremental sync (default: `True`)
- `SYNC_INTERVAL_HOURS`: Hours between incremental syncs (default: `24`)

### Storage Modes

The application supports two storage modes:

1. **Local Mode** (default): Uses SQLite database and local filesystem
   - No `DATABASE_URL` or `AWS_S3_BUCKET_NAME` set
   - Suitable for local development and testing

2. **AWS Mode** (production): Uses PostgreSQL (RDS) and S3 storage
   - Set `DATABASE_URL` for PostgreSQL connection
   - Set `AWS_S3_BUCKET_NAME` for S3 file storage
   - See `DEPLOYMENT.md` for AWS infrastructure setup

### Additional Configuration
- `VERBOSE`: Show detailed progress (default: `True`)

## Project Structure

```
hostaway-messages/
├── config.py                 # Configuration file
├── database/                 # Database schema and models
│   ├── __init__.py
│   ├── models.py            # SQLAlchemy ORM models
│   └── schema.py            # Database schema definitions
├── sync/                     # Sync modules
│   ├── __init__.py
│   ├── api_client.py        # Hostaway API client
│   ├── progress_tracker.py  # Progress dashboard
│   ├── sync_listings.py     # Listings sync
│   ├── sync_reservations.py # Reservations sync
│   ├── sync_guests.py       # Guest deduplication
│   ├── sync_messages.py     # Messages sync
│   └── sync_manager.py      # Main sync orchestrator
├── utils/                    # Utility modules
│   └── logging_config.py    # Logging configuration
├── data/                     # Data storage
│   ├── database/            # SQLite database
│   ├── photos/              # Photo metadata
│   └── exports/             # Export directory
├── conversations/            # Conversation text files
└── requirements.txt         # Python dependencies
```

## Database Schema

The system uses SQLite with the following main tables:

- **listings**: Property listing information
- **listing_photos**: Photo URLs and metadata
- **guests**: Guest information (deduplicated)
- **reservations**: Reservation details
- **conversations**: Conversation metadata
- **messages_metadata**: Message indexing for search
- **sync_logs**: Sync operation history

## Progress Dashboard

The sync process shows real-time progress:

```
[Syncing Listings] | "Blue Haven" Iconic Lakefront... | [████████░░░░░░░░░░░░] | 10/37 (27.0%) | ✓2 | ↻8 | ✗0 | 5.3s
```

- Current item being processed
- Progress bar
- Count and percentage
- Created (✓), Updated (↻), Errors (✗)
- Elapsed time

## Querying Data

Example queries using SQLAlchemy:

```python
from database.models import get_session, Listing, Reservation, Guest

session = get_session("data/database/hostaway.db")

# Get all active listings
listings = session.query(Listing).filter(Listing.status == 'active').all()

# Get reservations for a listing
reservations = session.query(Reservation).filter(
    Reservation.listing_id == 12345
).all()

# Get guest by email
guest = session.query(Guest).filter(Guest.email == 'guest@example.com').first()
```

## Error Handling

- Automatic retry on rate limiting
- Batch commits to prevent database locking
- Comprehensive error logging
- Graceful degradation on failures

## Logging

Logs are written to console by default. To log to file:

```python
from utils.logging_config import setup_logging
setup_logging(log_file="logs/sync.log")
```

## Notes

- **Photos**: Only URLs and metadata are stored, not downloaded images
- **Current State**: Only current state is stored, not historical changes
- **Idempotent**: Safe to run multiple times
- **Database**: Uses SQLite with WAL mode for better concurrency

## Troubleshooting

### Database Locked

If you get a "database is locked" error:
1. Check if another sync process is running
2. Close any database browsers
3. Remove stale lock files: `rm -f data/database/*.db-*`

### API Rate Limiting

The system automatically handles rate limiting with retries. If you see many rate limit errors, consider:
- Reducing sync frequency
- Contacting Hostaway support for higher rate limits

### Missing Data

If some data is missing:
1. Check sync logs in the `sync_logs` table
2. Verify API credentials in `config.py`
3. Run a full sync to ensure all data is fetched

## License

[Add your license here]

## Support

For issues or questions, please [create an issue](link-to-issues) or contact [your contact info].