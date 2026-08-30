#!/usr/bin/env python3
"""
Authentication decorators for route protection.
"""

from functools import wraps
from flask import current_app, g, redirect, render_template, url_for, request, jsonify
import dashboard.config as config
from dashboard.auth.api_keys import authenticate_request_api_key
from dashboard.auth.features import feature_label, first_accessible_endpoint, user_can_access_feature
from dashboard.auth.session import get_current_user, is_logged_in, is_approved, is_admin


def login_required(f):
    """Decorator to require user login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            if request.is_json or _is_api_request():
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def approved_required(f):
    """Decorator to require approved user account."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Allow API key auth for API routes
        if _is_api_request():
            if authenticate_request_api_key():
                return f(*args, **kwargs)
            scope_error = _api_key_scope_error()
            if scope_error is not None:
                return scope_error
        
        if not is_logged_in():
            if request.is_json or _is_api_request():
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login'))
        
        if not is_approved():
            if request.is_json or _is_api_request():
                return jsonify({'error': 'Account approval required'}), 403
            return redirect(url_for('auth.pending_approval'))
        
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin or owner role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Allow API key auth for API routes
        if _is_api_request():
            if authenticate_request_api_key():
                return f(*args, **kwargs)
            scope_error = _api_key_scope_error()
            if scope_error is not None:
                return scope_error
        
        if not is_logged_in():
            if request.is_json or _is_api_request():
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login'))
        
        if not is_approved():
            if request.is_json or _is_api_request():
                return jsonify({'error': 'Account approval required'}), 403
            return redirect(url_for('auth.pending_approval'))
        
        if not is_admin():
            if request.is_json or _is_api_request():
                return jsonify({'error': 'Admin access required'}), 403
            user = get_current_user()
            configured_endpoint = current_app.config.get('POST_LOGIN_ENDPOINT')
            landing_endpoint = (
                configured_endpoint
                if configured_endpoint and configured_endpoint != 'dashboard.dashboard_page'
                else first_accessible_endpoint(user)
            )
            return redirect(url_for(landing_endpoint))
        
        return f(*args, **kwargs)
    return decorated_function


def user_is_api_key_owner(user) -> bool:
    """Return whether a user matches the configured API-key owner email."""
    owner_email = (config.OWNER_EMAIL or '').strip().lower()
    user_email = (getattr(user, 'email', '') or '').strip().lower()
    return bool(owner_email and user_email == owner_email)


def owner_email_required(f):
    """Require the configured owner to be signed in with their user session.

    API-key authentication is deliberately not accepted here. Otherwise an
    existing API key could be used to create or delete other credentials.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            if request.is_json or _is_api_request():
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login'))

        if not is_approved():
            if request.is_json or _is_api_request():
                return jsonify({'error': 'Account approval required'}), 403
            return redirect(url_for('auth.pending_approval'))

        user = get_current_user()
        if not user_is_api_key_owner(user):
            if request.is_json or _is_api_request():
                return jsonify({'error': 'Owner access required'}), 403
            return render_template(
                'auth/feature_denied.html',
                feature_name='API key management',
                landing_endpoint=first_accessible_endpoint(user),
            ), 403

        return f(*args, **kwargs)
    return decorated_function


def check_feature_access(feature_key: str):
    """Return an error response when the current request cannot use a feature."""
    if _is_api_request():
        if authenticate_request_api_key():
            return None
        scope_error = _api_key_scope_error()
        if scope_error is not None:
            return scope_error

    if not is_logged_in():
        if request.is_json or _is_api_request():
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('auth.login'))

    if not is_approved():
        if request.is_json or _is_api_request():
            return jsonify({'error': 'Account approval required'}), 403
        return redirect(url_for('auth.pending_approval'))

    user = get_current_user()
    if not user_can_access_feature(user, feature_key):
        label = feature_label(feature_key)
        if request.is_json or _is_api_request():
            return jsonify({
                'error': f'{label} access required',
                'feature': feature_key,
            }), 403
        return render_template(
            'auth/feature_denied.html',
            feature_name=label,
            landing_endpoint=first_accessible_endpoint(user),
        ), 403

    return None


def feature_required(feature_key: str):
    """Decorator requiring an approved account with access to one feature."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            denied_response = check_feature_access(feature_key)
            if denied_response is not None:
                return denied_response
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def check_user_access():
    """Helper function to check user access for route protection."""
    if not is_logged_in():
        return False, 'Authentication required'
    
    if not is_approved():
        return False, 'Account approval required'
    
    return True, None


def _is_api_request() -> bool:
    """Return True when the current request targets an API endpoint."""
    path = request.path or ""
    return "/api/" in path or path.endswith("/api")


def _api_key_scope_error():
    """Return a stable denial when a valid key lacks legacy full access."""
    required_scope = getattr(g, 'api_key_scope_denied', None)
    if not required_scope:
        return None
    return jsonify({
        'error': 'API key scope does not allow this operation',
        'required_scope': required_scope,
    }), 403
