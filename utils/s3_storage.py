#!/usr/bin/env python3
"""
AWS S3 storage module for conversation files.
Provides S3Storage class for reading and writing conversation files to S3.
"""

import os
import logging
from typing import List, Optional
from datetime import datetime

try:
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

logger = logging.getLogger(__name__)


class S3Storage:
    """S3 storage handler for conversation files."""
    
    def __init__(self, bucket_name: Optional[str] = None, region: Optional[str] = None, prefix: str = "conversations/"):
        """
        Initialize S3 storage client.
        
        Args:
            bucket_name: S3 bucket name (defaults to AWS_S3_BUCKET_NAME env var)
            region: AWS region (defaults to AWS_S3_REGION env var)
            prefix: S3 key prefix for all conversation files (default: "conversations/")
        """
        if not BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for S3 storage. Install it with: pip install boto3"
            )
        
        self.bucket_name = bucket_name or os.getenv("AWS_S3_BUCKET_NAME")
        self.region = region or os.getenv("AWS_S3_REGION", "us-east-1")
        self.prefix = prefix.rstrip('/') + '/' if prefix else ""
        
        if not self.bucket_name:
            raise ValueError(
                "AWS_S3_BUCKET_NAME environment variable is required for S3 storage"
            )
        
        # Initialize S3 client
        self.s3_client = boto3.client(
            's3',
            region_name=self.region,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
        )
        
        logger.info(f"S3Storage initialized: bucket={self.bucket_name}, region={self.region}, prefix={self.prefix}")
    
    def sanitize_filename(self, name: str) -> str:
        """Sanitize filename by removing/replacing invalid characters for S3 keys."""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        return name.strip()
    
    def format_checkin_date(self, checkin_date: str) -> str:
        """Format check-in date for filename."""
        try:
            if isinstance(checkin_date, str):
                # Handle different date formats
                for fmt in ['%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ']:
                    try:
                        dt = datetime.strptime(checkin_date.split('T')[0], '%Y-%m-%d')
                        return dt.strftime('%Y-%m-%d')
                    except ValueError:
                        continue
            return str(checkin_date).split('T')[0] if checkin_date else 'unknown_date'
        except:
            return 'unknown_date'
    
    def save_conversation(self, listing_name: str, guest_name: str, 
                         checkin_date: str, messages: List[dict]) -> str:
        """
        Save conversation to S3 in conversational text format.
        
        Args:
            listing_name: Name of the listing
            guest_name: Name of the guest
            checkin_date: Check-in date string
            messages: List of message dictionaries
        
        Returns:
            S3 key path (e.g., "conversations/Listing Name/Guest Name_2025-11-15_conversation.txt")
        """
        # Sanitize names for S3 keys
        safe_listing_name = self.sanitize_filename(listing_name)
        safe_guest_name = self.sanitize_filename(guest_name)
        formatted_date = self.format_checkin_date(checkin_date)
        
        # Create S3 key
        filename = f"{safe_guest_name}_{formatted_date}_conversation.txt"
        s3_key = f"{self.prefix}{safe_listing_name}/{filename}"
        
        # Sort messages by timestamp
        sorted_messages = sorted(messages, key=lambda x: x.get('createdAt', ''))
        
        # Create conversational format
        conversational_text = []
        
        # Add header information
        conversational_text.append("=" * 60)
        conversational_text.append("GUEST CONVERSATION")
        conversational_text.append("=" * 60)
        conversational_text.append(f"Guest: {guest_name}")
        conversational_text.append(f"Listing: {listing_name}")
        conversational_text.append(f"Check-in Date: {checkin_date}")
        conversational_text.append(f"Total Messages: {len(sorted_messages)}")
        conversational_text.append("")
        
        # Add messages in conversational format
        for i, message in enumerate(sorted_messages, 1):
            timestamp = message.get('createdAt', '')
            sender = message.get('sender', 'Unknown')
            content = message.get('content', '').strip()
            
            # Format timestamp nicely
            if timestamp:
                try:
                    dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                    formatted_time = dt.strftime('%B %d, %Y at %I:%M %p')
                except:
                    formatted_time = timestamp
            else:
                formatted_time = 'Unknown time'
            
            # Add message with proper formatting
            conversational_text.append(f"[{i}] {formatted_time}")
            conversational_text.append(f"{sender}: {content}")
            conversational_text.append("")  # Empty line between messages
        
        # Join all lines
        full_text = '\n'.join(conversational_text)
        
        # Upload to S3
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=full_text.encode('utf-8'),
                ContentType='text/plain; charset=utf-8',
                Metadata={
                    'listing_name': listing_name,
                    'guest_name': guest_name,
                    'checkin_date': checkin_date,
                    'message_count': str(len(sorted_messages))
                }
            )
            logger.debug(f"Successfully saved conversation to S3: {s3_key}")
            return s3_key
        except ClientError as e:
            error_msg = f"Error uploading conversation to S3 ({s3_key}): {e}"
            logger.error(error_msg, exc_info=True)
            raise Exception(error_msg) from e
        except BotoCoreError as e:
            error_msg = f"Boto3 error uploading conversation to S3 ({s3_key}): {e}"
            logger.error(error_msg, exc_info=True)
            raise Exception(error_msg) from e
    
    def read_conversation(self, s3_key: str) -> Optional[str]:
        """
        Read conversation file content from S3.
        
        Args:
            s3_key: S3 key path (e.g., "conversations/Listing Name/Guest Name_2025-11-15_conversation.txt")
        
        Returns:
            File content as string, or None if not found
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            content = response['Body'].read().decode('utf-8')
            logger.debug(f"Successfully read conversation from S3: {s3_key}")
            return content
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'NoSuchKey':
                logger.warning(f"Conversation file not found in S3: {s3_key}")
                return None
            else:
                error_msg = f"Error reading conversation from S3 ({s3_key}): {e}"
                logger.error(error_msg, exc_info=True)
                return None
        except BotoCoreError as e:
            error_msg = f"Boto3 error reading conversation from S3 ({s3_key}): {e}"
            logger.error(error_msg, exc_info=True)
            return None
    
    def list_conversations(self, prefix: Optional[str] = None) -> List[str]:
        """
        List all conversation file keys in S3.
        
        Args:
            prefix: Optional prefix to filter keys (defaults to self.prefix)
        
        Returns:
            List of S3 keys
        """
        search_prefix = prefix or self.prefix
        keys = []
        
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=search_prefix)
            
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        keys.append(obj['Key'])
            
            logger.debug(f"Listed {len(keys)} conversation files from S3 (prefix: {search_prefix})")
            return keys
        except ClientError as e:
            error_msg = f"Error listing conversations from S3: {e}"
            logger.error(error_msg, exc_info=True)
            return []
        except BotoCoreError as e:
            error_msg = f"Boto3 error listing conversations from S3: {e}"
            logger.error(error_msg, exc_info=True)
            return []
    
    def delete_conversation(self, s3_key: str) -> bool:
        """
        Delete a conversation file from S3.
        
        Args:
            s3_key: S3 key path to delete
        
        Returns:
            True if successful, False otherwise
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            logger.debug(f"Successfully deleted conversation from S3: {s3_key}")
            return True
        except ClientError as e:
            error_msg = f"Error deleting conversation from S3 ({s3_key}): {e}"
            logger.error(error_msg, exc_info=True)
            return False
        except BotoCoreError as e:
            error_msg = f"Boto3 error deleting conversation from S3 ({s3_key}): {e}"
            logger.error(error_msg, exc_info=True)
            return False

