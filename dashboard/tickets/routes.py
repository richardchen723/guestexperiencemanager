#!/usr/bin/env python3
"""
Ticket API routes.
"""

import sys
import os
from datetime import datetime, date
from flask import Blueprint, render_template, jsonify, request, redirect, url_for

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.tickets.models import (
    Ticket, TicketComment, get_session, create_ticket, get_ticket,
    get_tickets, update_ticket, delete_ticket, add_ticket_comment, get_ticket_comments,
    init_ticket_database, TICKET_CATEGORIES
)
from dashboard.auth.decorators import approved_required, admin_required
from dashboard.auth.session import get_current_user
from dashboard.auth.models import get_all_users
from database.models import get_session as get_main_session, Listing
from dashboard.ai.cache import get_cached_insights
import dashboard.config as config

tickets_bp = Blueprint('tickets', __name__, url_prefix='/tickets')

# Constants
TICKET_STATUSES = ['Open', 'Assigned', 'In Progress', 'Blocked', 'Resolved', 'Closed']
TICKET_PRIORITIES = ['Low', 'Medium', 'High', 'Critical']


@tickets_bp.route('/')
@approved_required
def tickets_list():
    """Ticket list page."""
    return render_template('tickets/list.html', current_user=get_current_user())


@tickets_bp.route('/<int:ticket_id>/page')
@approved_required
def ticket_detail_page(ticket_id):
    """Ticket detail page."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return "Ticket not found", 404
    
    # Get listing info
    main_session = get_main_session(config.MAIN_DATABASE_PATH)
    try:
        listing = main_session.query(Listing).filter(Listing.listing_id == ticket.listing_id).first()
    finally:
        main_session.close()
    
    return render_template('tickets/detail.html', 
                         ticket=ticket, 
                         listing=listing,
                         current_user=get_current_user())


@tickets_bp.route('/create')
@approved_required
def ticket_create_form():
    """Ticket creation form."""
    # Get all listings for dropdown
    main_session = get_main_session(config.MAIN_DATABASE_PATH)
    listings = []
    try:
        listings = main_session.query(Listing).order_by(Listing.name).all()
    finally:
        main_session.close()
    
    listing_id = request.args.get('listing_id', type=int)
    issue_title = request.args.get('issue_title', '')
    
    return render_template('tickets/form.html', 
                         listings=listings,
                         listing_id=listing_id,
                         issue_title=issue_title,
                         current_user=get_current_user())


@tickets_bp.route('/<int:ticket_id>/edit')
@approved_required
def ticket_edit_form(ticket_id):
    """Ticket edit form."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return "Ticket not found", 404
    
    # Check if user can edit (creator, assigned user, or admin)
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('auth.login'))
    
    if (ticket.created_by != current_user.user_id and 
        ticket.assigned_user_id != current_user.user_id and 
        not current_user.is_admin()):
        return "You don't have permission to edit this ticket", 403
    
    # Get all listings for dropdown
    main_session = get_main_session(config.MAIN_DATABASE_PATH)
    listings = []
    try:
        listings = main_session.query(Listing).order_by(Listing.name).all()
    finally:
        main_session.close()
    
    return render_template('tickets/form.html', 
                         ticket=ticket,
                         listings=listings,
                         current_user=current_user)


# API Routes

@tickets_bp.route('/api/tickets', methods=['GET'])
@approved_required
def api_list_tickets():
    """Get list of tickets with optional filters."""
    listing_id = request.args.get('listing_id', type=int)
    assigned_user_id = request.args.get('assigned_user_id', type=int)
    status = request.args.get('status', type=str)
    priority = request.args.get('priority', type=str)
    category = request.args.get('category', type=str)
    issue_title = request.args.get('issue_title', type=str)
    # Normalize issue_title (trim whitespace)
    if issue_title:
        issue_title = issue_title.strip()
    
    tickets = get_tickets(
        listing_id=listing_id,
        assigned_user_id=assigned_user_id,
        status=status,
        priority=priority,
        category=category,
        issue_title=issue_title
    )
    
    # Get listing names for display
    main_session = get_main_session(config.MAIN_DATABASE_PATH)
    listing_map = {}
    try:
        listings = main_session.query(Listing).all()
        listing_map = {l.listing_id: {'name': l.name, 'address': l.address} for l in listings}
    finally:
        main_session.close()
    
    result = []
    for ticket in tickets:
        ticket_dict = ticket.to_dict(include_comments=False)
        if ticket.listing_id in listing_map:
            ticket_dict['listing'] = listing_map[ticket.listing_id]
        result.append(ticket_dict)
    
    return jsonify(result)


@tickets_bp.route('/api/tickets/<int:ticket_id>', methods=['GET'])
@approved_required
def api_get_ticket(ticket_id):
    """Get a single ticket with comments."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    # Get listing info
    main_session = get_main_session(config.MAIN_DATABASE_PATH)
    listing = None
    try:
        listing = main_session.query(Listing).filter(Listing.listing_id == ticket.listing_id).first()
    finally:
        main_session.close()
    
    # Load comments
    comments = get_ticket_comments(ticket_id)
    ticket_dict = ticket.to_dict(include_comments=False)
    ticket_dict['comments'] = [comment.to_dict() for comment in comments]
    
    if listing:
        ticket_dict['listing'] = {
            'listing_id': listing.listing_id,
            'name': listing.name,
            'address': listing.address,
            'city': listing.city
        }
    
    return jsonify(ticket_dict)


@tickets_bp.route('/api/tickets', methods=['POST'])
@approved_required
def api_create_ticket():
    """Create a new ticket."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request data'}), 400
    
    # Validate required fields
    listing_id_raw = data.get('listing_id')
    if not listing_id_raw:
        return jsonify({'error': 'listing_id is required'}), 400
    
    try:
        listing_id = int(listing_id_raw)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid listing_id. Must be an integer.'}), 400
    
    issue_title = data.get('issue_title', '').strip()
    title = data.get('title', '').strip()
    if not issue_title:
        return jsonify({'error': 'issue_title is required'}), 400
    if not title:
        return jsonify({'error': 'title is required'}), 400
    
    # Parse optional fields
    description = data.get('description', '').strip() or None
    
    # Parse assigned_user_id - can be int, string, or null
    assigned_user_id = None
    assigned_user_id_raw = data.get('assigned_user_id')
    if assigned_user_id_raw:
        try:
            assigned_user_id = int(assigned_user_id_raw)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid assigned_user_id. Must be an integer.'}), 400
    
    status = data.get('status', 'Open')
    priority = data.get('priority', 'Low')
    category = data.get('category', 'other')
    
    # Validate category
    if category not in TICKET_CATEGORIES:
        return jsonify({'error': f'Invalid category. Must be one of: {", ".join(TICKET_CATEGORIES)}'}), 400
    
    due_date_str = data.get('due_date')
    due_date = None
    if due_date_str:
        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid due_date format. Use YYYY-MM-DD'}), 400
    
    try:
        ticket = create_ticket(
            listing_id=listing_id,
            issue_title=issue_title,
            title=title,
            description=description,
            assigned_user_id=assigned_user_id,
            status=status,
            priority=priority,
            category=category,
            due_date=due_date,
            created_by=current_user.user_id
        )
        
        # Get the ticket again with relationships loaded to avoid lazy loading issues
        from sqlalchemy.orm import joinedload
        session = get_session()
        try:
            ticket_with_rels = session.query(Ticket).options(
                joinedload(Ticket.assigned_user),
                joinedload(Ticket.creator)
            ).filter(Ticket.ticket_id == ticket.ticket_id).first()
            
            if ticket_with_rels:
                ticket_dict = ticket_with_rels.to_dict(include_comments=False)
            else:
                ticket_dict = ticket.to_dict(include_comments=False)
        finally:
            session.close()
        
        return jsonify(ticket_dict), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@tickets_bp.route('/api/tickets/<int:ticket_id>', methods=['PUT'])
@approved_required
def api_update_ticket(ticket_id):
    """Update a ticket."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    ticket = get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    # Check permissions: creator, assigned user, or admin can edit
    if (ticket.created_by != current_user.user_id and 
        ticket.assigned_user_id != current_user.user_id and 
        not current_user.is_admin()):
        return jsonify({'error': 'Permission denied'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request data'}), 400
    
    # Build update dict
    update_data = {}
    
    if 'title' in data:
        title = data['title'].strip()
        if title:
            update_data['title'] = title
    
    if 'description' in data:
        update_data['description'] = data['description'].strip() or None
    
    if 'assigned_user_id' in data:
        assigned_user_id_raw = data['assigned_user_id']
        if assigned_user_id_raw:
            try:
                update_data['assigned_user_id'] = int(assigned_user_id_raw)
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid assigned_user_id. Must be an integer.'}), 400
        else:
            update_data['assigned_user_id'] = None
    
    if 'status' in data:
        if data['status'] in TICKET_STATUSES:
            update_data['status'] = data['status']
    
    if 'priority' in data:
        if data['priority'] in TICKET_PRIORITIES:
            update_data['priority'] = data['priority']
    
    if 'category' in data:
        if data['category'] in TICKET_CATEGORIES:
            update_data['category'] = data['category']
        else:
            return jsonify({'error': f'Invalid category. Must be one of: {", ".join(TICKET_CATEGORIES)}'}), 400
    
    if 'due_date' in data:
        due_date_str = data['due_date']
        if due_date_str:
            try:
                update_data['due_date'] = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Invalid due_date format. Use YYYY-MM-DD'}), 400
        else:
            update_data['due_date'] = None
    
    try:
        updated_ticket = update_ticket(ticket_id, **update_data)
        if updated_ticket:
            return jsonify(updated_ticket.to_dict(include_comments=False))
        return jsonify({'error': 'Failed to update ticket'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@tickets_bp.route('/api/tickets/<int:ticket_id>', methods=['DELETE'])
@admin_required
def api_delete_ticket(ticket_id):
    """Delete a ticket (admin only)."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    try:
        success = delete_ticket(ticket_id)
        if success:
            return jsonify({'message': 'Ticket deleted successfully'})
        return jsonify({'error': 'Failed to delete ticket'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@tickets_bp.route('/api/tickets/<int:ticket_id>/comments', methods=['GET'])
@approved_required
def api_get_comments(ticket_id):
    """Get all comments for a ticket."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    comments = get_ticket_comments(ticket_id)
    return jsonify([comment.to_dict() for comment in comments])


@tickets_bp.route('/api/tickets/<int:ticket_id>/comments', methods=['POST'])
@approved_required
def api_add_comment(ticket_id):
    """Add a comment to a ticket."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    ticket = get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request data'}), 400
    
    comment_text = data.get('comment_text', '').strip()
    if not comment_text:
        return jsonify({'error': 'comment_text is required'}), 400
    
    try:
        comment = add_ticket_comment(ticket_id, current_user.user_id, comment_text)
        return jsonify(comment.to_dict()), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@tickets_bp.route('/api/tickets/suggest', methods=['POST'])
@approved_required
def api_suggest_ticket():
    """Generate AI suggestions for a ticket from an issue."""
    from dashboard.tickets.ai_suggestions import generate_ticket_suggestions
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request data'}), 400
    
    listing_id = data.get('listing_id')
    issue_title = data.get('issue_title', '')
    issue_details = data.get('issue_details', '')
    
    if not listing_id:
        return jsonify({'error': 'listing_id is required'}), 400
    if not issue_title:
        return jsonify({'error': 'issue_title is required'}), 400
    
    try:
        suggestions = generate_ticket_suggestions(listing_id, issue_title, issue_details)
        
        # Convert suggested_due_date to due_date for frontend
        result = {
            'title': suggestions.get('title', ''),
            'description': suggestions.get('description', ''),
            'priority': suggestions.get('priority', 'Medium'),
            'category': suggestions.get('category', 'other'),
            'due_date': suggestions.get('suggested_due_date', '')
        }
        
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@tickets_bp.route('/api/listings/<int:listing_id>/issues', methods=['GET'])
@approved_required
def api_get_listing_issues(listing_id):
    """Get all issues for a listing from cached insights."""
    insights = get_cached_insights(listing_id)
    
    if not insights:
        return jsonify({'issues': []})
    
    issues = insights.get('issues', [])
    
    # Normalize issues to ensure they have title and details
    normalized_issues = []
    for issue in issues:
        if isinstance(issue, dict):
            normalized_issues.append({
                'title': issue.get('title', ''),
                'details': issue.get('details', '')
            })
        elif isinstance(issue, str):
            normalized_issues.append({
                'title': issue,
                'details': ''
            })
    
    return jsonify({'issues': normalized_issues})


@tickets_bp.route('/api/users', methods=['GET'])
@approved_required
def api_users_list():
    """Get all approved users for assignment dropdown."""
    users = get_all_users()
    approved_users = [u for u in users if u.is_approved]
    
    return jsonify([{
        'user_id': u.user_id,
        'name': u.name or u.email,
        'email': u.email,
        'picture_url': u.picture_url
    } for u in approved_users])


def register_ticket_routes(app):
    """Register ticket routes with the Flask app."""
    # Initialize ticket database tables
    init_ticket_database()
    
    # Register blueprint
    app.register_blueprint(tickets_bp)
