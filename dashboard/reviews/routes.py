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

from dashboard.reviews.query import (
    add_review_resolution_note,
    get_review_resolution_detail,
    get_review_queue,
    get_review_resolutions,
    get_reviews_by_filter,
    mark_host_reviewed,
    update_review_resolution_rule,
    update_review_resolution,
    update_review_resolution_stage,
)
from dashboard.reviews.automation import (
    HostReviewPublishingUnavailable,
    ReviewAutomationDisabled,
    get_property_review_templates,
    get_review_automation_preview,
    perform_review_automation_action,
    update_property_review_templates,
)
from database.models import ReviewFilter, Tag, get_session
from database.schema import get_database_path
from dashboard.auth.decorators import approved_required, admin_required, check_feature_access
from dashboard.auth.session import get_current_user
from dashboard.auth.models import get_all_users
import logging

logger = logging.getLogger(__name__)

reviews_bp = Blueprint('reviews', __name__, url_prefix='/reviews')


@reviews_bp.before_request
def require_reviews_access():
    return check_feature_access('reviews')


@reviews_bp.route('/')
@approved_required
def reviews_page():
    """Reviews page."""
    return render_template('reviews/index.html', current_user=get_current_user())


@reviews_bp.route('/resolutions')
@approved_required
def review_resolutions_page():
    """Review resolution swim-lane page."""
    return render_template('reviews/resolutions.html', current_user=get_current_user())


def _tag_ids_from_request():
    tag_ids_param = request.args.get('tag_ids')
    if not tag_ids_param:
        return None
    try:
        tag_ids = json.loads(tag_ids_param)
        return tag_ids if isinstance(tag_ids, list) else ([tag_ids] if tag_ids else None)
    except (json.JSONDecodeError, ValueError, TypeError):
        return [int(tag_id.strip()) for tag_id in tag_ids_param.split(',') if tag_id.strip().isdigit()]


@reviews_bp.route('/api/queue')
@reviews_bp.route('/api/unresponded')
@approved_required
def api_unresponded_reviews():
    """Get all reservations that remain inside the two-sided 14-day review window."""
    try:
        current_user = get_current_user()
        queue = get_review_queue(
            tag_ids=_tag_ids_from_request(),
            current_user_id=current_user.user_id,
        )
        return jsonify(queue), 200
    except Exception as e:
        logger.error(f"Error fetching review queue: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/queue/<int:reservation_id>/host-reviewed', methods=['POST'])
@approved_required
def api_mark_host_reviewed(reservation_id):
    """Mark the host-side review complete and run the two-sided review transition."""
    try:
        result = mark_host_reviewed(reservation_id, get_current_user().user_id)
        return jsonify(result), 200
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        logger.error(f"Error marking reservation {reservation_id} host reviewed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/templates')
@approved_required
def api_review_automation_templates():
    """Return property-specific templates for the human operator editor."""
    try:
        return jsonify(get_property_review_templates()), 200
    except Exception as e:
        logger.error('Error fetching review automation templates: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/templates/<int:listing_id>', methods=['PUT'])
@approved_required
def api_update_review_automation_template(listing_id):
    """Save one active property's chase-message and host-review templates."""
    data = request.get_json(silent=True) or {}
    try:
        result = update_property_review_templates(
            listing_id,
            data.get('chase_message_template'),
            data.get('host_review_template'),
            get_current_user().user_id,
        )
        return jsonify(result), 200
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error('Error updating review templates for listing %s: %s', listing_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/queue/<int:reservation_id>/automation-preview')
@approved_required
def api_review_automation_preview(reservation_id):
    """Render and validate the exact guest message or host review about to be used."""
    try:
        result = get_review_automation_preview(
            reservation_id,
            request.args.get('action', ''),
            get_current_user().user_id,
        )
        return jsonify(result), 200
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        logger.error('Error preparing review action for reservation %s: %s', reservation_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/queue/<int:reservation_id>/automation', methods=['POST'])
@approved_required
def api_perform_review_automation(reservation_id):
    """Execute the reviewed content using the configured guarded transport."""
    data = request.get_json(silent=True) or {}
    try:
        result = perform_review_automation_action(
            reservation_id,
            data.get('action_type', ''),
            data.get('content', ''),
            get_current_user().user_id,
        )
        return jsonify(result), 200
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except (ReviewAutomationDisabled, HostReviewPublishingUnavailable) as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        logger.error('Error executing review action for reservation %s: %s', reservation_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 502


@reviews_bp.route('/api/resolutions')
@approved_required
def api_review_resolutions():
    """Get special review-resolution tickets arranged into swim lanes."""
    try:
        payload = get_review_resolutions(get_current_user().user_id)
        payload['operators'] = [
            {
                'user_id': user.user_id,
                'name': user.name or user.email,
                'email': user.email,
            }
            for user in get_all_users()
            if user.is_approved
        ]
        return jsonify(payload), 200
    except Exception as e:
        logger.error(f"Error fetching review resolutions: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/resolution-rules', methods=['PUT'])
@approved_required
def api_update_review_resolution_rule():
    """Set the strict bad-review threshold for one portfolio."""
    data = request.get_json(silent=True) or {}
    try:
        result = update_review_resolution_rule(
            data.get('portfolio'),
            data.get('bad_review_threshold'),
            get_current_user().user_id,
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error updating review resolution rule: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/resolutions/<int:ticket_id>/stage', methods=['PATCH'])
@approved_required
def api_update_review_resolution_stage(ticket_id):
    """Move a review-resolution ticket between service-recovery stages."""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(update_review_resolution_stage(ticket_id, data.get('stage'))), 200
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error updating review resolution {ticket_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/resolutions/<int:ticket_id>', methods=['GET', 'PATCH'])
@approved_required
def api_review_resolution_detail(ticket_id):
    """Read or edit one review-resolution case without requiring Tickets access."""
    try:
        if request.method == 'GET':
            return jsonify(get_review_resolution_detail(ticket_id)), 200
        return jsonify(update_review_resolution(ticket_id, request.get_json(silent=True) or {})), 200
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error('Error editing review resolution %s: %s', ticket_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@reviews_bp.route('/api/resolutions/<int:ticket_id>/notes', methods=['POST'])
@approved_required
def api_add_review_resolution_note(ticket_id):
    """Append a timestamped operator note to a review-resolution case."""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(add_review_resolution_note(
            ticket_id,
            get_current_user().user_id,
            data.get('note_text'),
        )), 201
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error('Error adding note to review resolution %s: %s', ticket_id, e, exc_info=True)
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
        
        # Get sort parameters from query string
        sort_by = request.args.get('sort_by', 'review_date')  # Default to review_date
        sort_order = request.args.get('sort_order', 'desc')  # Default to desc
        
        # Validate sort_by
        valid_sort_fields = ['review_date', 'overall_rating']
        if sort_by not in valid_sort_fields:
            sort_by = 'review_date'
        
        # Validate sort_order
        if sort_order not in ['asc', 'desc']:
            sort_order = 'desc'
        
        logger.info(f"Filter found: tag_ids={filter_obj.tag_ids}, max_rating={filter_obj.max_rating}, months_back={filter_obj.months_back}, sort_by={sort_by}, sort_order={sort_order}")
        
        reviews = get_reviews_by_filter(filter_obj, sort_by=sort_by, sort_order=sort_order)
        
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
