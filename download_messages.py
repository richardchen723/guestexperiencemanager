#!/usr/bin/env python3
"""
Hostaway Messages Downloader

A simple script to download all guest conversations from Hostaway
and organize them into local folders by listing and guest.

Usage:
    python download_messages.py

Requirements:
    - Hostaway API key (set in config.py)
    - requests library (install with: pip install -r requirements.txt)
"""

import os
import csv
import json
import requests
from datetime import datetime
from typing import List, Dict, Optional
from config import HOSTAWAY_API_KEY, HOSTAWAY_ACCOUNT_ID, HOSTAWAY_BASE_URL, DOWNLOAD_ATTACHMENTS, VERBOSE


class HostawayClient:
    """Simple client for Hostaway API"""
    
    def __init__(self, account_id: str, api_key: str, base_url: str):
        self.account_id = account_id
        self.api_key = api_key
        self.base_url = base_url
        self.access_token = None
        self.token_expires_at = None
    
    def get_access_token(self) -> Optional[str]:
        """Get OAuth 2.0 access token"""
        if self.access_token and self.token_expires_at and datetime.now().timestamp() < self.token_expires_at:
            return self.access_token
        
        if VERBOSE:
            print("Getting access token...")
        
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
            response = requests.post(url, headers=headers, data=data)
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get('access_token')
            
            # Set token expiration (typically 1 hour)
            expires_in = token_data.get('expires_in', 3600)
            self.token_expires_at = datetime.now().timestamp() + expires_in - 60  # 1 minute buffer
            
            return self.access_token
        except requests.exceptions.RequestException as e:
            print(f"Error getting access token: {e}")
            return None
    
    def get_headers(self) -> Dict[str, str]:
        """Get headers with valid access token"""
        token = self.get_access_token()
        if not token:
            return {}
        
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make API request with error handling"""
        url = f"{self.base_url}/{endpoint}"
        headers = self.get_headers()
        if not headers:
            print("Failed to get valid access token")
            return None
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making request to {endpoint}: {e}")
            return None
    
    def get_listings(self) -> List[Dict]:
        """Get all listings"""
        if VERBOSE:
            print("Fetching listings...")
        
        data = self._make_request("listings")
        if data and 'result' in data:
            listings = data['result']
            if VERBOSE:
                print(f"Found {len(listings)} listings")
            return listings
        return []
    
    def get_reservations(self, listing_id: str) -> List[Dict]:
        """Get reservations for a specific listing"""
        params = {
            "listingId": listing_id,
            "status": "confirmed"
        }
        
        data = self._make_request("reservations", params)
        if data and 'result' in data:
            return data['result']
        return []
    
    def get_conversations(self, reservation_id: str) -> List[Dict]:
        """Get conversations for a specific reservation"""
        params = {
            "reservationId": reservation_id
        }
        
        data = self._make_request("conversations", params)
        if data and 'result' in data:
            return data['result']
        return []
    
    def get_conversation_messages(self, conversation_id: str) -> List[Dict]:
        """Get all messages for a specific conversation"""
        data = self._make_request(f"conversations/{conversation_id}/messages")
        if data and 'result' in data:
            return data['result']
        return []


class MessageOrganizer:
    """Organizes messages into folder structure"""
    
    def __init__(self, base_dir: str = "conversations"):
        self.base_dir = base_dir
        self.ensure_base_directory()
    
    def ensure_base_directory(self):
        """Create base conversations directory"""
        os.makedirs(self.base_dir, exist_ok=True)
    
    def sanitize_filename(self, name: str) -> str:
        """Sanitize filename by removing/replacing invalid characters"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        return name.strip()
    
    def format_checkin_date(self, checkin_date: str) -> str:
        """Format check-in date for filename"""
        try:
            # Try to parse the date and format it
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
                         checkin_date: str, messages: List[Dict]) -> str:
        """Save conversation to conversational text format"""
        # Sanitize names for filesystem
        safe_listing_name = self.sanitize_filename(listing_name)
        safe_guest_name = self.sanitize_filename(guest_name)
        formatted_date = self.format_checkin_date(checkin_date)
        
        # Create listing directory
        listing_dir = os.path.join(self.base_dir, safe_listing_name)
        os.makedirs(listing_dir, exist_ok=True)
        
        # Create filename
        filename = f"{safe_guest_name}_{formatted_date}_conversation.txt"
        filepath = os.path.join(listing_dir, filename)
        
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
        
        # Join all lines and write to file
        full_text = '\n'.join(conversational_text)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_text)
        
        return filepath


def main():
    """Main function to download and organize messages"""
    print("Hostaway Messages Downloader")
    print("=" * 40)
    
    # Check if credentials are set
    if HOSTAWAY_ACCOUNT_ID == "your_account_id_here":
        print("ERROR: Please set your Hostaway Account ID in config.py")
        print("Get your Account ID from: https://dashboard.hostaway.com/settings/api")
        return
    
    if HOSTAWAY_API_KEY == "your_api_key_here":
        print("ERROR: Please set your Hostaway API key in config.py")
        print("Get your API key from: https://dashboard.hostaway.com/settings/api")
        return
    
    # Initialize client and organizer
    client = HostawayClient(HOSTAWAY_ACCOUNT_ID, HOSTAWAY_API_KEY, HOSTAWAY_BASE_URL)
    organizer = MessageOrganizer()
    
    # Get all listings
    listings = client.get_listings()
    if not listings:
        print("No listings found or error fetching listings")
        return
    
    total_conversations = 0
    total_messages = 0
    
    # Process each listing
    for listing in listings:
        listing_id = listing.get('id')
        listing_name = listing.get('name', f"Listing_{listing_id}")
        
        if VERBOSE:
            print(f"\nProcessing listing: {listing_name}")
        
        # Get reservations for this listing
        reservations = client.get_reservations(listing_id)
        if not reservations:
            if VERBOSE:
                print(f"  No reservations found for {listing_name}")
            continue
        
        if VERBOSE:
            print(f"  Found {len(reservations)} reservations")
        
        # Process each reservation
        for reservation in reservations:
            reservation_id = reservation.get('id')
            guest_name = reservation.get('guestName', 'Unknown_Guest')
            checkin_date = reservation.get('arrivalDate', '')  # Use arrivalDate instead of checkInDate
            
            if VERBOSE:
                print(f"    Processing guest: {guest_name} (Check-in: {checkin_date})")
            
            # Get conversations for this reservation
            conversations = client.get_conversations(reservation_id)
            if not conversations:
                if VERBOSE:
                    print(f"      No conversations found")
                continue
            
            # Extract messages from conversations using the correct endpoint
            all_messages = []
            for conversation in conversations:
                conversation_id = conversation.get('id')
                if VERBOSE:
                    print(f"      Getting messages for conversation {conversation_id}")
                
                # Get ALL messages for this conversation using the correct endpoint
                conversation_messages = client.get_conversation_messages(conversation_id)
                if VERBOSE:
                    print(f"      Found {len(conversation_messages)} messages in conversation")
                
                for msg in conversation_messages:
                    # Determine sender name based on isIncoming field
                    is_incoming = msg.get('isIncoming', False)
                    
                    if is_incoming:
                        # This is a message from the guest
                        sender = guest_name
                    else:
                        # This is a message from the host
                        # Check if it's automated based on other fields
                        communication_type = msg.get('communicationType', '')
                        if communication_type == 'automation' or 'automation' in str(msg.get('messageSource', '')).lower():
                            sender = 'Host (Automated)'
                        else:
                            sender = 'Host'
                    
                    # Extract message data in the format we need
                    message_data = {
                        'createdAt': msg.get('date', ''),
                        'messageType': 'incoming' if is_incoming else 'outgoing',
                        'sender': sender,
                        'content': msg.get('body', '')
                    }
                    all_messages.append(message_data)
            
            if not all_messages:
                if VERBOSE:
                    print(f"      No messages found in conversations")
                continue
            
            # Save conversation
            filepath = organizer.save_conversation(
                listing_name, guest_name, checkin_date, all_messages
            )
            
            total_conversations += 1
            total_messages += len(all_messages)
            
            if VERBOSE:
                print(f"      Saved {len(all_messages)} messages to: {filepath}")
    
    print(f"\nDownload complete!")
    print(f"Total conversations saved: {total_conversations}")
    print(f"Total messages downloaded: {total_messages}")
    print(f"Files saved in: {organizer.base_dir}/")


if __name__ == "__main__":
    main()
