"""
Vercel serverless function entry point for Flask application.
This file is required by Vercel to properly detect and deploy the Python runtime.
"""

import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Import the Flask app from dashboard/app.py
# Vercel's @vercel/python builder automatically wraps WSGI apps
from dashboard.app import app

