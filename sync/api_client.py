#!/usr/bin/env python3
"""
Shared Hostaway API client for sync operations.
Handles OAuth 2.0 authentication and API requests with rate limiting.
"""

import time
import logging
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple

from config import HOSTAWAY_API_KEY, HOSTAWAY_ACCOUNT_ID, HOSTAWAY_BASE_URL, VERBOSE

# Configure logging
logger = logging.getLogger(__name__)

# Constants
RATE_LIMIT_RETRY_DELAY = 10  # seconds
TOKEN_EXPIRATION_BUFFER = 60  # seconds
TOKEN_ACTIVATION_DELAY = 1.0  # Hostaway tokens are not usable immediately
DEFAULT_TOKEN_EXPIRATION = 3600  # seconds
MAX_RETRIES = 5  # Maximum retries for DNS/network errors
BASE_DELAY = 1  # Base delay in seconds for exponential backoff
MAX_DELAY = 30  # Maximum delay cap in seconds


class HostawayAPIClient:
    """API client for Hostaway with OAuth 2.0 authentication and rate limiting."""

    # API clients are intentionally short-lived in several dashboard flows. Keep
    # tokens at the process level so every new client does not mint a new token.
    # The cache key prevents credentials for different accounts/environments from
    # sharing a token if they are ever used in the same process.
    _token_cache: Dict[Tuple[str, str, str], Tuple[str, float]] = {}
    _token_lock = threading.RLock()
    
    def __init__(self):
        """Initialize the API client with credentials."""
        # Validate credentials when client is instantiated (lazy validation)
        if not HOSTAWAY_ACCOUNT_ID or not HOSTAWAY_API_KEY:
            raise ValueError(
                "HOSTAWAY_ACCOUNT_ID and HOSTAWAY_API_KEY environment variables are required. "
                "Please set them in .env file or export them. "
                "Get your credentials from: https://dashboard.hostaway.com/settings/api"
            )
        
        self.account_id = HOSTAWAY_ACCOUNT_ID
        self.api_key = HOSTAWAY_API_KEY
        self.base_url = HOSTAWAY_BASE_URL
        self._token_cache_key = (
            str(self.base_url),
            str(self.account_id),
            str(self.api_key),
        )
        
        # Keep one requests session per worker thread. ``requests.Session`` is not
        # documented as thread-safe, while message sync intentionally fetches
        # several reservation conversations in parallel.
        self._thread_local = threading.local()

    @staticmethod
    def _token_from_headers(headers: Dict[str, str]) -> Optional[str]:
        authorization = str(headers.get('Authorization') or '')
        prefix = 'Bearer '
        return authorization[len(prefix):] if authorization.startswith(prefix) else None

    def _invalidate_access_token(self, rejected_token: Optional[str] = None) -> None:
        """Remove the cached token only if it is the token that was rejected."""
        with type(self)._token_lock:
            cached = type(self)._token_cache.get(self._token_cache_key)
            if cached and (rejected_token is None or cached[0] == rejected_token):
                type(self)._token_cache.pop(self._token_cache_key, None)

    def _build_session(self) -> requests.Session:
        """Create a pooled HTTP session for the current worker thread."""
        # Transport retries cover transient connection/read failures. HTTP status
        # retries are handled in ``_make_request`` so requests are not multiplied
        # by two independent retry loops.
        retry_strategy = Retry(
            total=2,
            connect=2,
            read=2,
            status=0,
            backoff_factor=BASE_DELAY,
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    @property
    def session(self) -> requests.Session:
        """Return a pooled session that is private to the calling thread."""
        session = getattr(self._thread_local, 'session', None)
        if session is None:
            session = self._build_session()
            self._thread_local.session = session
        return session
    
    def get_access_token(self) -> Optional[str]:
        """
        Get OAuth 2.0 access token.
        
        Returns:
            Access token string if successful, None otherwise.
        """
        with type(self)._token_lock:
            # Re-check inside the lock because another client/thread may have refreshed it.
            cached = type(self)._token_cache.get(self._token_cache_key)
            if cached and time.time() < cached[1]:
                return cached[0]
            if cached:
                type(self)._token_cache.pop(self._token_cache_key, None)

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

            # Retry logic for DNS/network errors with exponential backoff
            last_exception = None
            for attempt in range(MAX_RETRIES):
                try:
                    response = self.session.post(url, headers=headers, data=data, timeout=30)
                    response.raise_for_status()
                    token_data = response.json()
                    access_token = token_data.get('access_token')

                    if not access_token:
                        logger.error("No access token in response")
                        return None

                    # Set token expiration with buffer
                    try:
                        expires_in = float(
                            token_data.get('expires_in', DEFAULT_TOKEN_EXPIRATION)
                        )
                    except (TypeError, ValueError):
                        expires_in = float(DEFAULT_TOKEN_EXPIRATION)
                    expires_at = time.time() + expires_in - TOKEN_EXPIRATION_BUFFER

                    # Hostaway documents that a newly issued token becomes valid
                    # one second after the token response. Hold the shared refresh
                    # lock during that delay so no other client can use it early.
                    time.sleep(TOKEN_ACTIVATION_DELAY)
                    type(self)._token_cache[self._token_cache_key] = (
                        str(access_token),
                        expires_at,
                    )

                    if attempt > 0:
                        logger.info(f"Successfully got access token after {attempt} retries")

                    return str(access_token)
                
                except (requests.exceptions.Timeout,
                        requests.exceptions.ConnectTimeout,
                        requests.exceptions.ReadTimeout) as e:
                    last_exception = e
                    if attempt < MAX_RETRIES - 1:
                        wait_time = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                        logger.warning(f"Timeout getting access token (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"Timeout getting access token after {MAX_RETRIES} attempts")
                    
                except requests.exceptions.ConnectionError as e:
                    last_exception = e
                    error_str = str(e).lower()
                    is_dns_error = (
                        "nodename nor servname" in error_str or
                        "name or service not known" in error_str or
                        "failed to resolve" in error_str or
                        "getaddrinfo failed" in error_str or
                        "temporary failure in name resolution" in error_str
                    )

                    if attempt < MAX_RETRIES - 1:
                        wait_time = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                        error_type = "DNS resolution" if is_dns_error else "Connection"
                        logger.warning(f"{error_type} error getting access token (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        error_type = "DNS resolution" if is_dns_error else "Connection"
                        logger.error(f"{error_type} error getting access token after {MAX_RETRIES} attempts: {e}")
                    
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code in (400, 401, 403, 404, 422):
                        logger.error(f"Authentication/authorization error getting access token: {e}")
                        return None
                    last_exception = e
                    if attempt < MAX_RETRIES - 1:
                        wait_time = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                        logger.warning(f"HTTP error getting access token (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"HTTP error getting access token after {MAX_RETRIES} attempts: {e}")
                    
                except requests.exceptions.RequestException as e:
                    last_exception = e
                    if attempt < MAX_RETRIES - 1:
                        wait_time = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                        logger.warning(f"Request error getting access token (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"Request error getting access token after {MAX_RETRIES} attempts: {e}")

            if last_exception:
                logger.error(f"Failed to get access token after {MAX_RETRIES} attempts. Last error: {last_exception}")
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
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """
        Make API request with error handling and rate limiting.
        
        Args:
            endpoint: API endpoint path (without base URL)
            params: Optional query parameters
            
        Returns:
            Response JSON data, or None on error.
        """
        url = f"{self.base_url}/{endpoint}"
        headers = self.get_headers()
        
        if not headers:
            logger.error("Failed to get valid access token")
            return None
        
        # Retry logic for DNS/network errors with exponential backoff
        last_exception = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.get(url, headers=headers, params=params, timeout=30)
                
                # Handle rate limiting in the main retry loop and honor Hostaway's
                # Retry-After response when supplied.
                if response.status_code == 429:
                    if attempt >= MAX_RETRIES - 1:
                        response.raise_for_status()
                    retry_after = response.headers.get('Retry-After')
                    try:
                        wait_time = max(float(retry_after), 0.0) if retry_after else RATE_LIMIT_RETRY_DELAY
                    except (TypeError, ValueError):
                        wait_time = RATE_LIMIT_RETRY_DELAY
                    logger.warning(f"Rate limit exceeded for {endpoint}, retrying in {wait_time:g}s...")
                    time.sleep(wait_time)
                    continue

                # A token can be revoked before its advertised expiry. Refresh it
                # once instead of turning a valid sync into a silent empty result.
                if response.status_code in (401, 403) and attempt == 0:
                    self._invalidate_access_token(self._token_from_headers(headers))
                    headers = self.get_headers()
                    if not headers:
                        return None
                    continue
                
                response.raise_for_status()
                
                if attempt > 0:
                    logger.info(f"Successfully made request to {endpoint} after {attempt} retries")
                
                return response.json()
                
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ReadTimeout) as e:
                last_exception = e
                if attempt < MAX_RETRIES - 1:
                    wait_time = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    logger.warning(f"Timeout making request to {endpoint} (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Timeout making request to {endpoint} after {MAX_RETRIES} attempts")
                    
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                # Check if it's a DNS error (requests wraps socket.gaierror)
                error_str = str(e).lower()
                is_dns_error = (
                    "nodename nor servname" in error_str or
                    "name or service not known" in error_str or
                    "failed to resolve" in error_str or
                    "getaddrinfo failed" in error_str or
                    "temporary failure in name resolution" in error_str
                )
                
                if attempt < MAX_RETRIES - 1:
                    wait_time = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    error_type = "DNS resolution" if is_dns_error else "Connection"
                    logger.warning(f"{error_type} error making request to {endpoint} (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    error_type = "DNS resolution" if is_dns_error else "Connection"
                    logger.error(f"{error_type} error making request to {endpoint} after {MAX_RETRIES} attempts: {e}")
                    
            except requests.exceptions.HTTPError as e:
                # Don't retry client errors (4xx) except 429 which is handled above
                if e.response is not None and e.response.status_code in (400, 401, 403, 404, 422):
                    logger.error(f"HTTP error for {endpoint}: {e}")
                    if hasattr(e, 'response') and e.response is not None:
                        logger.debug(f"Response: {e.response.text[:200]}")
                    return None
                # For 5xx errors, urllib3 retry should handle it, but we'll also retry here
                last_exception = e
                if attempt < MAX_RETRIES - 1:
                    wait_time = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    logger.warning(f"HTTP error for {endpoint} (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"HTTP error for {endpoint} after {MAX_RETRIES} attempts: {e}")
                    if hasattr(e, 'response') and e.response is not None:
                        logger.debug(f"Response: {e.response.text[:200]}")
                        
            except requests.exceptions.RequestException as e:
                last_exception = e
                if attempt < MAX_RETRIES - 1:
                    wait_time = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    logger.warning(f"Request error for {endpoint} (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Request error for {endpoint} after {MAX_RETRIES} attempts: {e}")
        
        # All retries exhausted
        if last_exception:
            logger.error(f"Failed to make request to {endpoint} after {MAX_RETRIES} attempts. Last error: {last_exception}")
        return None

    def _make_post_request(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Make one non-retried JSON POST for an outbound, non-idempotent action."""
        url = f"{self.base_url}/{endpoint}"
        headers = self.get_headers()
        if not headers:
            raise RuntimeError("Hostaway authentication failed")

        try:
            response = self.session.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            status_suffix = f" ({status_code})" if status_code else ""
            if status_code in (401, 403):
                self._invalidate_access_token(self._token_from_headers(headers))
            logger.error("Hostaway POST failed for %s%s", endpoint, status_suffix)
            raise RuntimeError(f"Hostaway rejected the outbound action{status_suffix}") from exc

        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Hostaway returned an invalid response") from exc
        return data if isinstance(data, dict) else {'result': data}
    
    def get_listings_page(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Optional[List[Dict]]:
        """Return one listings page, preserving API failure as ``None``."""
        params: Dict[str, int] = {}
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset

        data = self._make_request("listings", params)
        if not isinstance(data, dict) or 'result' not in data:
            return None
        result = data['result']
        if not isinstance(result, list):
            logger.error("Hostaway listings response contained an invalid result payload")
            return None
        return result

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
        page = self.get_listings_page(limit=limit, offset=offset)
        return page if page is not None else []
    
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
                        limit: Optional[int] = None, 
                        offset: Optional[int] = None,
                        latest_activity_on: Optional[datetime] = None,
                        after_id: Optional[int] = None,
                        sort_order: Optional[str] = None) -> List[Dict]:
        """
        Get reservations with optional filters.
        
        Args:
            listing_id: Filter by listing ID
            limit: Maximum number of reservations to return
            offset: Number of reservations to skip
            latest_activity_on: Filter reservations with activity after this timestamp (ISO 8601 format)
            
        Returns:
            List of reservation dictionaries.
        """
        page = self.get_reservations_page(
            listing_id=listing_id,
            limit=limit,
            offset=offset,
            latest_activity_on=latest_activity_on,
            after_id=after_id,
            sort_order=sort_order,
        )
        return page if page is not None else []

    def get_reservations_page(self, listing_id: Optional[int] = None,
                              limit: Optional[int] = None,
                              offset: Optional[int] = None,
                              latest_activity_on: Optional[datetime] = None,
                              after_id: Optional[int] = None,
                              sort_order: Optional[str] = None) -> Optional[List[Dict]]:
        """Return one reservation page and preserve API failure as ``None``."""
        params: Dict[str, Any] = {}
        if listing_id:
            params['listingId'] = listing_id
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        if after_id:
            params['afterId'] = after_id
        if sort_order:
            params['sortOrder'] = sort_order
        if latest_activity_on:
            # The current Hostaway API accepts a date-only lower bound named
            # ``latestActivityStart``. Callers still apply an exact timestamp
            # filter locally when they need sub-day precision.
            params['latestActivityStart'] = latest_activity_on.strftime('%Y-%m-%d')
        
        data = self._make_request("reservations", params)
        if not isinstance(data, dict) or 'result' not in data:
            return None
        result = data['result']
        if not isinstance(result, list):
            logger.error("Hostaway reservations response contained an invalid result payload")
            return None
        return result
    
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
        after_id = None
        
        while True:
            reservations = self.get_reservations_page(limit=limit, after_id=after_id)
            if reservations is None:
                raise RuntimeError("Hostaway reservations pagination failed")
            if not reservations:
                break
            
            all_reservations.extend(reservations)
            
            # If we got fewer than the limit, we've reached the end
            if len(reservations) < limit:
                break
            
            after_id = reservations[-1].get('id')
            if not after_id:
                raise RuntimeError("Hostaway reservations page did not include a cursor ID")
        
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
        page = self.get_conversations_page(
            reservation_id=reservation_id,
            limit=limit,
            offset=offset,
        )
        return page if page is not None else []

    def get_conversations_page(self, reservation_id: Optional[int] = None,
                               limit: Optional[int] = None,
                               offset: Optional[int] = None) -> Optional[List[Dict]]:
        """Return one conversations page and preserve API failure as ``None``."""
        params: Dict[str, int] = {}
        if reservation_id:
            params['reservationId'] = reservation_id
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        
        data = self._make_request("conversations", params)
        if not isinstance(data, dict) or 'result' not in data:
            return None
        result = data['result']
        if not isinstance(result, list):
            logger.error("Hostaway conversations response contained an invalid result payload")
            return None
        return result
    
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
            conversations = self.get_conversations_page(limit=limit, offset=offset)
            if conversations is None:
                raise RuntimeError("Hostaway conversations pagination failed")
            if not conversations:
                break
            
            all_conversations.extend(conversations)
            
            # If we got fewer than the limit, we've reached the end
            if len(conversations) < limit:
                break
            
            offset += limit
        
        return all_conversations
    
    def get_conversation_messages(self, conversation_id: int,
                                  limit: Optional[int] = None,
                                  offset: Optional[int] = None) -> List[Dict]:
        """
        Get all messages for a specific conversation.
        
        Args:
            conversation_id: The conversation ID.
            
        Returns:
            List of message dictionaries.
        """
        page = self.get_conversation_messages_page(conversation_id, limit=limit, offset=offset)
        return page if page is not None else []

    def get_conversation_messages_page(self, conversation_id: int,
                                       limit: Optional[int] = None,
                                       offset: Optional[int] = None) -> Optional[List[Dict]]:
        """Return one message page and preserve API failure as ``None``."""
        params: Dict[str, int] = {}
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        data = self._make_request(f"conversations/{conversation_id}/messages", params)
        if not isinstance(data, dict) or 'result' not in data:
            return None
        result = data['result']
        if not isinstance(result, list):
            logger.error("Hostaway conversation messages response contained an invalid result payload")
            return None
        return result

    def get_all_conversation_messages(self, conversation_id: int, limit: int = 500) -> List[Dict]:
        """Fetch the complete sent-message history for one conversation."""
        messages: List[Dict] = []
        offset = 0
        while True:
            page = self.get_conversation_messages_page(
                conversation_id,
                limit=limit,
                offset=offset,
            )
            if page is None:
                raise RuntimeError(
                    f"Hostaway message pagination failed for conversation {conversation_id}"
                )
            if not page:
                break
            messages.extend(page)
            if len(page) < limit:
                break
            offset += limit
        return messages

    def send_conversation_message(
        self,
        conversation_id: int,
        body: str,
        communication_type: str = 'channel',
    ) -> Dict[str, Any]:
        """Send one message through an existing Hostaway conversation."""
        normalized_body = str(body or '').strip()
        if not normalized_body:
            raise ValueError('Message body is required')
        if not conversation_id:
            raise ValueError('Conversation ID is required')
        return self._make_post_request(
            f"conversations/{int(conversation_id)}/messages",
            {
                'body': normalized_body,
                'communicationType': communication_type or 'channel',
            },
        )
    
    def get_calendar(self, listing_id: int,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> List[Dict]:
        """
        Get calendar days for a listing.

        Args:
            listing_id: The listing ID.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.

        Returns:
            List of calendar day dicts with keys like date, isAvailable,
            status, price, minimumStay, maximumStay.
        """
        params: Dict[str, Any] = {}
        if start_date:
            params['startDate'] = start_date
        if end_date:
            params['endDate'] = end_date

        data = self._make_request(f"listings/{listing_id}/calendar", params)
        if data and 'result' in data:
            return data['result']
        return []

    @staticmethod
    def _add_indexed_params(params: Dict[str, Any], name: str, values: Optional[List[Any]]) -> None:
        """Encode Hostaway array query parameters as ``name[0]``, ``name[1]``..."""
        for index, value in enumerate(values or []):
            params[f'{name}[{index}]'] = value

    def get_reviews(self, listing_id: Optional[int] = None,
                    reservation_id: Optional[int] = None,
                    limit: Optional[int] = None,
                    offset: Optional[int] = None,
                    status: Optional[str] = None,
                    type: Optional[str] = None,
                    sortBy: Optional[str] = None,
                    order: Optional[str] = None,
                    statuses: Optional[List[str]] = None,
                    departure_date_start: Optional[str] = None,
                    departure_date_end: Optional[str] = None,
                    preview: bool = True) -> List[Dict]:
        """
        Get reviews with optional filters.
        
        Args:
            listing_id: Filter by listing ID
            reservation_id: Filter by reservation ID
            limit: Maximum number of reviews to return
            offset: Number of reviews to skip
            status: Filter by review status (e.g., 'Published')
            type: Filter by review type (e.g., 'guest-to-host', 'host-to-guest')
            sortBy: Sort field (e.g., 'departureDate', 'arrivalDate')
            order: Sort order ('asc' or 'desc')
            
        Returns:
            List of review dictionaries with sub-ratings.
        """
        page = self.get_reviews_page(
            listing_id=listing_id,
            reservation_id=reservation_id,
            limit=limit,
            offset=offset,
            status=status,
            type=type,
            sortBy=sortBy,
            order=order,
            statuses=statuses,
            departure_date_start=departure_date_start,
            departure_date_end=departure_date_end,
            preview=preview,
        )
        return page if page is not None else []

    def get_reviews_page(self, listing_id: Optional[int] = None,
                         reservation_id: Optional[int] = None,
                         limit: Optional[int] = None,
                         offset: Optional[int] = None,
                         status: Optional[str] = None,
                         type: Optional[str] = None,
                         sortBy: Optional[str] = None,
                         order: Optional[str] = None,
                         statuses: Optional[List[str]] = None,
                         departure_date_start: Optional[str] = None,
                         departure_date_end: Optional[str] = None,
                         preview: bool = True) -> Optional[List[Dict]]:
        """Return one review page using the current Hostaway parameter names."""
        params: Dict[str, Any] = {}
        if listing_id:
            self._add_indexed_params(params, 'listingMapIds', [listing_id])
        if reservation_id:
            params['reservationId'] = reservation_id
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        normalized_statuses = statuses or ([status] if status else None)
        self._add_indexed_params(params, 'statuses', normalized_statuses)
        if type:
            params['type'] = type
        if sortBy:
            params['sortBy'] = sortBy
        if order:
            params['sortOrder'] = order
        if departure_date_start:
            params['departureDateStart'] = departure_date_start
        if departure_date_end:
            params['departureDateEnd'] = departure_date_end
        if preview:
            params['preview'] = 'true'
        
        data = self._make_request("reviews", params)
        if not isinstance(data, dict) or 'result' not in data:
            return None
        result = data['result']
        if not isinstance(result, list):
            logger.error("Hostaway reviews response contained an invalid result payload")
            return None
        return result
