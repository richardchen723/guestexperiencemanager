#!/usr/bin/env python3
"""
Authentication routes.
"""

import sys
import os
from flask import Blueprint, render_template, redirect, url_for

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.auth.session import logout_user, get_current_user
from dashboard.auth.decorators import login_required
from dashboard.auth.oauth import handle_google_callback

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


@auth_bp.route('/login')
def login():
    """Login page - redirects to Google OAuth."""
    if get_current_user():
        user = get_current_user()
        if user and user.is_approved:
            return redirect(url_for('index'))
        elif user:
            return redirect(url_for('auth.pending_approval'))
    
    # Redirect to Google OAuth
    try:
        from flask_dance.contrib.google import google
        if not google.authorized:
            return redirect(url_for('google.login'))
        else:
            # Already authorized, handle callback
            return handle_google_callback()
    except Exception as e:
        # If Google OAuth is not configured, show error
        return f"Google OAuth not configured. Error: {e}", 500


@auth_bp.route('/google/callback')
def google_callback():
    """Google OAuth callback handler."""
    return handle_google_callback()


@auth_bp.route('/logout')
@login_required
def logout():
    """Logout the current user."""
    logout_user()
    return redirect(url_for('auth.login'))


@auth_bp.route('/pending-approval')
@login_required
def pending_approval():
    """Pending approval page for unapproved users."""
    user = get_current_user()
    if user and user.is_approved:
        return redirect(url_for('index'))
    
    return render_template('auth/pending.html', current_user=user)


def register_auth_routes(app):
    """Register authentication routes with the Flask app."""
    app.register_blueprint(auth_bp)
