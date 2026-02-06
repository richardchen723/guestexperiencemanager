#!/usr/bin/env python3
"""
Generate an API key and store its hash in the database.
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

from dashboard.auth.api_keys import create_api_key
from dashboard.auth.models import get_or_create_service_user


def main() -> int:
    # Load .env from project root if present
    project_root = Path(__file__).resolve().parents[1]
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    parser = argparse.ArgumentParser(description="Generate a new API key.")
    parser.add_argument("--name", default="Third Party Key", help="Label for the API key")
    args = parser.parse_args()
    
    service_user = get_or_create_service_user()
    raw_key = create_api_key(name=args.name, created_by=service_user.user_id)
    
    print("API key created. Store this value securely; it will not be shown again.")
    print(raw_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
