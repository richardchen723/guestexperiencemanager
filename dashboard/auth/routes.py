#!/usr/bin/env python3
"""
Authentication routes.
"""

import sys
import os
import re
from flask import Blueprint, render_template, redirect, url_for, jsonify, request

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.auth.session import logout_user, get_current_user
from dashboard.auth.decorators import login_required, approved_required
from dashboard.auth.oauth import handle_google_callback
from dashboard.auth.models import get_session

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


@auth_bp.route('/login')
def login():
    """Login page - redirects to Google OAuth."""
    if get_current_user():
        user = get_current_user()
        if user and user.is_approved:
            return redirect(url_for('dashboard.dashboard_page'))
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
        'whatsapp_number': user.whatsapp_number or '',
        'whatsapp_notifications_enabled': user.whatsapp_notifications_enabled
    })


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
