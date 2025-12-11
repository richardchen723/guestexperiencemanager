#!/usr/bin/env python3
"""
Google OAuth integration using Flask-Dance.
"""

import sys
import os
from flask import redirect, url_for

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import dashboard.config as config
from dashboard.auth.models import get_user_by_email, get_user_by_google_id, create_user
from dashboard.auth.session import login_user


def create_google_blueprint(app):
    """Create and configure Google OAuth blueprint."""
    try:
        from flask_dance.contrib.google import make_google_blueprint
        from flask_dance.consumer import oauth_authorized
    except ImportError:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Flask-Dance not installed. Google OAuth will not work.")
        return None
    
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Google OAuth credentials not configured. OAuth will not work.")
        return None
    
    google_bp = make_google_blueprint(
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        scope=['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile']
    )
    
    # Use Flask-Dance's signal to handle user creation after OAuth completes
    # The signal handler can return False to prevent default redirect, or return a redirect response
    @oauth_authorized.connect_via(google_bp)
    def google_logged_in(blueprint, token):
        """Handle user creation/login after successful OAuth."""
        # This signal is called after Flask-Dance successfully exchanges the auth code for a token
        # Now we can safely get user info and create/login the user
        result = handle_google_callback()
        # Return the redirect response, which Flask-Dance will use instead of its default
        return result
    
    # Now register the blueprint
    app.register_blueprint(google_bp, url_prefix='/auth')
    
    return google_bp


def handle_google_callback():
    """Handle Google OAuth callback and create/login user."""
    try:
        from flask_dance.contrib.google import google
        from flask import redirect, url_for
    except ImportError:
        return redirect(url_for('auth.login'))
    
    if not google.authorized:
        return redirect(url_for('auth.login'))
    
    try:
        # Get user info from Google
        resp = google.get('/oauth2/v2/userinfo')
        if not resp.ok:
            return redirect(url_for('auth.login'))
        
        user_info = resp.json()
        google_id = user_info.get('id')
        email = user_info.get('email')
        name = user_info.get('name')
        picture_url = user_info.get('picture')
        
        if not email:
            return redirect(url_for('auth.login'))
        
        # Check if this is the owner email - always set as owner and approved
        is_owner_email = email.lower() == config.OWNER_EMAIL.lower()
        
        # Check if user exists by email or google_id
        user = get_user_by_email(email)
        if not user:
            user = get_user_by_google_id(google_id) if google_id else None
        
        if not user:
            # Create new user
            # If owner email, automatically set as owner and approved
            user = create_user(
                email=email,
                name=name,
                picture_url=picture_url,
                google_id=google_id,
                role='owner' if is_owner_email else 'user',
                is_approved=True if is_owner_email else False
            )
        else:
            # Update user info from Google
            from dashboard.auth.models import get_session, User
            session = get_session()
            try:
                db_user = session.query(User).filter(User.user_id == user.user_id).first()
                if db_user:
                    updated = False
                    
                    # Always update Google ID if provided and missing
                    if not db_user.google_id and google_id:
                        db_user.google_id = google_id
                        updated = True
                    
                    # Always update name from Google if provided (replace any existing value)
                    if name and name != db_user.name:
                        db_user.name = name
                        updated = True
                    
                    # Always update picture from Google if provided (replace any existing value)
                    if picture_url and picture_url != db_user.picture_url:
                        db_user.picture_url = picture_url
                        updated = True
                    
                    # If this is the owner email, ensure role is owner and approved
                    if is_owner_email:
                        if db_user.role != 'owner':
                            db_user.role = 'owner'
                            updated = True
                        if not db_user.is_approved:
                            db_user.is_approved = True
                            updated = True
                    
                    if updated:
                        session.commit()
            finally:
                session.close()
        
        # Log in the user
        login_user(user.user_id)
        
        # Return None to let Flask-Dance handle the redirect, or return a redirect
        # Flask-Dance will redirect to the home page by default, but we want custom logic
        # So we return a redirect response
        if user.is_approved:
            return redirect(url_for('dashboard.dashboard_page'))
        else:
            return redirect(url_for('auth.pending_approval'))
            
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in Google OAuth callback: {e}", exc_info=True)
        return redirect(url_for('auth.login'))
