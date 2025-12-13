#!/usr/bin/env python3
"""
Flask application entry point for the Insights Dashboard.
"""

import sys
import os

# CRITICAL: Set up paths BEFORE any other imports
# Get the absolute path of this file, then go up to project root
# This works whether running as: python3 dashboard/app.py or python3 app.py (from dashboard dir)
# os.path.abspath() handles both relative and absolute paths correctly
_this_file = os.path.abspath(os.path.realpath(__file__))
_dashboard_dir = os.path.dirname(_this_file)
project_root = os.path.dirname(_dashboard_dir)

# Debug: uncomment to verify paths (commented out for production)
# print(f"DEBUG: __file__ = {__file__}")
# print(f"DEBUG: _this_file = {_this_file}")
# print(f"DEBUG: project_root = {project_root}")
# print(f"DEBUG: CWD before chdir = {os.getcwd()}")

# Add project root to Python path
# CRITICAL: Remove dashboard directory from path if it was auto-added
# Python automatically adds the script's directory to sys.path, which causes conflicts
if _dashboard_dir in sys.path:
    sys.path.remove(_dashboard_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Change working directory to project root BEFORE any imports
# This ensures that relative imports and file paths work correctly
os.chdir(project_root)

# Debug: verify path setup (comment out after testing)
# print(f"DEBUG: CWD after chdir = {os.getcwd()}")
# print(f"DEBUG: sys.path[0] = {sys.path[0]}")
# print(f"DEBUG: dashboard/config.py exists = {os.path.exists(os.path.join(project_root, 'dashboard', 'config.py'))}")

# Now we can safely import other modules
import logging
from datetime import datetime
from flask import Flask, url_for
import sqlalchemy

import dashboard.config as config
from utils.logging_config import setup_logging
from dashboard.api.routes import register_routes
from dashboard.auth.routes import register_auth_routes
from dashboard.auth.admin_routes import register_admin_routes
from dashboard.tickets.routes import register_ticket_routes
from dashboard.sync.routes import register_sync_routes
from dashboard.activities.routes import register_activities_routes
from dashboard.reviews.routes import register_reviews_routes
from dashboard.dashboard.routes import dashboard_bp
from dashboard.auth.oauth import create_google_blueprint
from dashboard.auth.init import ensure_owner_exists
from dashboard.auth.session import get_current_user


def create_app():
    """Create and configure the Flask application."""
    # Setup logging with DEBUG level for detailed diagnostics
    # Use absolute path for production deployment
    log_file = os.getenv('LOG_FILE', os.path.join(project_root, 'logs', 'dashboard.log'))
    # Ensure absolute path
    if not os.path.isabs(log_file):
        log_file = os.path.join(project_root, log_file)
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
    register_activities_routes(app)
    register_reviews_routes(app)
    app.register_blueprint(dashboard_bp)
    
    # Health check endpoint for monitoring
    @app.route('/health')
    def health_check():
        """Health check endpoint for monitoring and load balancers"""
        try:
            # Check database connectivity
            from database.models import get_engine
            from database.schema import get_database_path
            db_path = get_database_path()
            engine = get_engine(db_path)
            
            # Simple connection test
            with engine.connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1"))
            
            return {
                'status': 'healthy',
                'database': 'connected',
                'timestamp': datetime.utcnow().isoformat()
            }, 200
        except Exception as e:
            logger.error(f"Health check failed: {e}", exc_info=True)
            return {
                'status': 'unhealthy',
                'database': 'disconnected',
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }, 503
    
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


if __name__ == '__main__':
    app = create_app()
    logger = logging.getLogger(__name__)
    logger.info(f"Starting Insights Dashboard on http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    logger.info("Press Ctrl+C to stop")
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
