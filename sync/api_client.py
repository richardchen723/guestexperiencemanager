#!/usr/bin/env python3
"""
Shared Hostaway API client for sync operations.
Handles OAuth 2.0 authentication and API requests with rate limiting.
"""

import time
import logging
import requests
from datetime import datetime
from typing import List, Dict, Optional, Any

from config import HOSTAWAY_API_KEY, HOSTAWAY_ACCOUNT_ID, HOSTAWAY_BASE_URL, VERBOSE

# Configure logging
logger = logging.getLogger(__name__)

# Constants
RATE_LIMIT_RETRY_DELAY = 10  # seconds
TOKEN_EXPIRATION_BUFFER = 60  # seconds
DEFAULT_TOKEN_EXPIRATION = 3600  # seconds
MAX_RETRIES = 3  # Maximum number of retries for network errors
RETRY_DELAY_BASE = 2  # Base delay in seconds for exponential backoff


class HostawayAPIClient:
    """API client for Hostaway with OAuth 2.0 authentication and rate limiting."""
    
    def __init__(self):
        """Initialize the API client with credentials."""
        self.account_id = HOSTAWAY_ACCOUNT_ID
        self.api_key = HOSTAWAY_API_KEY
        self.base_url = HOSTAWAY_BASE_URL
        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[float] = None
    
    def get_access_token(self) -> Optional[str]:
        """
        Get OAuth 2.0 access token.
        
        Returns:
            Access token string if successful, None otherwise.
        """
        # Check if we have a valid cached token
        if (self.access_token and 
            self.token_expires_at and 
            datetime.now().timestamp() < self.token_expires_at):
            return self.access_token
        
        if VERBOSE:
            logger.info("Getting new access token...")
        
        url = f"{self.base_url}/accessTokens"
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cache-Control': 'no-cache'
        }
        data = {
            'grant_type': 'client_credentials',
            'client_id': self.account_id,
            'client_secret': self.api_key,
            'scope': 'general'
        }
        
        try:
            response = requests.post(url, headers=headers, data=data, timeout=30)
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get('access_token')
            
            if not self.access_token:
                logger.error("No access token in response")
                return None
            
            # Set token expiration with buffer
            expires_in = token_data.get('expires_in', DEFAULT_TOKEN_EXPIRATION)
            self.token_expires_at = (
                datetime.now().timestamp() + expires_in - TOKEN_EXPIRATION_BUFFER
            )
            
            return self.access_token
            
        except requests.exceptions.Timeout:
            logger.error("Timeout getting access token")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting access token: {e}")
            return None
    
    def get_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers with valid access token.
        
        Returns:
            Dictionary of headers, or empty dict if token unavailable.
        """
        token = self.get_access_token()
        if not token:
            return {}
        
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None, retry_count: int = 0) -> Optional[Dict]:
        """
        Make API request with error handling, rate limiting, and retry logic.
        
        Args:
            endpoint: API endpoint path (without base URL)
            params: Optional query parameters
            retry_count: Current retry attempt (for recursive retries)
            
        Returns:
            Response JSON data, or None on error.
        """
        url = f"{self.base_url}/{endpoint}"
        headers = self.get_headers()
        
        if not headers:
            logger.error("Failed to get valid access token")
            return None
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            # Handle rate limiting with retry
            if response.status_code == 429:
                if VERBOSE:
                    logger.warning(f"Rate limit exceeded for {endpoint}, waiting {RATE_LIMIT_RETRY_DELAY}s...")
                time.sleep(RATE_LIMIT_RETRY_DELAY)
                response = requests.get(url, headers=headers, params=params, timeout=30)
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            # Retry on timeout
            if retry_count < MAX_RETRIES:
                wait_time = RETRY_DELAY_BASE * (2 ** retry_count)  # Exponential backoff
                logger.warning(f"Timeout making request to {endpoint}, retrying in {wait_time}s (attempt {retry_count + 1}/{MAX_RETRIES})...")
                time.sleep(wait_time)
                return self._make_request(endpoint, params, retry_count + 1)
            logger.error(f"Timeout making request to {endpoint} after {MAX_RETRIES} retries")
            return None
            
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
            # Retry on SSL/connection errors (common with network issues)
            if retry_count < MAX_RETRIES:
                wait_time = RETRY_DELAY_BASE * (2 ** retry_count)  # Exponential backoff
                error_type = "SSL error" if isinstance(e, requests.exceptions.SSLError) else "Connection error"
                logger.warning(f"{error_type} making request to {endpoint}: {e}. Retrying in {wait_time}s (attempt {retry_count + 1}/{MAX_RETRIES})...")
                time.sleep(wait_time)
                return self._make_request(endpoint, params, retry_count + 1)
            error_type = "SSL error" if isinstance(e, requests.exceptions.SSLError) else "Connection error"
            logger.error(f"{error_type} making request to {endpoint} after {MAX_RETRIES} retries: {e}")
            return None
            
        except requests.exceptions.HTTPError as e:
            # Don't retry on HTTP errors (4xx, 5xx) except 429 (already handled above)
            logger.error(f"HTTP error for {endpoint}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.debug(f"Response: {e.response.text[:200]}")
            return None
            
        except requests.exceptions.RequestException as e:
            # Retry on other request exceptions (network issues, etc.)
            if retry_count < MAX_RETRIES:
                wait_time = RETRY_DELAY_BASE * (2 ** retry_count)  # Exponential backoff
                logger.warning(f"Request error for {endpoint}: {e}. Retrying in {wait_time}s (attempt {retry_count + 1}/{MAX_RETRIES})...")
                time.sleep(wait_time)
                return self._make_request(endpoint, params, retry_count + 1)
            logger.error(f"Error making request to {endpoint} after {MAX_RETRIES} retries: {e}")
            return None
    
    def get_listings(self, limit: Optional[int] = None, 
                    offset: Optional[int] = None) -> List[Dict]:
        """
        Get all listings with pagination support.
        
        Args:
            limit: Maximum number of listings to return
            offset: Number of listings to skip
            
        Returns:
            List of listing dictionaries.
        """
        params: Dict[str, int] = {}
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        
        data = self._make_request("listings", params)
        if data and 'result' in data:
            return data['result']
        return []
    
    def get_listing(self, listing_id: int) -> Optional[Dict]:
        """
        Get a specific listing by ID.
        
        Args:
            listing_id: The listing ID.
            
        Returns:
            Listing dictionary, or None if not found.
        """
        data = self._make_request(f"listings/{listing_id}")
        if data and 'result' in data:
            return data['result']
        return None
    
    def get_reservations(self, listing_id: Optional[int] = None, 
                        status: Optional[str] = None,
                        limit: Optional[int] = None, 
                        offset: Optional[int] = None,
                        updated_on: Optional[datetime] = None,
                        latest_activity_on: Optional[datetime] = None) -> List[Dict]:
        """
        Get reservations with optional filters.
        
        Args:
            listing_id: Filter by listing ID
            status: Filter by reservation status
            limit: Maximum number of reservations to return
            offset: Number of reservations to skip
            updated_on: Filter reservations updated after this timestamp (ISO 8601 format)
            latest_activity_on: Filter reservations with activity after this timestamp (ISO 8601 format)
            
        Returns:
            List of reservation dictionaries.
        """
        params: Dict[str, Any] = {}
        if listing_id:
            params['listingId'] = listing_id
        if status:
            params['status'] = status
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        if updated_on:
            # Format timestamp as ISO 8601 string (Hostaway API format)
            params['updatedOn'] = updated_on.strftime('%Y-%m-%dT%H:%M:%SZ')
        if latest_activity_on:
            params['latestActivityOn'] = latest_activity_on.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        data = self._make_request("reservations", params)
        if data and 'result' in data:
            return data['result']
        return []
    
    def get_all_reservations(self, limit: int = 100) -> List[Dict]:
        """
        Get all reservations with pagination support.
        
        Fetches all reservations across all listings without filtering by listing_id.
        
        Args:
            limit: Number of reservations per page (default: 100)
            
        Returns:
            List of all reservation dictionaries.
        """
        all_reservations = []
        offset = 0
        
        while True:
            reservations = self.get_reservations(limit=limit, offset=offset)
            if not reservations:
                break
            
            all_reservations.extend(reservations)
            
            # If we got fewer than the limit, we've reached the end
            if len(reservations) < limit:
                break
            
            offset += limit
        
        return all_reservations
    
    def get_conversations(self, reservation_id: Optional[int] = None,
                         limit: Optional[int] = None, 
                         offset: Optional[int] = None) -> List[Dict]:
        """
        Get conversations with optional filters.
        
        Args:
            reservation_id: Filter by reservation ID
            limit: Maximum number of conversations to return
            offset: Number of conversations to skip
            
        Returns:
            List of conversation dictionaries.
        """
        params: Dict[str, int] = {}
        if reservation_id:
            params['reservationId'] = reservation_id
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        
        data = self._make_request("conversations", params)
        if data and 'result' in data:
            return data['result']
        return []
    
    def get_all_conversations(self, limit: int = 100) -> List[Dict]:
        """
        Get all conversations with pagination support.
        
        Args:
            limit: Number of conversations per page (default: 100)
            
        Returns:
            List of all conversation dictionaries.
        """
        all_conversations = []
        offset = 0
        
        while True:
            conversations = self.get_conversations(limit=limit, offset=offset)
            if not conversations:
                break
            
            all_conversations.extend(conversations)
            
            # If we got fewer than the limit, we've reached the end
            if len(conversations) < limit:
                break
            
            offset += limit
        
        return all_conversations
    
    def get_conversation_messages(self, conversation_id: int) -> List[Dict]:
        """
        Get all messages for a specific conversation.
        
        Args:
            conversation_id: The conversation ID.
            
        Returns:
            List of message dictionaries.
        """
        data = self._make_request(f"conversations/{conversation_id}/messages")
        if data and 'result' in data:
            return data['result']
        return []
    
    def get_reviews(self, listing_id: Optional[int] = None,
                    reservation_id: Optional[int] = None,
                    limit: Optional[int] = None,
                    offset: Optional[int] = None,
                    status: Optional[str] = None) -> List[Dict]:
        """
        Get reviews with optional filters.
        
        Args:
            listing_id: Filter by listing ID
            reservation_id: Filter by reservation ID
            limit: Maximum number of reviews to return
            offset: Number of reviews to skip
            status: Filter by review status (e.g., 'Published')
            
        Returns:
            List of review dictionaries with sub-ratings.
        """
        params: Dict[str, Any] = {}
        if listing_id:
            params['listingId'] = listing_id
        if reservation_id:
            params['reservationId'] = reservation_id
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        if status:
            params['status'] = status
        
        data = self._make_request("reviews", params)
        if data and 'result' in data:
            return data['result']
        return []