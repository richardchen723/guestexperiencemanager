#!/usr/bin/env python3
"""
Session management for user authentication.
"""

from flask import session
from dashboard.auth.models import get_user_by_id, update_last_login


def login_user(user_id: int):
    """Log in a user by setting session variables."""
    session['user_id'] = user_id
    session['logged_in'] = True
    session.permanent = False  # Session expires on browser close
    update_last_login(user_id)


def logout_user():
    """Log out the current user."""
    session.clear()


def get_current_user():
    """Get the current logged-in user."""
    if not session.get('logged_in'):
        return None
    
    user_id = session.get('user_id')
    if not user_id:
        return None
    
    return get_user_by_id(user_id)


def is_logged_in():
    """Check if a user is currently logged in."""
    return session.get('logged_in', False)


def is_approved():
    """Check if the current user is approved."""
    user = get_current_user()
    return user and user.is_approved if user else False


def is_admin():
    """Check if the current user is an admin or owner."""
    user = get_current_user()
    return user and user.is_admin() if user else False
