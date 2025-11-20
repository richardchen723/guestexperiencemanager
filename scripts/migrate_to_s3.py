#!/usr/bin/env python3
"""
Migration script to upload conversation files from local filesystem to AWS S3.

This script:
1. Scans local conversations directory
2. Uploads each file to S3
3. Updates database records with S3 keys instead of local paths

Usage:
    python3 scripts/migrate_to_s3.py --conversations-dir ./conversations --update-db
"""

import sys
import os
import argparse
import logging
from pathlib import Path
from typing import List, Tuple

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.s3_storage import S3Storage
from database.models import get_session, Conversation, MessageMetadata

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def find_conversation_files(conversations_dir: str) -> List[Tuple[str, str]]:
    """
    Find all conversation files in the local directory.
    
    Args:
        conversations_dir: Path to conversations directory
    
    Returns:
        List of tuples: (local_file_path, relative_path)
    """
    conversations_path = Path(conversations_dir)
    if not conversations_path.exists():
        logger.error(f"Conversations directory not found: {conversations_dir}")
        return []
    
    files = []
    for file_path in conversations_path.rglob("*_conversation.txt"):
        relative_path = file_path.relative_to(conversations_path)
        files.append((str(file_path), str(relative_path)))
    
    logger.info(f"Found {len(files)} conversation files")
    return files


def upload_file_to_s3(s3_storage: S3Storage, local_path: str, relative_path: str) -> str:
    """
    Upload a single file to S3.
    
    Args:
        s3_storage: S3Storage instance
        local_path: Local file path
        relative_path: Relative path (will become S3 key)
    
    Returns:
        S3 key of uploaded file
    """
    try:
        # Read file content
        with open(local_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Convert relative path to S3 key
        # e.g., "Listing Name/Guest_2025-11-15_conversation.txt" -> "conversations/Listing Name/Guest_2025-11-15_conversation.txt"
        s3_key = f"conversations/{relative_path}"
        
        # Upload to S3
        s3_storage.s3_client.put_object(
            Bucket=s3_storage.bucket_name,
            Key=s3_key,
            Body=content.encode('utf-8'),
            ContentType='text/plain; charset=utf-8'
        )
        
        logger.debug(f"Uploaded: {local_path} -> {s3_key}")
        return s3_key
        
    except Exception as e:
        logger.error(f"Error uploading {local_path}: {e}", exc_info=True)
        raise


def update_database_paths(db_path: str, old_path: str, new_path: str):
    """
    Update database records to use S3 keys instead of local paths.
    
    Args:
        db_path: Database path or URL
        old_path: Old local file path
        new_path: New S3 key
    """
    session = get_session(db_path)
    
    try:
        # Update conversations table
        conversations = session.query(Conversation).filter(
            Conversation.conversation_file_path == old_path
        ).all()
        
        for conv in conversations:
            conv.conversation_file_path = new_path
        
        # Update messages_metadata table
        messages = session.query(MessageMetadata).filter(
            MessageMetadata.message_file_path == old_path
        ).all()
        
        for msg in messages:
            msg.message_file_path = new_path
        
        if conversations or messages:
            session.commit()
            logger.info(f"Updated {len(conversations)} conversations and {len(messages)} messages: {old_path} -> {new_path}")
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error updating database paths: {e}", exc_info=True)
        raise
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description='Migrate conversation files to S3')
    parser.add_argument('--conversations-dir', help='Path to conversations directory', 
                       default='./conversations')
    parser.add_argument('--db-path', help='Database path or URL', 
                       default=None)  # Will use config if not provided
    parser.add_argument('--update-db', action='store_true', 
                       help='Update database records with S3 keys')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Dry run (do not upload files)')
    parser.add_argument('--batch-size', type=int, default=100, 
                       help='Number of files to process before committing (default: 100)')
    
    args = parser.parse_args()
    
    # Get database path from config if not provided
    if args.db_path is None:
        from config import DATABASE_PATH
        args.db_path = DATABASE_PATH
    
    # Check S3 configuration
    if not os.getenv("AWS_S3_BUCKET_NAME"):
        logger.error("AWS_S3_BUCKET_NAME environment variable is required")
        sys.exit(1)
    
    if args.dry_run:
        logger.info("DRY RUN MODE - No files will be uploaded")
    
    try:
        # Initialize S3 storage
        s3_storage = S3Storage()
        logger.info(f"Initialized S3 storage: bucket={s3_storage.bucket_name}")
        
        # Find all conversation files
        files = find_conversation_files(args.conversations_dir)
        
        if not files:
            logger.warning("No conversation files found")
            return
        
        # Upload files
        uploaded = 0
        failed = 0
        path_mappings = {}  # old_path -> new_path
        
        for local_path, relative_path in files:
            try:
                if not args.dry_run:
                    s3_key = upload_file_to_s3(s3_storage, local_path, relative_path)
                    path_mappings[local_path] = s3_key
                    uploaded += 1
                    
                    # Update database in batches
                    if args.update_db and uploaded % args.batch_size == 0:
                        logger.info(f"Processed {uploaded} files, updating database...")
                        for old_path, new_path in list(path_mappings.items()):
                            update_database_paths(args.db_path, old_path, new_path)
                        path_mappings.clear()
                else:
                    # Dry run: just log what would be uploaded
                    s3_key = f"conversations/{relative_path}"
                    logger.info(f"Would upload: {local_path} -> {s3_key}")
                    uploaded += 1
                    
            except Exception as e:
                logger.error(f"Failed to upload {local_path}: {e}")
                failed += 1
                continue
        
        # Update remaining database records
        if args.update_db and not args.dry_run and path_mappings:
            logger.info("Updating remaining database records...")
            for old_path, new_path in path_mappings.items():
                update_database_paths(args.db_path, old_path, new_path)
        
        logger.info("\n" + "="*60)
        logger.info(f"Migration completed!")
        logger.info(f"  Uploaded: {uploaded} files")
        logger.info(f"  Failed: {failed} files")
        logger.info("="*60)
        
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

