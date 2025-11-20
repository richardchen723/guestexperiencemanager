#!/usr/bin/env python3
"""
Helper script to generate PostgreSQL connection strings.
Run this after setting up RDS to create your connection strings.
"""

import sys

print("\n" + "="*60)
print("PostgreSQL Connection String Generator")
print("="*60)
print("\nEnter your RDS connection details:\n")

# Get input
endpoint = input("RDS Endpoint (e.g., hostaway-db.xxxxx.us-east-1.rds.amazonaws.com): ").strip()
port = input("Port (default 5432): ").strip() or "5432"
username = input("Master Username: ").strip()
password = input("Master Password: ").strip()

if not all([endpoint, username, password]):
    print("\n✗ Error: All fields are required")
    sys.exit(1)

# Generate connection strings
print("\n" + "="*60)
print("Your Connection Strings")
print("="*60)
print("\nAdd these to your .env file:\n")

main_url = f"postgresql://{username}:{password}@{endpoint}:{port}/hostaway_main"
users_url = f"postgresql://{username}:{password}@{endpoint}:{port}/hostaway_users"
cache_url = f"postgresql://{username}:{password}@{endpoint}:{port}/hostaway_ai_cache"

print(f"DATABASE_URL={main_url}")
print(f"USERS_DATABASE_URL={users_url}")
print(f"CACHE_DATABASE_URL={cache_url}")

print("\n" + "="*60)
print("⚠ IMPORTANT: Keep these credentials secure!")
print("="*60)
print("\nNext steps:")
print("1. Add these to your .env file")
print("2. Make sure the 3 databases exist in RDS:")
print("   - hostaway_main")
print("   - hostaway_users")
print("   - hostaway_ai_cache")
print("3. Run: python3 scripts/test_setup.py")

