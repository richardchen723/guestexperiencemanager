#!/usr/bin/env python3
"""
S3 Storage utility for file operations.
Supports both S3 storage and local filesystem fallback.
"""

import os
import logging
from typing import Optional, List
from pathlib import Path
import boto3
from botocore.exceptions import ClientError, BotoCoreError
from botocore.config import Config

logger = logging.getLogger(__name__)


class S3Storage:
    """S3 storage utility with local filesystem fallback."""
    
    def __init__(self, bucket_name: Optional[str] = None, region: Optional[str] = None):
        """
        Initialize S3 storage client.
        
        Args:
            bucket_name: S3 bucket name (from config if not provided)
            region: AWS region (from config if not provided)
        """
        self.bucket_name = bucket_name or os.getenv("AWS_S3_BUCKET_NAME")
        self.region = region or os.getenv("AWS_S3_REGION", "us-east-1")
        self.use_s3 = bool(self.bucket_name)
        
        if self.use_s3:
            try:
                # Configure boto3 client with retry logic
                config = Config(
                    retries={
                        'max_attempts': 3,
                        'mode': 'adaptive'
                    },
                    connect_timeout=10,
                    read_timeout=30
                )
                
                self.s3_client = boto3.client(
                    's3',
                    region_name=self.region,
                    config=config
                )
                
                # Verify bucket exists
                try:
                    self.s3_client.head_bucket(Bucket=self.bucket_name)
                    logger.info(f"S3 storage initialized: bucket={self.bucket_name}, region={self.region}")
                except ClientError as e:
                    error_code = e.response.get('Error', {}).get('Code', '')
                    if error_code == '404':
                        logger.error(f"S3 bucket '{self.bucket_name}' not found")
                        self.use_s3 = False
                    else:
                        logger.warning(f"Error accessing S3 bucket: {e}. Falling back to local storage.")
                        self.use_s3 = False
            except Exception as e:
                logger.warning(f"Failed to initialize S3 client: {e}. Falling back to local storage.")
                self.use_s3 = False
        else:
            logger.info("S3 storage not configured. Using local filesystem.")
            self.s3_client = None
    
    def upload_file(self, local_path: str, s3_key: str) -> bool:
        """
        Upload a file from local filesystem to S3.
        
        Args:
            local_path: Path to local file
            s3_key: S3 object key (path in bucket)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.use_s3:
            logger.debug(f"S3 not configured. Skipping upload: {s3_key}")
            return False
        
        try:
            self.s3_client.upload_file(local_path, self.bucket_name, s3_key)
            logger.debug(f"Uploaded file to S3: {s3_key}")
            return True
        except Exception as e:
            logger.error(f"Error uploading file to S3 {s3_key}: {e}", exc_info=True)
            return False
    
    def download_file(self, s3_key: str, local_path: str) -> bool:
        """
        Download a file from S3 to local filesystem.
        
        Args:
            s3_key: S3 object key (path in bucket)
            local_path: Path to save downloaded file
            
        Returns:
            True if successful, False otherwise
        """
        if not self.use_s3:
            logger.debug(f"S3 not configured. Skipping download: {s3_key}")
            return False
        
        try:
            # Ensure directory exists
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            
            self.s3_client.download_file(self.bucket_name, s3_key, local_path)
            logger.debug(f"Downloaded file from S3: {s3_key} -> {local_path}")
            return True
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == '404':
                logger.warning(f"File not found in S3: {s3_key}")
            else:
                logger.error(f"Error downloading file from S3 {s3_key}: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Error downloading file from S3 {s3_key}: {e}", exc_info=True)
            return False
    
    def read_file(self, s3_key: str) -> Optional[str]:
        """
        Read file content directly from S3.
        
        Args:
            s3_key: S3 object key (path in bucket)
            
        Returns:
            File content as string, or None if error
        """
        if not self.use_s3:
            logger.debug(f"S3 not configured. Cannot read from S3: {s3_key}")
            return None
        
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            content = response['Body'].read().decode('utf-8')
            logger.debug(f"Read file from S3: {s3_key} ({len(content)} bytes)")
            return content
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == '404':
                logger.debug(f"File not found in S3: {s3_key}")
            else:
                logger.error(f"Error reading file from S3 {s3_key}: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error reading file from S3 {s3_key}: {e}", exc_info=True)
            return None
    
    def write_file(self, s3_key: str, content: str) -> bool:
        """
        Write content directly to S3.
        
        Args:
            s3_key: S3 object key (path in bucket)
            content: Content to write (string)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.use_s3:
            logger.debug(f"S3 not configured. Cannot write to S3: {s3_key}")
            return False
        
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=content.encode('utf-8'),
                ContentType='text/plain; charset=utf-8'
            )
            logger.debug(f"Wrote file to S3: {s3_key} ({len(content)} bytes)")
            return True
        except Exception as e:
            logger.error(f"Error writing file to S3 {s3_key}: {e}", exc_info=True)
            return False
    
    def file_exists(self, s3_key: str) -> bool:
        """
        Check if file exists in S3.
        
        Args:
            s3_key: S3 object key (path in bucket)
            
        Returns:
            True if file exists, False otherwise
        """
        if not self.use_s3:
            return False
        
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == '404':
                return False
            logger.error(f"Error checking file existence in S3 {s3_key}: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Error checking file existence in S3 {s3_key}: {e}", exc_info=True)
            return False
    
    def delete_file(self, s3_key: str) -> bool:
        """
        Delete file from S3.
        
        Args:
            s3_key: S3 object key (path in bucket)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.use_s3:
            logger.debug(f"S3 not configured. Cannot delete from S3: {s3_key}")
            return False
        
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            logger.debug(f"Deleted file from S3: {s3_key}")
            return True
        except Exception as e:
            logger.error(f"Error deleting file from S3 {s3_key}: {e}", exc_info=True)
            return False
    
    def list_files(self, prefix: str = "") -> List[str]:
        """
        List files in S3 with given prefix.
        
        Args:
            prefix: Prefix to filter files (e.g., "conversations/")
            
        Returns:
            List of S3 keys (file paths)
        """
        if not self.use_s3:
            return []
        
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
            
            keys = []
            for page in pages:
                if 'Contents' in page:
                    keys.extend([obj['Key'] for obj in page['Contents']])
            
            logger.debug(f"Listed {len(keys)} files from S3 with prefix: {prefix}")
            return keys
        except Exception as e:
            logger.error(f"Error listing files from S3 with prefix {prefix}: {e}", exc_info=True)
            return []


