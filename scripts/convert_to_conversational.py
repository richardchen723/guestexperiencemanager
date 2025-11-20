#!/usr/bin/env python3
"""
Convert CSV conversations to AI-friendly conversational text format
"""

import csv
import os
from datetime import datetime


def convert_csv_to_conversational(csv_file_path, output_file_path):
    """Convert CSV conversation to conversational text format"""
    
    if not os.path.exists(csv_file_path):
        print(f"❌ File not found: {csv_file_path}")
        return
    
    print(f"Converting {csv_file_path} to conversational format...")
    
    # Read the CSV file
    messages = []
    with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            messages.append(row)
    
    print(f"Found {len(messages)} messages")
    
    # Create conversational format
    conversational_text = []
    
    # Add header information
    conversational_text.append("=" * 60)
    conversational_text.append("GUEST CONVERSATION")
    conversational_text.append("=" * 60)
    
    # Extract guest name and listing from file path
    file_name = os.path.basename(csv_file_path)
    guest_name = file_name.replace('.csv', '').split('_')[0:-1]  # Remove date part
    guest_name = ' '.join(guest_name)
    
    # Extract listing from directory path
    dir_path = os.path.dirname(csv_file_path)
    listing_name = os.path.basename(dir_path)
    
    conversational_text.append(f"Guest: {guest_name}")
    conversational_text.append(f"Listing: {listing_name}")
    conversational_text.append(f"Total Messages: {len(messages)}")
    conversational_text.append("")
    
    # Add messages in conversational format
    for i, message in enumerate(messages, 1):
        timestamp = message.get('timestamp', '')
        sender = message.get('sender', 'Unknown')
        content = message.get('content', '').strip()
        
        # Format timestamp nicely
        if timestamp:
            try:
                dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M')
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
    
    # Write to output file
    with open(output_file_path, 'w', encoding='utf-8') as f:
        f.write(full_text)
    
    print(f"✅ Conversational format saved to: {output_file_path}")
    
    # Show preview
    print("\n" + "=" * 60)
    print("PREVIEW (First 20 lines):")
    print("=" * 60)
    lines = full_text.split('\n')
    for line in lines[:20]:
        print(line)
    if len(lines) > 20:
        print(f"... ({len(lines) - 20} more lines)")


def main():
    """Convert Lauren's conversation as a test"""
    csv_file = "conversations/_Blue Haven_ Iconic Lakefront 4 Season Retreat/Lauren Walker_2025-09-26.csv"
    output_file = "conversations/_Blue Haven_ Iconic Lakefront 4 Season Retreat/Lauren Walker_2025-09-26_conversational.txt"
    
    convert_csv_to_conversational(csv_file, output_file)


if __name__ == "__main__":
    main()

