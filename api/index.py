#!/usr/bin/env python3
"""Vercel serverless function entry point."""
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from dashboard.app import create_app

app = create_app()

