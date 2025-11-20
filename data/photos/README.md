# Photo Storage

This directory stores photo metadata for listings.

Photos are NOT downloaded locally - only URLs and metadata are stored in the database.

The directory structure is:
```
photos/
└── listings/
    └── {listing_id}/
        ├── thumbnails/      # (not used - photos stored in DB only)
        ├── full/            # (not used - photos stored in DB only)
        └── metadata.json    # (optional - can store additional metadata)
```

All photo information is stored in the `listing_photos` table in the database.
