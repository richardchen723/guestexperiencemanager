#!/usr/bin/env python3
"""
Flask application entry point for the Insights Dashboard.
"""

import sys
import os
import logging
from flask import Flask, url_for

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import dashboard.config as config
from utils.logging_config import setup_logging
from dashboard.api.routes import register_routes
from dashboard.auth.routes import register_auth_routes
from dashboard.auth.admin_routes import register_admin_routes
from dashboard.tickets.routes import register_ticket_routes
from dashboard.sync.routes import register_sync_routes
from dashboard.auth.oauth import create_google_blueprint
from dashboard.auth.init import ensure_owner_exists
from dashboard.auth.session import get_current_user


def create_app():
    """Create and configure the Flask application."""
    # Detect Vercel environment
    is_vercel = os.environ.get('VERCEL', '0') == '1'
    
    # Setup logging - use stdout in Vercel (no file system), file logging locally
    if is_vercel:
        # Vercel/serverless environments have read-only filesystems, so avoid file logging
        setup_logging(log_file=None)
    else:
        log_file = os.path.join(project_root, 'logs', 'dashboard.log')
        setup_logging(log_file=log_file)
    
    logger = logging.getLogger(__name__)
    logger.info("Initializing Flask application with DEBUG logging enabled")
    
    # Allow insecure transport for localhost development (HTTP instead of HTTPS)
    # This is required for OAuth to work on localhost
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    
    # Get absolute paths for templates and static files
    dashboard_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(dashboard_dir, 'templates')
    static_dir = os.path.join(dashboard_dir, 'static')
    
    app = Flask(__name__, 
                template_folder=template_dir,
                static_folder=static_dir,
                static_url_path='/static')
    
    # Configure session
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['SESSION_PERMANENT'] = False  # Session expires on browser close
    
    # Initialize owner account
    ensure_owner_exists()
    
    # Register Google OAuth blueprint
    google_bp = create_google_blueprint(app)
    
    # Register routes
    register_routes(app)
    register_auth_routes(app)
    register_admin_routes(app)
    register_ticket_routes(app)
    register_sync_routes(app)
    
    # Make current_user available to all templates
    @app.context_processor
    def inject_user():
        return dict(current_user=get_current_user())
    
    # Add cache-busting headers for templates (prevent browser caching old templates)
    @app.after_request
    def add_no_cache_headers(response):
        if response.content_type and 'text/html' in response.content_type:
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response
    
    # Debug routes only available in debug mode
    if config.FLASK_DEBUG:
        @app.route('/debug/paths')
        def debug_paths():
            return {
                'dashboard_dir': dashboard_dir,
                'template_dir': template_dir,
                'static_dir': static_dir,
                'template_exists': os.path.exists(template_dir),
                'static_exists': os.path.exists(static_dir),
                'css_exists': os.path.exists(os.path.join(static_dir, 'css', 'style.css')),
                'js_exists': os.path.exists(os.path.join(static_dir, 'js', 'dashboard.js'))
            }
        
        @app.route('/debug/oauth')
        def debug_oauth():
            redirect_uri = f"http://{config.FLASK_HOST}:{config.FLASK_PORT}/auth/google/authorized"
            return {
                'oauth_configured': config.GOOGLE_CLIENT_ID is not None,
                'redirect_uri': redirect_uri,
                'instructions': 'Add this exact redirect URI to Google Cloud Console: ' + redirect_uri,
                'flask_host': config.FLASK_HOST,
                'flask_port': config.FLASK_PORT,
                'note': 'Flask-Dance uses /auth/google/authorized as the default callback route'
            }
    
    # Add error handler for 404
    @app.errorhandler(404)
    def not_found(error):
        return "Page not found", 404
    
    # Add error handler for 403
    @app.errorhandler(403)
    def forbidden(error):
        return "Access forbidden", 403
    
    return app


# Create app instance for Vercel serverless function
# Vercel expects 'app' to be available at module level
app = create_app()

# For local development
if __name__ == '__main__':
    logger = logging.getLogger(__name__)
    logger.info(f"Starting Insights Dashboard on http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    logger.info("Press Ctrl+C to stop")
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
