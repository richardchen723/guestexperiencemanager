#!/usr/bin/env python3
"""
Test script to verify AWS RDS and S3 setup.
Run this after configuring environment variables to test connections.
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_database_connection():
    """Test database connection."""
    print("\n" + "="*60)
    print("Testing Database Connections")
    print("="*60)
    
    # Test main database
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("✗ DATABASE_URL not set")
        return False
    
    try:
        from database.models import get_engine, init_models
        print(f"Connecting to: {db_url.split('@')[1] if '@' in db_url else 'unknown'}")
        engine = get_engine(db_url)
        init_models(db_url)
        print("✓ Main database connection successful!")
    except Exception as e:
        print(f"✗ Main database connection failed: {e}")
        return False
    
    # Test users database
    users_url = os.getenv("USERS_DATABASE_URL")
    if users_url:
        try:
            from dashboard.auth.models import get_engine as get_users_engine, init_user_database
            print(f"Connecting to users database...")
            init_user_database()
            print("✓ Users database connection successful!")
        except Exception as e:
            print(f"✗ Users database connection failed: {e}")
            return False
    else:
        print("⚠ USERS_DATABASE_URL not set (using SQLite fallback)")
    
    return True

def test_s3_connection():
    """Test S3 connection."""
    print("\n" + "="*60)
    print("Testing S3 Connection")
    print("="*60)
    
    bucket_name = os.getenv("AWS_S3_BUCKET_NAME")
    if not bucket_name:
        print("✗ AWS_S3_BUCKET_NAME not set")
        return False
    
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    
    if not access_key or not secret_key:
        print("✗ AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not set")
        return False
    
    try:
        from utils.s3_storage import S3Storage
        s3 = S3Storage()
        print(f"✓ S3 connection successful!")
        print(f"  Bucket: {s3.bucket_name}")
        print(f"  Region: {s3.region}")
        print(f"  Prefix: {s3.prefix}")
        
        # Try to list files (test read access)
        files = s3.list_conversations()
        print(f"  Found {len(files)} existing files in S3")
        
        return True
    except Exception as e:
        print(f"✗ S3 connection failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_required_env_vars():
    """Check if all required environment variables are set."""
    print("\n" + "="*60)
    print("Checking Required Environment Variables")
    print("="*60)
    
    required_vars = {
        "Database": [
            "DATABASE_URL",
            "USERS_DATABASE_URL",
            "CACHE_DATABASE_URL"
        ],
        "AWS S3": [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_S3_BUCKET_NAME",
            "AWS_S3_REGION"
        ],
        "Hostaway API": [
            "HOSTAWAY_ACCOUNT_ID",
            "HOSTAWAY_API_KEY"
        ],
        "OpenAI": [
            "OPENAI_API_KEY"
        ],
        "Flask": [
            "SECRET_KEY"
        ]
    }
    
    all_set = True
    for category, vars_list in required_vars.items():
        print(f"\n{category}:")
        for var in vars_list:
            value = os.getenv(var)
            if value:
                # Mask sensitive values
                if "KEY" in var or "SECRET" in var or "PASSWORD" in var:
                    masked = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
                    print(f"  ✓ {var}: {masked}")
                else:
                    print(f"  ✓ {var}: {value}")
            else:
                print(f"  ✗ {var}: NOT SET")
                all_set = False
    
    return all_set

def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("Hostaway Messages - Setup Verification")
    print("="*60)
    
    # Load environment variables from .env file
    from dotenv import load_dotenv
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✓ Loaded .env file from {env_path}")
    else:
        print("⚠ No .env file found - using system environment variables")
    
    # Check required variables
    vars_ok = test_required_env_vars()
    
    if not vars_ok:
        print("\n⚠ Some required environment variables are missing.")
        print("Please set them before continuing.")
        return
    
    # Test database
    db_ok = test_database_connection()
    
    # Test S3
    s3_ok = test_s3_connection()
    
    # Summary
    print("\n" + "="*60)
    print("Summary")
    print("="*60)
    print(f"Environment Variables: {'✓' if vars_ok else '✗'}")
    print(f"Database Connection: {'✓' if db_ok else '✗'}")
    print(f"S3 Connection: {'✓' if s3_ok else '✗'}")
    
    if vars_ok and db_ok and s3_ok:
        print("\n🎉 All tests passed! Your setup is ready for deployment.")
    else:
        print("\n⚠ Some tests failed. Please fix the issues above.")
        sys.exit(1)

if __name__ == "__main__":
    main()

