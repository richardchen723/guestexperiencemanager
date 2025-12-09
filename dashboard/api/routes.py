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

from database.models import get_session, Listing, Tag, ListingTag
from database.schema import get_database_path
import dashboard.config as config
from dashboard.ai.analyzer import get_insights
from dashboard.auth.decorators import approved_required
from dashboard.auth.session import get_current_user
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import joinedload


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
        """Get all listings as JSON with quality ratings and tags."""
        from dashboard.ai.cache import get_cached_insights_batch
        
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            # Get tag filter parameters
            tags_param = request.args.get('tags', '')
            tag_logic = request.args.get('tag_logic', 'AND').upper()  # AND or OR
            
            query = session.query(Listing)
            
            # Apply tag filtering if provided
            if tags_param:
                tag_names = [t.strip().lower() for t in tags_param.split(',') if t.strip()]
                if tag_names:
                    # Get tag IDs
                    tags = session.query(Tag).filter(Tag.name.in_(tag_names)).all()
                    tag_ids = [t.tag_id for t in tags]
                    
                    if tag_ids:
                        # Get listing IDs that have these tags
                        if tag_logic == 'OR':
                            # At least one tag must match
                            listing_ids_with_tags = session.query(ListingTag.listing_id).filter(
                                ListingTag.tag_id.in_(tag_ids)
                            ).distinct().all()
                        else:
                            # All tags must match (AND)
                            listing_ids_with_tags = session.query(ListingTag.listing_id).filter(
                                ListingTag.tag_id.in_(tag_ids)
                            ).group_by(ListingTag.listing_id).having(
                                func.count(ListingTag.tag_id.distinct()) == len(tag_ids)
                            ).all()
                        
                        listing_ids = [row[0] for row in listing_ids_with_tags]
                        query = query.filter(Listing.listing_id.in_(listing_ids))
                    else:
                        # No tags found, return empty result
                        return jsonify([])
            
            listings = query.order_by(Listing.name).all()
            
            # Batch load all insights in one query (fixes N+1 problem)
            listing_ids = [l.listing_id for l in listings]
            insights_map = get_cached_insights_batch(listing_ids)
            
            # Batch load tags for all listings
            listing_tags_map = {}
            if listing_ids:
                listing_tags = session.query(ListingTag).filter(
                    ListingTag.listing_id.in_(listing_ids)
                ).options(joinedload(ListingTag.tag)).all()
                for lt in listing_tags:
                    if lt.listing_id not in listing_tags_map:
                        listing_tags_map[lt.listing_id] = []
                    listing_tags_map[lt.listing_id].append({
                        'tag_id': lt.tag.tag_id,
                        'name': lt.tag.name,
                        'color': lt.tag.color
                    })
            
            result = []
            for l in listings:
                insights = insights_map.get(l.listing_id)
                quality_rating = insights.get('quality_rating') if insights else None
                
                result.append({
                    'listing_id': l.listing_id,
                    'name': l.name,
                    'internal_listing_name': l.internal_listing_name,
                    'address': l.address,
                    'city': l.city,
                    'status': l.status,
                    'quality_rating': quality_rating,  # Good, Fair, Poor, or None
                    'tags': listing_tags_map.get(l.listing_id, [])
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
    
    # Tag Management Endpoints
    @app.route('/api/tags', methods=['GET'])
    @approved_required
    def api_list_tags():
        """Get all tags with usage counts."""
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            tags = session.query(Tag).order_by(Tag.name).all()
            
            result = []
            for tag in tags:
                # Count usage
                listing_count = session.query(func.count(ListingTag.listing_id)).filter(
                    ListingTag.tag_id == tag.tag_id
                ).scalar()
                
                result.append({
                    'tag_id': tag.tag_id,
                    'name': tag.name,
                    'color': tag.color,
                    'created_at': tag.created_at.isoformat() if tag.created_at else None,
                    'usage_count': listing_count or 0
                })
            
            return jsonify(result)
        finally:
            session.close()
    
    @app.route('/api/tags', methods=['POST'])
    @approved_required
    def api_create_tag():
        """Create a new tag."""
        data = request.get_json()
        if not data or 'name' not in data:
            return jsonify({'error': 'Tag name is required'}), 400
        
        try:
            normalized_name = Tag.normalize_name(data['name'])
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        
        color = data.get('color')
        if color and not (color.startswith('#') and len(color) == 7):
            return jsonify({'error': 'Invalid color format. Use hex format like #FF5733'}), 400
        
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            # Check if tag already exists
            existing_tag = session.query(Tag).filter(Tag.name == normalized_name).first()
            if existing_tag:
                return jsonify({
                    'tag_id': existing_tag.tag_id,
                    'name': existing_tag.name,
                    'color': existing_tag.color,
                    'created_at': existing_tag.created_at.isoformat() if existing_tag.created_at else None
                }), 200
            
            tag = Tag(name=normalized_name, color=color)
            session.add(tag)
            session.commit()
            
            return jsonify({
                'tag_id': tag.tag_id,
                'name': tag.name,
                'color': tag.color,
                'created_at': tag.created_at.isoformat() if tag.created_at else None
            }), 201
        except Exception as e:
            session.rollback()
            return jsonify({'error': str(e)}), 500
        finally:
            session.close()
    
    @app.route('/api/tags/<int:tag_id>', methods=['DELETE'])
    @approved_required
    def api_delete_tag(tag_id):
        """Delete a tag (cascade removes from listings/tickets)."""
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            tag = session.query(Tag).filter(Tag.tag_id == tag_id).first()
            if not tag:
                return jsonify({'error': 'Tag not found'}), 404
            
            session.delete(tag)
            session.commit()
            return jsonify({'message': 'Tag deleted successfully'}), 200
        except Exception as e:
            session.rollback()
            return jsonify({'error': str(e)}), 500
        finally:
            session.close()
    
    @app.route('/api/tags/autocomplete', methods=['GET'])
    @approved_required
    def api_tag_autocomplete():
        """Get tag suggestions based on partial name."""
        query = request.args.get('q', '').strip().lower()
        if not query:
            return jsonify([])
        
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            tags = session.query(Tag).filter(
                Tag.name.like(f'%{query}%')
            ).order_by(Tag.name).limit(10).all()
            
            result = [{'tag_id': t.tag_id, 'name': t.name, 'color': t.color} for t in tags]
            return jsonify(result)
        finally:
            session.close()
    
    # Listing Tag Endpoints
    @app.route('/api/listings/<int:listing_id>/tags', methods=['GET'])
    @approved_required
    def api_get_listing_tags(listing_id):
        """Get all tags for a listing."""
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            listing = session.query(Listing).filter(Listing.listing_id == listing_id).first()
            if not listing:
                return jsonify({'error': 'Listing not found'}), 404
            
            listing_tags = session.query(ListingTag).filter(
                ListingTag.listing_id == listing_id
            ).options(joinedload(ListingTag.tag)).all()
            
            result = [{
                'tag_id': lt.tag.tag_id,
                'name': lt.tag.name,
                'color': lt.tag.color,
                'created_at': lt.created_at.isoformat() if lt.created_at else None
            } for lt in listing_tags]
            
            return jsonify(result)
        finally:
            session.close()
    
    @app.route('/api/listings/<int:listing_id>/tags', methods=['POST'])
    @approved_required
    def api_add_listing_tags(listing_id):
        """Add tag(s) to a listing."""
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400
        
        tag_names = data.get('tags', [])
        if not tag_names or not isinstance(tag_names, list):
            return jsonify({'error': 'tags must be a non-empty array'}), 400
        
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            listing = session.query(Listing).filter(Listing.listing_id == listing_id).first()
            if not listing:
                return jsonify({'error': 'Listing not found'}), 404
            
            added_tags = []
            for tag_name in tag_names:
                try:
                    normalized_name = Tag.normalize_name(tag_name)
                except ValueError as e:
                    continue  # Skip invalid tag names
                
                # Get or create tag
                tag = session.query(Tag).filter(Tag.name == normalized_name).first()
                if not tag:
                    tag = Tag(name=normalized_name)
                    session.add(tag)
                    session.flush()  # Get tag_id
                
                # Check if listing already has this tag
                existing = session.query(ListingTag).filter(
                    ListingTag.listing_id == listing_id,
                    ListingTag.tag_id == tag.tag_id
                ).first()
                
                if not existing:
                    listing_tag = ListingTag(listing_id=listing_id, tag_id=tag.tag_id)
                    session.add(listing_tag)
                    added_tags.append({
                        'tag_id': tag.tag_id,
                        'name': tag.name,
                        'color': tag.color
                    })
            
            session.commit()
            return jsonify(added_tags), 201
        except Exception as e:
            session.rollback()
            return jsonify({'error': str(e)}), 500
        finally:
            session.close()
    
    @app.route('/api/listings/<int:listing_id>/tags/<int:tag_id>', methods=['DELETE'])
    @approved_required
    def api_remove_listing_tag(listing_id, tag_id):
        """Remove a tag from a listing."""
        session = get_session(config.MAIN_DATABASE_PATH)
        try:
            listing_tag = session.query(ListingTag).filter(
                ListingTag.listing_id == listing_id,
                ListingTag.tag_id == tag_id
            ).first()
            
            if not listing_tag:
                return jsonify({'error': 'Tag not found on listing'}), 404
            
            session.delete(listing_tag)
            session.commit()
            return jsonify({'message': 'Tag removed successfully'}), 200
        except Exception as e:
            session.rollback()
            return jsonify({'error': str(e)}), 500
        finally:
            session.close()

