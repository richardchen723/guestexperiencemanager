#!/usr/bin/env python3
"""
Document storage utilities for saving and validating uploaded documents.
"""

import os
import uuid
import hashlib
from pathlib import Path
from typing import Tuple, Optional
from werkzeug.datastructures import FileStorage
import logging

logger = logging.getLogger(__name__)

# Allowed document MIME types
ALLOWED_MIME_TYPES = {
    'application/pdf': 'PDF',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'Word',
    'application/msword': 'Word (legacy)'
}

# Allowed file extensions
ALLOWED_EXTENSIONS = {'.pdf', '.doc', '.docx'}

# Max file size: 25MB
MAX_FILE_SIZE = 25 * 1024 * 1024


def validate_document(file: FileStorage) -> Tuple[bool, Optional[str]]:
    """
    Validate uploaded document file.
    
    Args:
        file: Werkzeug FileStorage object
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not file or not file.filename:
        return False, "No file provided"
    
    # Check file extension
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        return False, f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
    
    # Check MIME type
    mime_type = file.content_type
    if mime_type and mime_type not in ALLOWED_MIME_TYPES:
        # Allow if extension is valid (some browsers may not send correct MIME type)
        logger.warning(f"Unexpected MIME type {mime_type} for file {file.filename}, but extension is valid")
    
    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)  # Reset to beginning
    
    if file_size > MAX_FILE_SIZE:
        return False, f"File size exceeds maximum of {MAX_FILE_SIZE / (1024 * 1024):.1f} MB"
    
    if file_size == 0:
        return False, "File is empty"
    
    return True, None


def save_document(file: FileStorage, base_dir: str, document_id: int) -> Tuple[str, str, int, str]:
    """
    Save uploaded document to filesystem.
    
    Args:
        file: Werkzeug FileStorage object
        base_dir: Base directory for document storage
        document_id: Document ID for creating subdirectory
    
    Returns:
        Tuple of (file_path, file_name, file_size, file_hash)
    
    Raises:
        ValueError: If file validation fails
    """
    # Validate file
    is_valid, error = validate_document(file)
    if not is_valid:
        raise ValueError(error)
    
    # Create directory structure
    upload_dir = Path(base_dir) / str(document_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate unique filename
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        file_ext = '.pdf'  # Default fallback
    
    unique_filename = f"{uuid.uuid4().hex}{file_ext}"
    file_path = upload_dir / unique_filename
    
    # Reset file position and read content
    file.seek(0)
    file_content = file.read()
    file.seek(0)  # Reset again for potential reuse
    
    # Calculate file hash (SHA256)
    file_hash = hashlib.sha256(file_content).hexdigest()
    
    # Save file
    with open(file_path, 'wb') as f:
        f.write(file_content)
    
    # Get file size
    file_size = file_path.stat().st_size
    
    # Return relative path (from base_dir)
    relative_path = f"{document_id}/{unique_filename}"
    
    logger.info(f"Saved document: {relative_path}, size: {file_size} bytes, hash: {file_hash[:16]}...")
    
    return relative_path, file.filename, file_size, file_hash


def get_document_path(base_dir: str, file_path: str) -> Path:
    """
    Get full path to document file.
    
    Args:
        base_dir: Base directory for document storage
        file_path: Relative file path (e.g., "123/uuid.pdf")
    
    Returns:
        Path object to the document file
    """
    return Path(base_dir) / file_path


def delete_document(base_dir: str, file_path: str) -> bool:
    """
    Delete document file from filesystem.
    
    Args:
        base_dir: Base directory for document storage
        file_path: Relative file path
    
    Returns:
        True if file was deleted, False if it didn't exist
    """
    full_path = get_document_path(base_dir, file_path)
    
    if full_path.exists():
        try:
            full_path.unlink()
            # Also try to remove parent directory if empty
            parent_dir = full_path.parent
            if parent_dir.exists() and not any(parent_dir.iterdir()):
                parent_dir.rmdir()
            return True
        except Exception as e:
            logger.error(f"Error deleting document {file_path}: {e}", exc_info=True)
            return False
    
    return False

