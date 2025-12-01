#!/usr/bin/env python3
"""
File storage utility (local filesystem only - S3 support removed).
Keeps S3Storage class name for backward compatibility.
"""

import os
import logging
import shutil
from typing import Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


class S3Storage:
    """File storage utility (local filesystem only - S3 support removed)."""
    
    def __init__(self, bucket_name: Optional[str] = None, region: Optional[str] = None):
        """
        Initialize file storage (local filesystem only).
        
        Args:
            bucket_name: Ignored (kept for backward compatibility)
            region: Ignored (kept for backward compatibility)
        """
        # Get conversations directory from config or use default
        from dashboard.config import CONVERSATIONS_DIR
        self.base_dir = CONVERSATIONS_DIR
        self.use_s3 = False  # Always False - S3 removed
        self.s3_client = None
        
        # Ensure base directory exists
        Path(self.base_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"File storage initialized: using local filesystem at {self.base_dir}")
    
    def upload_file(self, local_path: str, s3_key: str) -> bool:
        """
        Copy file to storage location (local filesystem).
        
        Args:
            local_path: Path to local file
            s3_key: Relative path within conversations/ directory (e.g., "conversations/Listing Name/file.txt")
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Remove 'conversations/' prefix if present, then build full path
            relative_path = s3_key.lstrip('conversations/').lstrip('/')
            target_path = Path(self.base_dir) / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            shutil.copy2(local_path, target_path)
            logger.debug(f"Copied file to local storage: {s3_key} -> {target_path}")
            return True
        except Exception as e:
            logger.error(f"Error copying file to local storage {s3_key}: {e}", exc_info=True)
            return False
    
    def download_file(self, s3_key: str, local_path: str) -> bool:
        """
        Copy file from storage to local path (local filesystem).
        
        Args:
            s3_key: Relative path within conversations/ directory
            local_path: Path to save file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Remove 'conversations/' prefix if present, then build full path
            relative_path = s3_key.lstrip('conversations/').lstrip('/')
            source_path = Path(self.base_dir) / relative_path
            
            if not source_path.exists():
                logger.warning(f"File not found in local storage: {s3_key}")
                return False
            
            # Ensure target directory exists
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, local_path)
            logger.debug(f"Copied file from local storage: {s3_key} -> {local_path}")
            return True
        except Exception as e:
            logger.error(f"Error copying file from local storage {s3_key}: {e}", exc_info=True)
            return False
    
    def read_file(self, s3_key: str) -> Optional[str]:
        """
        Read file content from local filesystem.
        
        Args:
            s3_key: Relative path within conversations/ directory
            
        Returns:
            File content as string, or None if error
        """
        try:
            # Remove 'conversations/' prefix if present, then build full path
            relative_path = s3_key.lstrip('conversations/').lstrip('/')
            file_path = Path(self.base_dir) / relative_path
            
            if file_path.exists() and file_path.is_file():
                content = file_path.read_text(encoding='utf-8')
                logger.debug(f"Read file from local storage: {s3_key} ({len(content)} bytes)")
                return content
            else:
                logger.debug(f"File not found in local storage: {s3_key}")
                return None
        except Exception as e:
            logger.error(f"Error reading file from local storage {s3_key}: {e}", exc_info=True)
            return None
    
    def write_file(self, s3_key: str, content: str) -> bool:
        """
        Write content directly to local filesystem.
        
        Args:
            s3_key: Relative path within conversations/ directory
            content: Content to write (string)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Remove 'conversations/' prefix if present, then build full path
            relative_path = s3_key.lstrip('conversations/').lstrip('/')
            file_path = Path(self.base_dir) / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_path.write_text(content, encoding='utf-8')
            logger.debug(f"Wrote file to local storage: {s3_key} ({len(content)} bytes)")
            return True
        except Exception as e:
            logger.error(f"Error writing file to local storage {s3_key}: {e}", exc_info=True)
            return False
    
    def file_exists(self, s3_key: str) -> bool:
        """
        Check if file exists in local filesystem.
        
        Args:
            s3_key: Relative path within conversations/ directory
            
        Returns:
            True if file exists, False otherwise
        """
        try:
            # Remove 'conversations/' prefix if present, then build full path
            relative_path = s3_key.lstrip('conversations/').lstrip('/')
            file_path = Path(self.base_dir) / relative_path
            return file_path.exists() and file_path.is_file()
        except Exception as e:
            logger.error(f"Error checking file existence in local storage {s3_key}: {e}", exc_info=True)
            return False
    
    def delete_file(self, s3_key: str) -> bool:
        """
        Delete file from local filesystem.
        
        Args:
            s3_key: Relative path within conversations/ directory
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Remove 'conversations/' prefix if present, then build full path
            relative_path = s3_key.lstrip('conversations/').lstrip('/')
            file_path = Path(self.base_dir) / relative_path
            
            if file_path.exists():
                file_path.unlink()
                logger.debug(f"Deleted file from local storage: {s3_key}")
                return True
            else:
                logger.debug(f"File not found for deletion: {s3_key}")
                return False
        except Exception as e:
            logger.error(f"Error deleting file from local storage {s3_key}: {e}", exc_info=True)
            return False
    
    def list_files(self, prefix: str = "") -> List[str]:
        """
        List files in local filesystem with given prefix.
        
        Args:
            prefix: Prefix to filter files (e.g., "conversations/Listing Name/")
            
        Returns:
            List of file paths (relative to conversations/)
        """
        try:
            # Remove 'conversations/' prefix if present
            search_prefix = prefix.lstrip('conversations/').lstrip('/')
            search_path = Path(self.base_dir) / search_prefix if search_prefix else Path(self.base_dir)
            
            if not search_path.exists():
                return []
            
            files = []
            # Walk directory and collect all files
            for file_path in search_path.rglob('*'):
                if file_path.is_file():
                    # Get relative path from base_dir
                    relative_path = file_path.relative_to(Path(self.base_dir))
                    # Add 'conversations/' prefix for compatibility
                    files.append(f"conversations/{relative_path.as_posix()}")
            
            logger.debug(f"Listed {len(files)} files from local storage with prefix: {prefix}")
            return files
        except Exception as e:
            logger.error(f"Error listing files from local storage with prefix {prefix}: {e}", exc_info=True)
            return []

