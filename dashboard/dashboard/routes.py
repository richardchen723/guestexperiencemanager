#!/usr/bin/env python3
"""
Dashboard routes and API endpoints.
"""

import sys
import os
import logging
from flask import Blueprint, render_template, jsonify, request

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.dashboard.service import DashboardService
from dashboard.auth.decorators import approved_required
from dashboard.auth.session import get_current_user

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


@dashboard_bp.route('/')
@approved_required
def dashboard_page():
    """Render dashboard page."""
    return render_template('dashboard/dashboard.html', 
                         current_user=get_current_user())


@dashboard_bp.route('/api/data')
@approved_required
def api_dashboard_data():
    """Get all dashboard data in one request."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Validate and parse query parameters
    try:
        ticket_limit = min(max(1, int(request.args.get('ticket_limit', 10))), 50)
    except (ValueError, TypeError):
        ticket_limit = 10
    
    try:
        occupancy_months = min(max(1, int(request.args.get('occupancy_months', 6))), 12)
    except (ValueError, TypeError):
        occupancy_months = 6
    
    try:
        service = DashboardService(current_user.user_id)
        data = service.get_dashboard_data(
            ticket_limit=ticket_limit,
            occupancy_months=occupancy_months
        )
        return jsonify(data)
    except Exception as e:
        logger.error(f"Error fetching dashboard data for user {current_user.user_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to load dashboard data'}), 500



