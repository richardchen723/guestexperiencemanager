#!/usr/bin/env python3
"""
Script to move all combined conversation files from property subfolders 
to the main conversations folder.
"""

import os
import shutil
import glob

def move_combined_files():
    """Move all combined conversation files to the main conversations folder"""
    
    conversations_dir = "conversations"
    
    if not os.path.exists(conversations_dir):
        print(f"❌ Conversations directory '{conversations_dir}' not found")
        return
    
    # Find all combined files
    combined_files = glob.glob(os.path.join(conversations_dir, "*", "*_all_conversations.txt"))
    
    if not combined_files:
        print("❌ No combined conversation files found")
        return
    
    print(f"Found {len(combined_files)} combined files to move")
    
    moved_count = 0
    
    for file_path in combined_files:
        try:
            # Get the filename
            filename = os.path.basename(file_path)
            
            # Create destination path in main conversations folder
            dest_path = os.path.join(conversations_dir, filename)
            
            # Move the file
            shutil.move(file_path, dest_path)
            
            print(f"✅ Moved: {filename}")
            moved_count += 1
            
        except Exception as e:
            print(f"❌ Error moving {file_path}: {e}")
    
    print(f"\n🎉 Successfully moved {moved_count} combined files to main conversations folder")

def main():
    """Main function"""
    print("Moving Combined Conversation Files")
    print("=" * 40)
    move_combined_files()

if __name__ == "__main__":
    main()

