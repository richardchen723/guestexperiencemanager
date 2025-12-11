#!/usr/bin/env python3
"""
Authentication decorators for route protection.
"""

from functools import wraps
from flask import redirect, url_for, request, jsonify
from dashboard.auth.session import get_current_user, is_logged_in, is_approved, is_admin


def login_required(f):
    """Decorator to require user login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def approved_required(f):
    """Decorator to require approved user account."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login'))
        
        if not is_approved():
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Account approval required'}), 403
            return redirect(url_for('auth.pending_approval'))
        
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin or owner role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login'))
        
        if not is_approved():
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Account approval required'}), 403
            return redirect(url_for('auth.pending_approval'))
        
        if not is_admin():
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('dashboard.dashboard_page'))
        
        return f(*args, **kwargs)
    return decorated_function


def check_user_access():
    """Helper function to check user access for route protection."""
    if not is_logged_in():
        return False, 'Authentication required'
    
    if not is_approved():
        return False, 'Account approval required'
    
    return True, None
