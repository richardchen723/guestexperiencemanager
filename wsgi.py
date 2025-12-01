#!/usr/bin/env python3
"""
WSGI entry point for Gunicorn.
"""
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from dashboard.app import create_app

# Create the application instance
application = create_app()

if __name__ == "__main__":
    application.run()

