#!/usr/bin/env python3
"""
Authentication routes.
"""

import sys
import os
import re
import logging
import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from flask import Blueprint, render_template, redirect, url_for, jsonify, request, current_app, session

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.auth.session import logout_user, get_current_user
from dashboard.auth.decorators import login_required, approved_required
from dashboard.auth.oauth import handle_google_callback
from dashboard.auth.models import (
    delete_google_drive_credential_for_user,
    get_google_drive_credential_for_user,
    get_session,
    save_google_drive_credential,
)
import dashboard.config as config

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')
logger = logging.getLogger(__name__)

GOOGLE_DRIVE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
)
GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_DRIVE_OAUTH_STATE_KEY = "google_drive_oauth_state"
GOOGLE_DRIVE_OAUTH_NEXT_KEY = "google_drive_oauth_next"


def _safe_next_path(candidate: str = None) -> str:
    if not candidate:
        return url_for('bookkeeping.bookkeeping_page')
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/'):
        return url_for('bookkeeping.bookkeeping_page')
    return candidate


def _append_query_value(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[key] = value
    return urlunparse(parsed._replace(query=urlencode(query)))


def _google_drive_status_payload(user) -> dict:
    credential = get_google_drive_credential_for_user(user.user_id) if user else None
    service_account_configured = bool(
        config.GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE or config.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON
    )
    mode = 'user_authorized' if credential else ('service_account' if service_account_configured else 'not_connected')
    display_name = None
    if credential:
        display_name = (getattr(user, 'name', None) or getattr(credential, 'google_email', None) or getattr(user, 'email', None))
    return {
        'connected': bool(credential),
        'mode': mode,
        'effective_mode': mode,
        'google_email': getattr(credential, 'google_email', None),
        'display_name': display_name,
        'expires_at': credential.expires_at.isoformat() if credential and credential.expires_at else None,
        'has_refresh_token': bool(getattr(credential, 'refresh_token', None)),
        'scopes': credential.scopes() if credential else [],
        'service_account_fallback_available': service_account_configured,
        'root_folder_id': config.GOOGLE_DRIVE_BOOKKEEPING_ROOT_FOLDER_ID,
        'connect_url': url_for('auth.google_drive_connect', next=url_for('bookkeeping.bookkeeping_page')),
    }


@auth_bp.route('/login')
def login():
    """Login page - redirects to Google OAuth."""
    if get_current_user():
        user = get_current_user()
        if user and user.is_approved:
            return redirect(url_for('dashboard.dashboard_page'))
        elif user:
            return redirect(url_for('auth.pending_approval'))
    
    # If the Google blueprint was not registered (missing dependency/config), fail with clear guidance.
    if 'google' not in current_app.blueprints:
        return (
            "Google OAuth is unavailable. Install dashboard dependencies "
            "(including Flask-Dance) and configure GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.",
            503
        )

    # Redirect to Google OAuth
    try:
        from flask_dance.contrib.google import google
        if not google.authorized:
            return redirect(url_for('google.login'))
        else:
            # Already authorized, handle callback
            return handle_google_callback()
    except ImportError:
        return (
            "Google OAuth dependency missing (Flask-Dance). "
            "Install dashboard requirements and restart the app.",
            503
        )
    except Exception as e:
        logger.error(f"Error during Google OAuth login redirect: {e}", exc_info=True)
        return "Google OAuth login failed. Please contact admin.", 500


@auth_bp.route('/google/callback')
def google_callback():
    """Google OAuth callback handler."""
    return handle_google_callback()


@auth_bp.route('/logout')
@login_required
def logout():
    """Logout the current user."""
    user = get_current_user()
    user_id = user.user_id if user else None
    
    # Log logout activity before logging out
    if user_id:
        try:
            from dashboard.activities.logger import log_auth_activity
            log_auth_activity(
                user_id=user_id,
                action='logout',
                metadata={
                    'ip_address': request.remote_addr if request else None,
                    'user_agent': request.headers.get('User-Agent') if request else None
                }
            )
        except Exception as e:
            # Log but don't fail logout if activity logging fails
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error logging logout activity: {e}", exc_info=True)
    
    logout_user()
    return redirect(url_for('auth.login'))


@auth_bp.route('/pending-approval')
@login_required
def pending_approval():
    """Pending approval page for unapproved users."""
    user = get_current_user()
    if user and user.is_approved:
        return redirect(url_for('dashboard.dashboard_page'))
    
    return render_template('auth/pending.html', current_user=user)


@auth_bp.route('/profile')
@approved_required
def profile_page():
    """User profile page."""
    return render_template('auth/profile.html', current_user=get_current_user())


@auth_bp.route('/google-drive/connect')
@approved_required
def google_drive_connect():
    """Begin a user-authorized Google Drive OAuth flow."""
    user = get_current_user()
    if not user:
        return redirect(url_for('auth.login'))
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        return "Google OAuth is not configured for Drive access.", 503

    state = secrets.token_urlsafe(32)
    next_path = _safe_next_path(request.args.get('next'))
    session[GOOGLE_DRIVE_OAUTH_STATE_KEY] = state
    session[GOOGLE_DRIVE_OAUTH_NEXT_KEY] = next_path

    query = {
        'client_id': config.GOOGLE_CLIENT_ID,
        'redirect_uri': url_for('auth.google_drive_callback_authorized', _external=True),
        'response_type': 'code',
        'scope': ' '.join(GOOGLE_DRIVE_OAUTH_SCOPES),
        'access_type': 'offline',
        'include_granted_scopes': 'true',
        'prompt': 'consent',
        'state': state,
        'login_hint': user.email,
    }
    return redirect(f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode(query)}")


@auth_bp.route('/google-drive/callback')
@approved_required
def google_drive_callback_authorized():
    """Complete the user-authorized Google Drive OAuth flow."""
    user = get_current_user()
    if not user:
        return redirect(url_for('auth.login'))

    next_path = _safe_next_path(session.pop(GOOGLE_DRIVE_OAUTH_NEXT_KEY, None))
    if request.args.get('error'):
        error_target = _append_query_value(next_path, 'driveAuth', 'error')
        error_target = _append_query_value(error_target, 'driveAuthMessage', request.args.get('error'))
        return redirect(error_target)

    expected_state = session.pop(GOOGLE_DRIVE_OAUTH_STATE_KEY, None)
    actual_state = request.args.get('state')
    if not expected_state or not actual_state or expected_state != actual_state:
        error_target = _append_query_value(next_path, 'driveAuth', 'error')
        error_target = _append_query_value(error_target, 'driveAuthMessage', 'state_mismatch')
        return redirect(error_target)

    code = (request.args.get('code') or '').strip()
    if not code:
        error_target = _append_query_value(next_path, 'driveAuth', 'error')
        error_target = _append_query_value(error_target, 'driveAuthMessage', 'missing_code')
        return redirect(error_target)

    try:
        token_response = requests.post(
            GOOGLE_OAUTH_TOKEN_URL,
            data={
                'code': code,
                'client_id': config.GOOGLE_CLIENT_ID,
                'client_secret': config.GOOGLE_CLIENT_SECRET,
                'redirect_uri': url_for('auth.google_drive_callback_authorized', _external=True),
                'grant_type': 'authorization_code',
            },
            timeout=30,
        )
        try:
            token_payload = token_response.json()
        except ValueError:
            token_payload = {'raw': token_response.text}
        if not token_response.ok or not token_payload.get('access_token'):
            logger.error("Google Drive OAuth token exchange failed: %s", token_payload)
            error_target = _append_query_value(next_path, 'driveAuth', 'error')
            error_target = _append_query_value(error_target, 'driveAuthMessage', 'token_exchange_failed')
            return redirect(error_target)

        access_token = token_payload.get('access_token')
        userinfo_response = requests.get(
            GOOGLE_OAUTH_USERINFO_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=30,
        )
        try:
            userinfo_payload = userinfo_response.json()
        except ValueError:
            userinfo_payload = {'raw': userinfo_response.text}
        if not userinfo_response.ok:
            logger.error("Google Drive OAuth userinfo fetch failed: %s", userinfo_payload)
            error_target = _append_query_value(next_path, 'driveAuth', 'error')
            error_target = _append_query_value(error_target, 'driveAuthMessage', 'userinfo_failed')
            return redirect(error_target)

        google_email = (userinfo_payload.get('email') or '').strip().lower()
        if google_email and google_email != (user.email or '').strip().lower():
            error_target = _append_query_value(next_path, 'driveAuth', 'error')
            error_target = _append_query_value(error_target, 'driveAuthMessage', 'email_mismatch')
            return redirect(error_target)

        expires_at = None
        expires_in = token_payload.get('expires_in')
        try:
            if expires_in is not None:
                expires_at = datetime.utcnow() + timedelta(seconds=max(0, int(expires_in) - 30))
        except (TypeError, ValueError):
            expires_at = None

        scopes = [value for value in (token_payload.get('scope') or '').split() if value]
        if not scopes:
            scopes = list(GOOGLE_DRIVE_OAUTH_SCOPES)

        save_google_drive_credential(
            user.user_id,
            access_token=access_token,
            refresh_token=token_payload.get('refresh_token'),
            token_uri=GOOGLE_OAUTH_TOKEN_URL,
            scopes=scopes,
            expires_at=expires_at,
            google_email=google_email or user.email,
        )
        success_target = _append_query_value(next_path, 'driveAuth', 'connected')
        return redirect(success_target)
    except Exception as exc:
        logger.error("Error completing Google Drive OAuth callback: %s", exc, exc_info=True)
        error_target = _append_query_value(next_path, 'driveAuth', 'error')
        error_target = _append_query_value(error_target, 'driveAuthMessage', 'callback_failed')
        return redirect(error_target)


@auth_bp.route('/api/profile', methods=['GET'])
@approved_required
def api_get_profile():
    """Get current user's profile data."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    
    return jsonify({
        'user_id': user.user_id,
        'email': user.email,
        'name': user.name,
        'role': user.role,
        'is_admin': user.is_admin(),  # Add is_admin field
        'whatsapp_number': user.whatsapp_number or '',
        'whatsapp_notifications_enabled': user.whatsapp_notifications_enabled,
        'google_drive': _google_drive_status_payload(user),
    })


@auth_bp.route('/api/google-drive-status', methods=['GET'])
@approved_required
def api_get_google_drive_status():
    """Get the current user's Google Drive authorization status."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    return jsonify(_google_drive_status_payload(user))


@auth_bp.route('/api/google-drive-disconnect', methods=['DELETE'])
@approved_required
def api_google_drive_disconnect():
    """Remove the stored Drive credential for the current user."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401

    delete_google_drive_credential_for_user(user.user_id)
    return jsonify(_google_drive_status_payload(user))


@auth_bp.route('/api/profile', methods=['PUT'])
@approved_required
def api_update_profile():
    """Update current user's profile."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request data'}), 400
    
    session = get_session()
    try:
        # Import User model
        from dashboard.auth.models import User
        
        # Refresh user from database
        db_user = session.query(User).filter(User.user_id == user.user_id).first()
        if not db_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Update WhatsApp number
        if 'whatsapp_number' in data:
            whatsapp_number = data['whatsapp_number'].strip() if data['whatsapp_number'] else None
            
            # Validate phone number format (E.164)
            if whatsapp_number:
                # E.164 format: +[country code][number] (1-15 digits after +)
                pattern = r'^\+[1-9]\d{1,14}$'
                if not re.match(pattern, whatsapp_number):
                    return jsonify({'error': 'Invalid phone number format. Use E.164 format (e.g., +14155552671)'}), 400
            
            db_user.whatsapp_number = whatsapp_number
        
        # Update notification preference
        if 'whatsapp_notifications_enabled' in data:
            db_user.whatsapp_notifications_enabled = bool(data['whatsapp_notifications_enabled'])
        
        session.commit()
        session.refresh(db_user)
        
        # Log profile update activity
        try:
            from dashboard.activities.logger import log_auth_activity
            updated_fields = []
            if 'whatsapp_number' in data:
                updated_fields.append('whatsapp_number')
            if 'whatsapp_notifications_enabled' in data:
                updated_fields.append('whatsapp_notifications_enabled')
            
            if updated_fields:
                log_auth_activity(
                    user_id=user.user_id,
                    action='profile_update',
                    metadata={'updated_fields': updated_fields}
                )
        except Exception as e:
            # Log but don't fail profile update if activity logging fails
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error logging profile update activity: {e}", exc_info=True)
        
        return jsonify({
            'user_id': db_user.user_id,
            'email': db_user.email,
            'name': db_user.name,
            'role': db_user.role,
            'whatsapp_number': db_user.whatsapp_number or '',
            'whatsapp_notifications_enabled': db_user.whatsapp_notifications_enabled
        })
    except Exception as e:
        session.rollback()
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error updating profile: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


def register_auth_routes(app):
    """Register authentication routes with the Flask app."""
    app.register_blueprint(auth_bp)
