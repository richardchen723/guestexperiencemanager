#!/usr/bin/env python3
"""
Reviews API routes.
"""

import sys
import os
import json
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.reviews.query import get_unresponded_reviews, get_reviews_by_filter
from database.models import ReviewFilter, Tag, get_session
from database.schema import get_database_path
from dashboard.auth.decorators import approved_required, admin_required
from dashboard.auth.session import get_current_user
import logging

logger = logging.getLogger(__name__)

reviews_bp = Blueprint('reviews', __name__, url_prefix='/reviews')


@reviews_bp.route('/')
@approved_required
def reviews_page():
    """Reviews page."""
    return render_template('reviews/index.html', current_user=get_current_user())


@reviews_bp.route('/api/unresponded')
@approved_required
def api_unresponded_reviews():
    """Get unresponded reviews (status='Submitted', origin='Guest')."""
    try:
        # Get tag_ids from query parameters
        tag_ids_param = request.args.get('tag_ids')
        tag_ids = None
        if tag_ids_param:
            try:
                import json
                tag_ids = json.loads(tag_ids_param)
                if not isinstance(tag_ids, list):
                    tag_ids = [tag_ids] if tag_ids else None
            except (json.JSONDecodeError, ValueError):
                # Try comma-separated list
                tag_ids = [int(tid.strip()) for tid in tag_ids_param.split(',') if tid.strip().isdigit()]
        
        reviews = get_unresponded_reviews(tag_ids=tag_ids)
        return jsonify({'reviews': reviews}), 200
    except Exception as e:
        logger.error(f"Error fetching unresponded reviews: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/filters')
@approved_required
def api_get_filters():
    """Get all saved review filters for current user."""
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Not authenticated'}), 401
        
        filters = session.query(ReviewFilter).filter(
            ReviewFilter.created_by == current_user.user_id
        ).order_by(ReviewFilter.created_at.desc()).all()
        
        result = []
        for filter_obj in filters:
            # Parse tag_ids if it's a JSON string
            tag_ids = filter_obj.tag_ids
            if isinstance(tag_ids, str):
                try:
                    tag_ids = json.loads(tag_ids)
                except:
                    tag_ids = []
            
            result.append({
                'filter_id': filter_obj.filter_id,
                'name': filter_obj.name,
                'tag_ids': tag_ids if isinstance(tag_ids, list) else [],
                'max_rating': filter_obj.max_rating,
                'months_back': filter_obj.months_back,
                'created_at': filter_obj.created_at.isoformat() if filter_obj.created_at else None,
                'updated_at': filter_obj.updated_at.isoformat() if filter_obj.updated_at else None
            })
        
        return jsonify({'filters': result}), 200
        
    except Exception as e:
        logger.error(f"Error fetching filters: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@reviews_bp.route('/api/filters', methods=['POST'])
@approved_required
def api_create_filter():
    """Create a new review filter."""
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Not authenticated'}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Parse tag_ids
        tag_ids = data.get('tag_ids', [])
        if isinstance(tag_ids, list):
            # For SQLite, store as JSON string; for PostgreSQL, store as JSONB
            import os
            if os.getenv("DATABASE_URL"):
                # PostgreSQL - store as list (JSONB)
                tag_ids_value = tag_ids
            else:
                # SQLite - store as JSON string
                tag_ids_value = json.dumps(tag_ids) if tag_ids else None
        else:
            tag_ids_value = tag_ids
        
        filter_obj = ReviewFilter(
            name=data.get('name'),
            tag_ids=tag_ids_value,
            max_rating=data.get('max_rating'),
            months_back=data.get('months_back'),
            created_by=current_user.user_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        session.add(filter_obj)
        session.commit()
        
        # Return created filter
        tag_ids_result = tag_ids if isinstance(tag_ids, list) else (json.loads(tag_ids) if tag_ids else [])
        
        return jsonify({
            'filter_id': filter_obj.filter_id,
            'name': filter_obj.name,
            'tag_ids': tag_ids_result,
            'max_rating': filter_obj.max_rating,
            'months_back': filter_obj.months_back,
            'created_at': filter_obj.created_at.isoformat() if filter_obj.created_at else None,
            'updated_at': filter_obj.updated_at.isoformat() if filter_obj.updated_at else None
        }), 201
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error creating filter: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@reviews_bp.route('/api/filters/<int:filter_id>', methods=['PUT'])
@approved_required
def api_update_filter(filter_id):
    """Update a review filter."""
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Not authenticated'}), 401
        
        filter_obj = session.query(ReviewFilter).filter(
            ReviewFilter.filter_id == filter_id,
            ReviewFilter.created_by == current_user.user_id
        ).first()
        
        if not filter_obj:
            return jsonify({'error': 'Filter not found'}), 404
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Update fields
        if 'name' in data:
            filter_obj.name = data['name']
        if 'tag_ids' in data:
            tag_ids = data['tag_ids']
            if isinstance(tag_ids, list):
                import os
                if os.getenv("DATABASE_URL"):
                    # PostgreSQL - store as list (JSONB)
                    filter_obj.tag_ids = tag_ids
                else:
                    # SQLite - store as JSON string
                    filter_obj.tag_ids = json.dumps(tag_ids) if tag_ids else None
            else:
                filter_obj.tag_ids = tag_ids
        if 'max_rating' in data:
            filter_obj.max_rating = data['max_rating']
        if 'months_back' in data:
            filter_obj.months_back = data['months_back']
        
        filter_obj.updated_at = datetime.utcnow()
        
        session.commit()
        
        # Return updated filter
        tag_ids_result = filter_obj.tag_ids
        if isinstance(tag_ids_result, str):
            try:
                tag_ids_result = json.loads(tag_ids_result)
            except:
                tag_ids_result = []
        elif not isinstance(tag_ids_result, list):
            tag_ids_result = []
        
        return jsonify({
            'filter_id': filter_obj.filter_id,
            'name': filter_obj.name,
            'tag_ids': tag_ids_result,
            'max_rating': filter_obj.max_rating,
            'months_back': filter_obj.months_back,
            'created_at': filter_obj.created_at.isoformat() if filter_obj.created_at else None,
            'updated_at': filter_obj.updated_at.isoformat() if filter_obj.updated_at else None
        }), 200
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error updating filter: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@reviews_bp.route('/api/filters/<int:filter_id>', methods=['DELETE'])
@approved_required
def api_delete_filter(filter_id):
    """Delete a review filter."""
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Not authenticated'}), 401
        
        filter_obj = session.query(ReviewFilter).filter(
            ReviewFilter.filter_id == filter_id,
            ReviewFilter.created_by == current_user.user_id
        ).first()
        
        if not filter_obj:
            return jsonify({'error': 'Filter not found'}), 404
        
        session.delete(filter_obj)
        session.commit()
        
        return jsonify({'message': 'Filter deleted successfully'}), 200
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error deleting filter: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@reviews_bp.route('/api/filters/<int:filter_id>/reviews')
@approved_required
def api_get_filtered_reviews(filter_id):
    """Get reviews matching a filter's criteria."""
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        current_user = get_current_user()
        if not current_user:
            logger.warning(f"Unauthenticated request for filter {filter_id} reviews")
            return jsonify({'error': 'Not authenticated'}), 401
        
        logger.info(f"Fetching reviews for filter {filter_id} (user: {current_user.user_id})")
        
        filter_obj = session.query(ReviewFilter).filter(
            ReviewFilter.filter_id == filter_id,
            ReviewFilter.created_by == current_user.user_id
        ).first()
        
        if not filter_obj:
            logger.warning(f"Filter {filter_id} not found for user {current_user.user_id}")
            return jsonify({'error': 'Filter not found'}), 404
        
        logger.info(f"Filter found: tag_ids={filter_obj.tag_ids}, max_rating={filter_obj.max_rating}, months_back={filter_obj.months_back}")
        
        reviews = get_reviews_by_filter(filter_obj)
        
        logger.info(f"Found {len(reviews)} reviews matching filter {filter_id}")
        return jsonify({'reviews': reviews}), 200
        
    except Exception as e:
        logger.error(f"Error fetching filtered reviews for filter {filter_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


def register_reviews_routes(app):
    """Register reviews routes with the Flask app."""
    app.register_blueprint(reviews_bp)

