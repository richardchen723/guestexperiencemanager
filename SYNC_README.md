# Hostaway Data Sync System

This system synchronizes data from the Hostaway API into a local SQLite database for analysis and AI training.

## Overview

The sync system consists of:
- **Database**: SQLite database storing listings, reservations, guests, conversations, messages, and photos metadata
- **Sync Scripts**: Individual sync modules for each data type
- **Sync Manager**: Orchestrates all sync operations

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Initialize Database

The database will be automatically created on first sync, but you can initialize it manually:

```bash
python -m database.schema
```

This creates the database at `data/database/hostaway.db`

### 3. Run Full Sync

To perform a full sync of all data:

```bash
python -m sync.sync_manager --full
```

Or use the Python API:

```python
from sync import full_sync
results = full_sync()
```

### 4. Run Incremental Sync

For daily incremental updates:

```bash
python -m sync.sync_manager --incremental
```

Or:

```python
from sync import incremental_sync
results = incremental_sync()
```

## Sync Modules

### sync_listings.py
- Fetches all listings from Hostaway API
- Stores listing data in database
- Stores photo URLs and metadata (photos are NOT downloaded)

### sync_reservations.py
- Fetches reservations for all listings
- Extracts guest information from reservations
- Links reservations to listings and guests

### sync_guests.py
- Deduplicates guests by email and external account ID
- Consolidates guest information across reservations

### sync_messages.py
- Reads existing conversation files from `conversations/` directory
- Populates message metadata in database
- Links messages to conversations, reservations, and listings

### sync_manager.py
- Orchestrates all sync operations
- Handles full vs incremental sync logic
- Tracks sync status and errors

## Database Schema

The database includes the following tables:

- **listings**: Property listing information
- **listing_photos**: Photo URLs and metadata (not downloaded)
- **guests**: Guest information (deduplicated)
- **reservations**: Reservation details linked to listings and guests
- **conversations**: Conversation metadata linked to reservations
- **messages_metadata**: Message metadata for search and indexing
- **sync_logs**: Sync operation history

## Configuration

Edit `config.py` to configure:

- `DATABASE_PATH`: Path to SQLite database
- `STORE_PHOTO_METADATA`: Whether to store photo metadata (default: True)
- `SYNC_FULL_ON_START`: Perform full sync on first run (default: True)
- `SYNC_INCREMENTAL_DAILY`: Enable daily incremental sync (default: True)
- `SYNC_INTERVAL_HOURS`: Hours between incremental syncs (default: 24)

## Usage Examples

### Sync Only Listings

```python
from sync.sync_listings import sync_listings
result = sync_listings(full_sync=True)
```

### Sync Only Reservations

```python
from sync.sync_reservations import sync_reservations
result = sync_reservations(full_sync=True)
```

### Query Database

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

## File System Structure

```
hostaway-messages/
├── data/
│   ├── database/
│   │   └── hostaway.db          # SQLite database
│   ├── photos/
│   │   └── listings/             # Photo metadata (not downloaded)
│   └── exports/
│       └── training_data/        # For future AI exports
├── conversations/                # Existing conversation files
│   └── {listing_name}/
│       └── {guest_name}_{date}_conversation.txt
├── database/                     # Database schema and models
├── sync/                         # Sync scripts
└── config.py                     # Configuration
```

## Notes

- **Photos**: Photos are NOT downloaded. Only URLs and metadata are stored in the database.
- **Current State Only**: The system stores only the current state of data, not historical changes.
- **Incremental Sync**: Tracks last sync time and only syncs data that has changed.
- **Message Files**: Message content remains in text files. Database stores metadata for querying.

## Troubleshooting

### Database Locked
If you get a "database is locked" error, make sure no other process is using the database.

### API Rate Limiting
The sync scripts handle rate limiting automatically with retries. If you see many rate limit errors, consider reducing sync frequency.

### Missing Data
If some data is missing:
1. Check sync logs in the `sync_logs` table
2. Verify API credentials in `config.py`
3. Run a full sync to ensure all data is fetched
