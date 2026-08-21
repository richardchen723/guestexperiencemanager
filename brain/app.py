#!/usr/bin/env python3
"""Flask application entry point for STR Signal Brain."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

import sqlalchemy
from flask import Flask, render_template
from jinja2 import ChoiceLoader, FileSystemLoader
from werkzeug.middleware.proxy_fix import ProxyFix

_this_file = os.path.abspath(os.path.realpath(__file__))
_brain_dir = os.path.dirname(_this_file)
project_root = os.path.dirname(_brain_dir)

if _brain_dir in sys.path:
    sys.path.remove(_brain_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
os.chdir(project_root)

import dashboard.config as config
from brain.models import init_brain_database
from brain.routes import brain_bp
from brain.webhooks import webhooks_bp
from dashboard.auth.init import ensure_owner_exists
from dashboard.auth.oauth import create_google_blueprint
from dashboard.auth.routes import register_auth_routes
from dashboard.auth.session import get_current_user
from utils.logging_config import setup_logging


def _configure_oauth_transport():
    """Allow local HTTP OAuth only when explicitly configured for dev."""
    if config.FLASK_DEBUG or getattr(config, "BRAIN_ALLOW_INSECURE_OAUTH", False):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def create_app():
    """Create and configure the Brain Flask application."""
    log_file = os.getenv("BRAIN_LOG_FILE", os.path.join(project_root, "logs", "brain.log"))
    if not os.path.isabs(log_file):
        log_file = os.path.join(project_root, log_file)
    setup_logging(log_file=log_file)
    logger = logging.getLogger(__name__)
    logger.info("Initializing STR Signal Brain")

    _configure_oauth_transport()

    template_dir = os.path.join(_brain_dir, "templates")
    static_dir = os.path.join(_brain_dir, "static")
    dashboard_template_dir = os.path.join(project_root, "dashboard", "templates")

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir, static_url_path="/brain-static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.jinja_loader = ChoiceLoader([FileSystemLoader(template_dir), FileSystemLoader(dashboard_template_dir)])
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["SESSION_PERMANENT"] = False
    app.config["POST_LOGIN_ENDPOINT"] = "brain.today_page"
    app.config["POST_LOGOUT_ENDPOINT"] = "auth.login"
    app.config["PENDING_APPROVAL_ENDPOINT"] = "auth.pending_approval"

    ensure_owner_exists()
    init_brain_database()

    create_google_blueprint(app)
    register_auth_routes(app)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(brain_bp)

    @app.route("/health")
    def health_check():
        try:
            from brain.models import get_engine

            engine = get_engine()
            with engine.connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1"))
            return {
                "status": "healthy",
                "service": "str-signal-brain",
                "database": "connected",
                "timestamp": datetime.utcnow().isoformat(),
            }, 200
        except Exception as exc:
            logger.error("Brain health check failed: %s", exc, exc_info=True)
            return {
                "status": "unhealthy",
                "service": "str-signal-brain",
                "database": "disconnected",
                "error": str(exc),
                "timestamp": datetime.utcnow().isoformat(),
            }, 503

    @app.context_processor
    def inject_user():
        return {"current_user": get_current_user()}

    @app.after_request
    def add_no_cache_headers(response):
        if response.content_type and "text/html" in response.content_type:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.errorhandler(404)
    def not_found(error):
        return render_template("brain/error.html", message="Page not found"), 404

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("brain/error.html", message="Access forbidden"), 403

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=config.BRAIN_FLASK_HOST, port=config.BRAIN_FLASK_PORT, debug=config.FLASK_DEBUG)
