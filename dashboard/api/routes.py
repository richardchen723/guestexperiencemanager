#!/usr/bin/env python3
"""
Flask API routes for the dashboard.
"""

import sys
import os
from flask import render_template, jsonify, request

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from database.models import get_session, Listing
from database.schema import get_database_path
import dashboard.config as config
from dashboard.ai.analyzer import get_insights
from dashboard.auth.decorators import approved_required
from dashboard.auth.session import get_current_user


def register_routes(app):
    """
    Register all routes with the Flask app.
    
    Args:
        app: Flask application instance
    """
    
    @app.route('/')
    @approved_required
    def index():
        """Dashboard home page with listing selection."""
        return render_template('index.html', current_user=get_current_user())
    
    @app.route('/api/listings')
    @approved_required
    def api_listings():
        """Get all listings as JSON with quality ratings."""
        from dashboard.ai.cache import get_cached_insights_batch
        
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            listings = session.query(Listing).order_by(Listing.name).all()
            
            # Batch load all insights in one query (fixes N+1 problem)
            listing_ids = [l.listing_id for l in listings]
            insights_map = get_cached_insights_batch(listing_ids)
            
            result = []
            for l in listings:
                insights = insights_map.get(l.listing_id)
                quality_rating = insights.get('quality_rating') if insights else None
                
                result.append({
                    'listing_id': l.listing_id,
                    'name': l.name,
                    'address': l.address,
                    'city': l.city,
                    'status': l.status,
                    'quality_rating': quality_rating  # Good, Fair, Poor, or None
                })
            
            return jsonify(result)
        finally:
            session.close()
    
    @app.route('/api/insights/<int:listing_id>')
    @approved_required
    def api_insights(listing_id):
        """Get insights for a specific listing."""
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        try:
            insights = get_insights(listing_id, force_refresh=force_refresh)
            return jsonify(insights)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/insights/<int:listing_id>')
    @approved_required
    def insights_page(listing_id):
        """Render insights page for a listing."""
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            listing = session.query(Listing).filter(Listing.listing_id == listing_id).first()
            if not listing:
                return "Listing not found", 404
            
            return render_template('insights.html', listing=listing, current_user=get_current_user())
        finally:
            session.close()

