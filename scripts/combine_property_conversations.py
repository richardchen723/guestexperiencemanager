#!/usr/bin/env python3
"""
Script to combine all conversation files for each property into a single file.
Each property will have one combined file with all guest conversations.
"""

import os
import glob
from pathlib import Path

def combine_property_conversations():
    """Combine all conversation files for each property into one file"""
    
    conversations_dir = "conversations"
    
    if not os.path.exists(conversations_dir):
        print(f"❌ Conversations directory '{conversations_dir}' not found")
        return
    
    # Get all property directories
    property_dirs = [d for d in os.listdir(conversations_dir) 
                    if os.path.isdir(os.path.join(conversations_dir, d)) and not d.startswith('.')]
    
    print(f"Found {len(property_dirs)} properties to process")
    
    total_combined = 0
    
    for property_dir in property_dirs:
        property_path = os.path.join(conversations_dir, property_dir)
        
        # Find all conversation files in this property directory
        conversation_files = glob.glob(os.path.join(property_path, "*_conversation.txt"))
        
        if not conversation_files:
            print(f"⚠️  No conversation files found in {property_dir}")
            continue
        
        # Sort files by guest name for consistent ordering
        conversation_files.sort()
        
        print(f"\n📁 Processing {property_dir}")
        print(f"   Found {len(conversation_files)} conversation files")
        
        # Create combined file path
        combined_filename = f"{property_dir}_all_conversations.txt"
        combined_filepath = os.path.join(property_path, combined_filename)
        
        # Combine all conversation files
        with open(combined_filepath, 'w', encoding='utf-8') as combined_file:
            # Write header for the combined file
            combined_file.write("=" * 80 + "\n")
            combined_file.write(f"ALL CONVERSATIONS - {property_dir.upper()}\n")
            combined_file.write("=" * 80 + "\n")
            combined_file.write(f"Total Guest Conversations: {len(conversation_files)}\n")
            combined_file.write("=" * 80 + "\n\n")
            
            # Process each conversation file
            for i, conv_file in enumerate(conversation_files, 1):
                try:
                    with open(conv_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # Add separator between conversations
                    if i > 1:
                        combined_file.write("\n" + "=" * 80 + "\n")
                        combined_file.write("NEXT CONVERSATION\n")
                        combined_file.write("=" * 80 + "\n\n")
                    
                    # Write the conversation content
                    combined_file.write(content)
                    
                except Exception as e:
                    print(f"   ⚠️  Error reading {conv_file}: {e}")
                    continue
        
        print(f"   ✅ Combined into: {combined_filename}")
        total_combined += 1
    
    print(f"\n🎉 Successfully combined conversations for {total_combined} properties")
    print(f"Combined files saved in: {conversations_dir}/")

def main():
    """Main function"""
    print("Property Conversation Combiner")
    print("=" * 40)
    combine_property_conversations()

if __name__ == "__main__":
    main()

