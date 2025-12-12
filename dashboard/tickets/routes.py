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
    Ticket, TicketComment, TicketTag, TicketImage, CommentImage, get_session, create_ticket, get_ticket,
    get_tickets, update_ticket, delete_ticket, add_ticket_comment, get_ticket_comments, delete_ticket_comment,
    init_ticket_database, TICKET_CATEGORIES
)
from dashboard.tickets.recurring_tasks import process_recurring_tasks
from dashboard.tickets.image_utils import save_uploaded_image
from pathlib import Path
from flask import send_from_directory
from database.models import Tag, ListingTag, get_session as get_main_session
from sqlalchemy import func, or_, and_, String, cast
from sqlalchemy.orm import joinedload
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
    """Get list of tickets with optional filters including tags."""
    listing_id = request.args.get('listing_id', type=int)
    assigned_user_id = request.args.get('assigned_user_id', type=int)
    status_param = request.args.get('status', type=str)  # Can be comma-separated
    priority = request.args.get('priority', type=str)
    category = request.args.get('category', type=str)
    issue_title = request.args.get('issue_title', type=str)
    tags_param = request.args.get('tags', '')
    tag_logic = request.args.get('tag_logic', 'AND').upper()  # AND or OR
    search_query = request.args.get('search', type=str)
    
    # Normalize issue_title (trim whitespace)
    if issue_title:
        issue_title = issue_title.strip()
    
    session = get_session()
    main_session = get_main_session(config.MAIN_DATABASE_PATH)
    
    try:
        # Start with base query
        query = session.query(Ticket)
        
        # Apply tag filtering if provided
        if tags_param:
            tag_names = [t.strip().lower() for t in tags_param.split(',') if t.strip()]
            if tag_names:
                # Get tag IDs from main database
                tags = main_session.query(Tag).filter(Tag.name.in_(tag_names)).all()
                tag_ids = [t.tag_id for t in tags]
                
                if tag_ids:
                    if tag_logic == 'OR':
                        # At least one tag must match
                        ticket_ids_with_tags = session.query(TicketTag.ticket_id).filter(
                            TicketTag.tag_id.in_(tag_ids)
                        ).distinct().all()
                    else:
                        # All tags must match (AND)
                        ticket_ids_with_tags = session.query(TicketTag.ticket_id).filter(
                            TicketTag.tag_id.in_(tag_ids)
                        ).group_by(TicketTag.ticket_id).having(
                            func.count(TicketTag.tag_id.distinct()) == len(tag_ids)
                        ).all()
                    
                    ticket_ids = [row[0] for row in ticket_ids_with_tags]
                    query = query.filter(Ticket.ticket_id.in_(ticket_ids))
                else:
                    # No tags found, return empty result
                    return jsonify([])
        
        # Apply other filters
        if listing_id:
            # Handle "General" tickets - if listing_id is 0 or "general", show tickets with NULL listing_id
            if listing_id == 0 or str(listing_id).lower() == 'general':
                query = query.filter(Ticket.listing_id.is_(None))
            else:
                query = query.filter(Ticket.listing_id == listing_id)
        if assigned_user_id:
            query = query.filter(Ticket.assigned_user_id == assigned_user_id)
        if status_param:
            # Support multiple statuses (comma-separated)
            statuses = [s.strip() for s in status_param.split(',') if s.strip()]
            if statuses:
                query = query.filter(Ticket.status.in_(statuses))
        if priority:
            query = query.filter(Ticket.priority == priority)
        if category:
            query = query.filter(Ticket.category == category)
        
        # Apply text search if provided
        if search_query:
            search_query = search_query.strip()
            if search_query:
                # func, or_, and_, String, and cast are already imported at module level
                
                search_pattern_lower = f"%{search_query.lower()}%"
                search_pattern = f"%{search_query}%"
                
                # Build search conditions - search across ticket_id, title, description, and issue_title
                # Handle NULL values: only search in non-NULL fields
                search_conditions = [
                    cast(Ticket.ticket_id, String).like(search_pattern),
                    func.lower(Ticket.title).like(search_pattern_lower)
                ]
                
                # Add description search (only if not NULL)
                search_conditions.append(
                    and_(Ticket.description.isnot(None), func.lower(Ticket.description).like(search_pattern_lower))
                )
                
                # Add issue_title search (only if not NULL)
                search_conditions.append(
                    and_(Ticket.issue_title.isnot(None), func.lower(Ticket.issue_title).like(search_pattern_lower))
                )
                
                # Apply the search filter - match if any condition is true
                query = query.filter(or_(*search_conditions))
        
        tickets = query.order_by(Ticket.created_at.desc()).all()
        
        # Filter by issue_title in Python if provided
        if issue_title:
            issue_title_normalized = issue_title.strip().lower()
            filtered_tickets = []
            for t in tickets:
                if t.issue_title:
                    ticket_issue_normalized = t.issue_title.strip().lower()
                    if ticket_issue_normalized == issue_title_normalized:
                        filtered_tickets.append(t)
                    elif (len(ticket_issue_normalized) > 0 and len(issue_title_normalized) > 0):
                        shorter = min(ticket_issue_normalized, issue_title_normalized, key=len)
                        longer = max(ticket_issue_normalized, issue_title_normalized, key=len)
                        if len(shorter) >= len(longer) * 0.8 and shorter in longer:
                            filtered_tickets.append(t)
            tickets = filtered_tickets
        
        # Get listing names for display (only for tickets with listing_id)
        listing_map = {}
        if tickets and any(t.listing_id for t in tickets):
            listings = main_session.query(Listing).all()
            listing_map = {
                l.listing_id: {
                    'name': l.name,
                    'internal_listing_name': l.internal_listing_name,
                    'address': l.address
                } for l in listings
            }
        
        # Get tags for all tickets
        ticket_ids = [t.ticket_id for t in tickets]
        ticket_tags_map = {}
        if ticket_ids:
            ticket_tags = session.query(TicketTag).filter(
                TicketTag.ticket_id.in_(ticket_ids)
            ).all()
            # Get tag details from main database
            tag_ids = list(set([tt.tag_id for tt in ticket_tags]))
            if tag_ids:
                tags = main_session.query(Tag).filter(Tag.tag_id.in_(tag_ids)).all()
                tag_map = {t.tag_id: {'tag_id': t.tag_id, 'name': t.name, 'color': t.color} for t in tags}
                
                for tt in ticket_tags:
                    if tt.ticket_id not in ticket_tags_map:
                        ticket_tags_map[tt.ticket_id] = []
                    if tt.tag_id in tag_map:
                        ticket_tags_map[tt.ticket_id].append({
                            **tag_map[tt.tag_id],
                            'is_inherited': tt.is_inherited
                        })
        
        result = []
        for ticket in tickets:
            ticket_dict = ticket.to_dict(include_comments=False)
            if ticket.listing_id and ticket.listing_id in listing_map:
                ticket_dict['listing'] = listing_map[ticket.listing_id]
            ticket_dict['tags'] = ticket_tags_map.get(ticket.ticket_id, [])
            result.append(ticket_dict)
        
        return jsonify(result)
    finally:
        session.close()
        main_session.close()


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
            'internal_listing_name': listing.internal_listing_name,
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
    
    # Validate listing_id (optional - can be None for general tickets)
    listing_id = None
    listing_id_raw = data.get('listing_id')
    if listing_id_raw:
        try:
            listing_id = int(listing_id_raw)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid listing_id. Must be an integer or empty for general tickets.'}), 400
    
    issue_title = data.get('issue_title', '').strip()
    title = data.get('title', '').strip()
    
    # For general tickets (no listing_id), issue_title can be empty - use title as fallback
    if not listing_id and not issue_title:
        issue_title = title  # Use title as issue_title for general tickets
    
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
    
    # Parse recurring task fields
    is_recurring = data.get('is_recurring', False)
    frequency_value = None
    frequency_unit = None
    initial_due_date = None
    recurring_admin_id = None
    reopen_days_before_due_date = None
    
    if is_recurring:
        frequency_value_raw = data.get('frequency_value')
        frequency_unit_raw = data.get('frequency_unit')
        reopen_days_raw = data.get('reopen_days_before_due_date')
        
        # Validate frequency
        if frequency_value_raw:
            try:
                frequency_value = int(frequency_value_raw)
                if frequency_value <= 0:
                    return jsonify({'error': 'frequency_value must be greater than 0'}), 400
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid frequency_value. Must be an integer.'}), 400
        
        if frequency_unit_raw:
            if frequency_unit_raw not in ['days', 'months']:
                return jsonify({'error': 'Invalid frequency_unit. Must be "days" or "months".'}), 400
            frequency_unit = frequency_unit_raw
        
        if not frequency_value or not frequency_unit:
            return jsonify({'error': 'frequency_value and frequency_unit are required for recurring tasks'}), 400
        
        # Validate reopen_days_before_due_date
        if reopen_days_raw is not None:
            try:
                reopen_days_before_due_date = int(reopen_days_raw)
                if reopen_days_before_due_date < 0:
                    return jsonify({'error': 'reopen_days_before_due_date must be >= 0'}), 400
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid reopen_days_before_due_date. Must be an integer.'}), 400
        else:
            reopen_days_before_due_date = 10  # Default
        
        # Set initial_due_date to due_date if provided
        if due_date:
            initial_due_date = due_date
        
        # Find admin user for assignment
        from dashboard.tickets.recurring_tasks import get_admin_user
        admin_user = get_admin_user()
        if admin_user:
            recurring_admin_id = admin_user.user_id
            # Also assign the ticket to admin
            if not assigned_user_id:
                assigned_user_id = admin_user.user_id
    
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
            created_by=current_user.user_id,
            is_recurring=is_recurring,
            frequency_value=frequency_value,
            frequency_unit=frequency_unit,
            initial_due_date=initial_due_date,
            recurring_admin_id=recurring_admin_id,
            reopen_days_before_due_date=reopen_days_before_due_date
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
        
        # Log ticket creation activity
        try:
            from dashboard.activities.logger import log_ticket_activity
            log_ticket_activity(
                user_id=current_user.user_id,
                action='create',
                ticket_id=ticket.ticket_id,
                metadata={
                    'title': ticket.title,
                    'status': ticket.status,
                    'assigned_user_id': assigned_user_id,
                    'priority': priority,
                    'category': category
                }
            )
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error logging ticket creation activity: {e}", exc_info=True)
        
        # Send notification if ticket was assigned during creation
        if assigned_user_id:
            try:
                from dashboard.notifications.helpers import send_assignment_notification
                send_assignment_notification(assigned_user_id, ticket.ticket_id)
            except Exception as e:
                # Log but don't fail ticket creation if notification fails
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Error sending assignment notification: {e}", exc_info=True)
        
        # Parse mentions from description and send notifications
        if description:
            try:
                from dashboard.notifications.mention_parser import parse_mentions
                from dashboard.notifications.helpers import send_mention_notification
                
                mentioned_users = parse_mentions(description)
                mentioner_name = current_user.name or current_user.email
                
                for mentioned_user_id, mention_text in mentioned_users:
                    # Don't notify if user mentioned themselves
                    if mentioned_user_id != current_user.user_id:
                        send_mention_notification(mentioned_user_id, ticket.ticket_id, description, mentioner_name)
            except Exception as e:
                # Log but don't fail ticket creation if notification fails
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Error sending mention notifications from ticket description: {e}", exc_info=True)
        
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
    
    # Handle recurring task fields
    if 'is_recurring' in data:
        is_recurring = data.get('is_recurring', False)
        update_data['is_recurring'] = is_recurring
        
        if not is_recurring:
            # If disabling recurring, also disable is_recurring_active
            update_data['is_recurring_active'] = False
        else:
            # If enabling recurring, ensure is_recurring_active is True
            update_data['is_recurring_active'] = True
            
            # Update frequency fields if provided
            if 'frequency_value' in data:
                frequency_value_raw = data['frequency_value']
                if frequency_value_raw:
                    try:
                        frequency_value = int(frequency_value_raw)
                        if frequency_value <= 0:
                            return jsonify({'error': 'frequency_value must be greater than 0'}), 400
                        update_data['frequency_value'] = frequency_value
                    except (ValueError, TypeError):
                        return jsonify({'error': 'Invalid frequency_value. Must be an integer.'}), 400
            
            if 'frequency_unit' in data:
                frequency_unit = data['frequency_unit']
                if frequency_unit:
                    if frequency_unit not in ['days', 'months']:
                        return jsonify({'error': 'Invalid frequency_unit. Must be "days" or "months".'}), 400
                    update_data['frequency_unit'] = frequency_unit
            
            # Set initial_due_date if not set and due_date is provided
            if 'initial_due_date' in data:
                initial_due_date_str = data['initial_due_date']
                if initial_due_date_str:
                    try:
                        update_data['initial_due_date'] = datetime.strptime(initial_due_date_str, '%Y-%m-%d').date()
                    except ValueError:
                        return jsonify({'error': 'Invalid initial_due_date format. Use YYYY-MM-DD'}), 400
                else:
                    update_data['initial_due_date'] = None
            elif not ticket.initial_due_date and ticket.due_date:
                # If initial_due_date is not set and we have a due_date, use it
                update_data['initial_due_date'] = ticket.due_date
            
            # Update recurring_admin_id if provided
            if 'recurring_admin_id' in data:
                admin_id_raw = data['recurring_admin_id']
                if admin_id_raw:
                    try:
                        update_data['recurring_admin_id'] = int(admin_id_raw)
                    except (ValueError, TypeError):
                        return jsonify({'error': 'Invalid recurring_admin_id. Must be an integer.'}), 400
                else:
                    update_data['recurring_admin_id'] = None
    
    if 'is_recurring_active' in data:
        update_data['is_recurring_active'] = bool(data['is_recurring_active'])
    
    if 'reopen_days_before_due_date' in data:
        reopen_days_raw = data['reopen_days_before_due_date']
        if reopen_days_raw is not None:
            try:
                reopen_days = int(reopen_days_raw)
                if reopen_days < 0:
                    return jsonify({'error': 'reopen_days_before_due_date must be >= 0'}), 400
                update_data['reopen_days_before_due_date'] = reopen_days
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid reopen_days_before_due_date. Must be an integer.'}), 400
    
    try:
        # Track old values for notifications and logging
        # IMPORTANT: Get old values BEFORE update_ticket() modifies the ticket object
        old_assigned_user_id = ticket.assigned_user_id
        old_status = ticket.status or 'Open'  # Ensure we have a default if None
        
        updated_ticket = update_ticket(ticket_id, **update_data)
        
        if updated_ticket:
            # Log activity for ticket updates
            try:
                from dashboard.activities.logger import log_ticket_activity
                
                # Check what changed - get new values from the updated ticket object
                new_assigned_user_id = updated_ticket.assigned_user_id
                new_status = updated_ticket.status or 'Open'  # Get from updated ticket, ensure not None
                
                # Log status change if it changed
                if new_status != old_status:
                    # #region agent log
                    import json
                    debug_log_path = '/Users/richardchen/projects/hostaway-messages/.cursor/debug.log'
                    try:
                        with open(debug_log_path, 'a') as f:
                            f.write(json.dumps({
                                'sessionId': 'debug-session',
                                'runId': 'run1',
                                'hypothesisId': 'H2',
                                'location': 'routes.py:686',
                                'message': 'About to log status_change',
                                'data': {
                                    'old_status': old_status,
                                    'old_status_type': type(old_status).__name__,
                                    'new_status': new_status,
                                    'new_status_type': type(new_status).__name__,
                                    'ticket_id': ticket_id,
                                    'ticket_title': ticket.title
                                },
                                'timestamp': int(__import__('datetime').datetime.utcnow().timestamp() * 1000)
                            }) + '\n')
                    except: pass
                    # #endregion
                    metadata_dict = {
                        'title': ticket.title,
                        'old_status': old_status or 'Open',  # Ensure not None
                        'new_status': new_status or 'Open'   # Ensure not None
                    }
                    # #region agent log
                    try:
                        with open(debug_log_path, 'a') as f:
                            f.write(json.dumps({
                                'sessionId': 'debug-session',
                                'runId': 'run1',
                                'hypothesisId': 'H2',
                                'location': 'routes.py:700',
                                'message': 'Metadata dict created',
                                'data': {
                                    'metadata_dict': metadata_dict,
                                    'old_status_in_dict': metadata_dict.get('old_status'),
                                    'new_status_in_dict': metadata_dict.get('new_status')
                                },
                                'timestamp': int(__import__('datetime').datetime.utcnow().timestamp() * 1000)
                            }) + '\n')
                    except: pass
                    # #endregion
                    log_ticket_activity(
                        user_id=current_user.user_id,
                        action='status_change',
                        ticket_id=ticket_id,
                        metadata=metadata_dict
                    )
                
                # Log assignment change if it changed
                if new_assigned_user_id != old_assigned_user_id:
                    log_ticket_activity(
                        user_id=current_user.user_id,
                        action='assign',
                        ticket_id=ticket_id,
                        metadata={
                            'title': ticket.title,
                            'old_assigned_user_id': old_assigned_user_id,
                            'new_assigned_user_id': new_assigned_user_id
                        }
                    )
                
                # Log general update if other fields changed (but not status or assignment)
                other_fields_changed = any(
                    key in update_data 
                    for key in ['title', 'description', 'priority', 'category', 'due_date']
                )
                if other_fields_changed and new_status == old_status and new_assigned_user_id == old_assigned_user_id:
                    log_ticket_activity(
                        user_id=current_user.user_id,
                        action='update',
                        ticket_id=ticket_id,
                        metadata={
                            'title': ticket.title,
                            'updated_fields': list(update_data.keys())
                        }
                    )
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Error logging ticket update activity: {e}", exc_info=True)
            
            # Send notifications for assignment and status changes
            try:
                from dashboard.notifications.helpers import send_assignment_notification, send_status_change_notification
                
                # Check if assignment changed
                new_assigned_user_id = update_data.get('assigned_user_id', old_assigned_user_id)
                if new_assigned_user_id and new_assigned_user_id != old_assigned_user_id:
                    # Only notify if assigned to a different user (not unassignment)
                    send_assignment_notification(new_assigned_user_id, ticket_id)
                
                # Check if status changed
                new_status = update_data.get('status', old_status)
                if new_status != old_status and updated_ticket.assigned_user_id:
                    # Only notify if ticket has an assigned user
                    changer_name = current_user.name or current_user.email
                    send_status_change_notification(updated_ticket.assigned_user_id, ticket_id, old_status, new_status, changer_name)
            except Exception as e:
                # Log but don't fail ticket update if notification fails
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Error sending notifications: {e}", exc_info=True)
            
            return jsonify(updated_ticket.to_dict(include_comments=False))
        return jsonify({'error': 'Failed to update ticket'}), 500
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        error_details = traceback.format_exc()
        logger.error(
            f"Error updating ticket {ticket_id}: {str(e)}",
            exc_info=True,
            extra={'ticket_id': ticket_id, 'update_data': update_data}
        )
        logger.debug(f"Full error traceback:\n{error_details}")
        return jsonify({'error': str(e)}), 500


@tickets_bp.route('/api/tickets/<int:ticket_id>', methods=['DELETE'])
@admin_required
def api_delete_ticket(ticket_id):
    """Delete a ticket (admin only)."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    ticket = get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    try:
        # Log ticket deletion activity
        try:
            from dashboard.activities.logger import log_ticket_activity
            log_ticket_activity(
                user_id=current_user.user_id,
                action='delete',
                ticket_id=ticket_id,
                metadata={
                    'title': ticket.title,
                    'status': ticket.status
                }
            )
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error logging ticket deletion activity: {e}", exc_info=True)
        
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
        
        # Log comment creation activity
        try:
            from dashboard.activities.logger import log_comment_activity
            log_comment_activity(
                user_id=current_user.user_id,
                action='create',
                ticket_id=ticket_id,
                comment_id=comment.comment_id,
                metadata={'comment_length': len(comment_text)}
            )
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error logging comment creation activity: {e}", exc_info=True)
        
        # Parse mentions and send notifications
        try:
            # #region agent log
            from dashboard.config import DEBUG_LOG_PATH
            with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"routes.py:747","message":"Starting mention parsing","data":{{"comment_text":"{comment_text[:50]}...","ticket_id":{ticket_id},"current_user_id":{current_user.user_id}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            # #endregion
            from dashboard.notifications.mention_parser import parse_mentions
            from dashboard.notifications.helpers import send_mention_notification
            
            mentioned_users = parse_mentions(comment_text)
            # #region agent log
            with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"routes.py:752","message":"Mentions parsed","data":{{"mentioned_users_count":{len(mentioned_users)},"mentioned_users":{mentioned_users}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            # #endregion
            mentioner_name = current_user.name or current_user.email
            
            for mentioned_user_id, mention_text in mentioned_users:
                # Don't notify if user mentioned themselves
                if mentioned_user_id != current_user.user_id:
                    # #region agent log
                    with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"routes.py:757","message":"Sending mention notification","data":{{"mentioned_user_id":{mentioned_user_id},"ticket_id":{ticket_id},"mention_text":"{mention_text}"}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
                    # #endregion
                    send_mention_notification(mentioned_user_id, ticket_id, comment_text, mentioner_name)
                else:
                    # #region agent log
                    with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"C","location":"routes.py:760","message":"Skipping self-mention","data":{{"mentioned_user_id":{mentioned_user_id},"current_user_id":{current_user.user_id}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
                    # #endregion
        except Exception as e:
            # Log but don't fail comment creation if notification fails
            import logging
            logger = logging.getLogger(__name__)
            # #region agent log
            try:
                from dashboard.config import DEBUG_LOG_PATH
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"routes.py:762","message":"Exception in mention notifications","data":{{"error":str(e)}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            logger.warning(f"Error sending mention notifications: {e}", exc_info=True)
        
        return jsonify(comment.to_dict()), 201
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        error_details = traceback.format_exc()
        logger.error(
            f"Error adding comment to ticket {ticket_id}: {str(e)}",
            exc_info=True,
            extra={'ticket_id': ticket_id, 'user_id': current_user.user_id}
        )
        logger.debug(f"Full error traceback:\n{error_details}")
        return jsonify({'error': str(e)}), 500


@tickets_bp.route('/api/tickets/<int:ticket_id>/comments/<int:comment_id>', methods=['DELETE'])
@approved_required
def api_delete_comment(ticket_id, comment_id):
    """Delete a comment from a ticket."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    session = get_session()
    try:
        comment = session.query(TicketComment).filter(
            TicketComment.comment_id == comment_id,
            TicketComment.ticket_id == ticket_id
        ).first()
        
        if not comment:
            return jsonify({'error': 'Comment not found'}), 404
        
        # Check permissions: user can only delete their own comments
        if comment.user_id != current_user.user_id and not current_user.is_admin():
            return jsonify({'error': 'Permission denied. You can only delete your own comments.'}), 403
        
        # Log comment deletion activity
        try:
            from dashboard.activities.logger import log_comment_activity
            log_comment_activity(
                user_id=current_user.user_id,
                action='delete',
                ticket_id=ticket_id,
                comment_id=comment_id,
                metadata={'comment_length': len(comment.comment_text) if comment.comment_text else 0}
            )
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error logging comment deletion activity: {e}", exc_info=True)
        
        # Delete the comment
        success = delete_ticket_comment(comment_id)
        if success:
            return jsonify({'message': 'Comment deleted successfully'}), 200
        return jsonify({'error': 'Failed to delete comment'}), 500
        
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        error_details = traceback.format_exc()
        logger.error(
            f"Error deleting comment {comment_id} from ticket {ticket_id}: {str(e)}",
            exc_info=True,
            extra={'ticket_id': ticket_id, 'comment_id': comment_id, 'user_id': current_user.user_id}
        )
        logger.debug(f"Full error traceback:\n{error_details}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


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


# Ticket Tag Endpoints
@tickets_bp.route('/api/tickets/<int:ticket_id>/tags', methods=['GET'])
@approved_required
def api_get_ticket_tags(ticket_id):
    """Get all tags for a ticket (with inherited flag)."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    session = get_session()
    main_session = get_main_session(config.MAIN_DATABASE_PATH)
    
    try:
        ticket_tags = session.query(TicketTag).filter(
            TicketTag.ticket_id == ticket_id
        ).all()
        
        tag_ids = [tt.tag_id for tt in ticket_tags]
        if not tag_ids:
            return jsonify([])
        
        tags = main_session.query(Tag).filter(Tag.tag_id.in_(tag_ids)).all()
        tag_map = {t.tag_id: {'tag_id': t.tag_id, 'name': t.name, 'color': t.color} for t in tags}
        
        result = []
        for tt in ticket_tags:
            if tt.tag_id in tag_map:
                result.append({
                    **tag_map[tt.tag_id],
                    'is_inherited': tt.is_inherited,
                    'created_at': tt.created_at.isoformat() if tt.created_at else None
                })
        
        return jsonify(result)
    finally:
        session.close()
        main_session.close()


@tickets_bp.route('/api/tickets/<int:ticket_id>/tags', methods=['POST'])
@approved_required
def api_add_ticket_tags(ticket_id):
    """Add tag(s) to a ticket."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request data'}), 400
    
    tag_names = data.get('tags', [])
    if not tag_names or not isinstance(tag_names, list):
        return jsonify({'error': 'tags must be a non-empty array'}), 400
    
    session = get_session()
    main_session = get_main_session(config.MAIN_DATABASE_PATH)
    
    try:
        added_tags = []
        for tag_name in tag_names:
            try:
                normalized_name = Tag.normalize_name(tag_name)
            except ValueError:
                continue  # Skip invalid tag names
            
            # Get or create tag in main database
            tag = main_session.query(Tag).filter(Tag.name == normalized_name).first()
            if not tag:
                tag = Tag(name=normalized_name)
                main_session.add(tag)
                main_session.flush()  # Get tag_id
            
            # Check if ticket already has this tag
            existing = session.query(TicketTag).filter(
                TicketTag.ticket_id == ticket_id,
                TicketTag.tag_id == tag.tag_id
            ).first()
            
            if not existing:
                ticket_tag = TicketTag(
                    ticket_id=ticket_id,
                    tag_id=tag.tag_id,
                    is_inherited=False  # User-added tags are not inherited
                )
                session.add(ticket_tag)
                added_tags.append({
                    'tag_id': tag.tag_id,
                    'name': tag.name,
                    'color': tag.color,
                    'is_inherited': False
                })
        
        main_session.commit()
        session.commit()
        return jsonify(added_tags), 201
    except Exception as e:
        main_session.rollback()
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()
        main_session.close()


@tickets_bp.route('/api/tickets/<int:ticket_id>/tags/<int:tag_id>', methods=['DELETE'])
@approved_required
def api_remove_ticket_tag(ticket_id, tag_id):
    """Remove a tag from a ticket."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    session = get_session()
    try:
        ticket_tag = session.query(TicketTag).filter(
            TicketTag.ticket_id == ticket_id,
            TicketTag.tag_id == tag_id
        ).first()
        
        if not ticket_tag:
            return jsonify({'error': 'Tag not found on ticket'}), 404
        
        session.delete(ticket_tag)
        session.commit()
        return jsonify({'message': 'Tag removed successfully'}), 200
    except Exception as e:
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


# Image Upload Endpoints

@tickets_bp.route('/api/tickets/<int:ticket_id>/images', methods=['POST'])
@approved_required
def api_upload_ticket_image(ticket_id):
    """Upload an image to a ticket."""
    from werkzeug.utils import secure_filename
    
    session = get_session()
    current_user = get_current_user()
    
    try:
        # Check if ticket exists
        ticket = session.query(Ticket).filter(Ticket.ticket_id == ticket_id).first()
        if not ticket:
            return jsonify({'error': 'Ticket not found'}), 404
        
        # Check permissions (creator, assignee, or admin)
        if not (current_user.is_admin() or 
                ticket.created_by == current_user.user_id or 
                ticket.assigned_user_id == current_user.user_id):
            return jsonify({'error': 'Permission denied'}), 403
        
        # Check if file was uploaded
        if 'image' not in request.files:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                f"No image file provided for ticket {ticket_id}",
                extra={'ticket_id': ticket_id, 'user_id': current_user.user_id, 'files': list(request.files.keys())}
            )
            return jsonify({'error': 'No image file provided'}), 400
        
        file = request.files['image']
        if file.filename == '':
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Empty filename for ticket {ticket_id} image upload",
                extra={'ticket_id': ticket_id, 'user_id': current_user.user_id}
            )
            return jsonify({'error': 'No file selected'}), 400
        
        # Save and optimize image
        file_path, file_name, width, height, thumbnail_path = save_uploaded_image(
            file, config.TICKET_IMAGES_DIR, f'tickets/{ticket_id}'
        )
        
        # Get file size
        full_path = Path(config.TICKET_IMAGES_DIR) / file_path
        file_size = full_path.stat().st_size
        
        # Determine MIME type
        mime_type = 'image/jpeg'  # All optimized images are JPEG
        
        # Create database record
        ticket_image = TicketImage(
            ticket_id=ticket_id,
            file_path=file_path,
            file_name=secure_filename(file_name),
            file_size=file_size,
            mime_type=mime_type,
            width=width,
            height=height,
            thumbnail_path=thumbnail_path,
            uploaded_by=current_user.user_id
        )
        session.add(ticket_image)
        session.commit()
        image_id = ticket_image.image_id
        
        # Re-query with eager loading to get relationships
        from sqlalchemy.orm import joinedload
        ticket_image = session.query(TicketImage).options(
            joinedload(TicketImage.uploader)
        ).filter(TicketImage.image_id == image_id).first()
        
        if ticket_image:
            # Access relationship while session is open
            _ = ticket_image.uploader
            # Expunge to detach from session but keep loaded relationship
            session.expunge(ticket_image)
            from dashboard.tickets.models import _safe_expunge
            _safe_expunge(session, ticket_image.uploader)
        
        return jsonify(ticket_image.to_dict()), 201
        
    except ValueError as e:
        session.rollback()
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        error_details = traceback.format_exc()
        logger.error(
            f"ValueError uploading ticket image for ticket {ticket_id}: {str(e)}",
            exc_info=True,
            extra={'ticket_id': ticket_id, 'user_id': current_user.user_id}
        )
        logger.debug(f"Full error traceback:\n{error_details}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        session.rollback()
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        error_details = traceback.format_exc()
        logger.error(
            f"Error uploading ticket image for ticket {ticket_id}: {str(e)}",
            exc_info=True,
            extra={'ticket_id': ticket_id, 'user_id': current_user.user_id}
        )
        logger.debug(f"Full error traceback:\n{error_details}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@tickets_bp.route('/api/tickets/<int:ticket_id>/images', methods=['GET'])
@approved_required
def api_get_ticket_images(ticket_id):
    """Get all images for a ticket."""
    from sqlalchemy.orm import joinedload
    session = get_session()
    try:
        ticket = session.query(Ticket).filter(Ticket.ticket_id == ticket_id).first()
        if not ticket:
            return jsonify({'error': 'Ticket not found'}), 404
        
        images = session.query(TicketImage).options(
            joinedload(TicketImage.uploader)
        ).filter(
            TicketImage.ticket_id == ticket_id
        ).order_by(TicketImage.created_at.asc()).all()
        
        # Access relationships while session is open
        for img in images:
            _ = img.uploader
            session.expunge(img)
            from dashboard.tickets.models import _safe_expunge
            _safe_expunge(session, img.uploader)
        
        result = [img.to_dict() for img in images]
        return jsonify(result)
    finally:
        session.close()


@tickets_bp.route('/api/tickets/<int:ticket_id>/images/<int:image_id>', methods=['DELETE'])
@approved_required
def api_delete_ticket_image(ticket_id, image_id):
    """Delete an image from a ticket."""
    session = get_session()
    current_user = get_current_user()
    
    try:
        ticket_image = session.query(TicketImage).filter(
            TicketImage.image_id == image_id,
            TicketImage.ticket_id == ticket_id
        ).first()
        
        if not ticket_image:
            return jsonify({'error': 'Image not found'}), 404
        
        # Check permissions
        ticket = session.query(Ticket).filter(Ticket.ticket_id == ticket_id).first()
        if not ticket:
            return jsonify({'error': 'Ticket not found'}), 404
        
        if not (current_user.is_admin() or 
                ticket.created_by == current_user.user_id or 
                ticket.assigned_user_id == current_user.user_id or
                ticket_image.uploaded_by == current_user.user_id):
            return jsonify({'error': 'Permission denied'}), 403
        
        # Delete files
        file_path = Path(config.TICKET_IMAGES_DIR) / ticket_image.file_path
        if file_path.exists():
            file_path.unlink()
        
        if ticket_image.thumbnail_path:
            thumb_path = Path(config.TICKET_IMAGES_DIR) / ticket_image.thumbnail_path
            if thumb_path.exists():
                thumb_path.unlink()
        
        # Delete database record
        session.delete(ticket_image)
        session.commit()
        
        return jsonify({'message': 'Image deleted successfully'}), 200
        
    except Exception as e:
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@tickets_bp.route('/api/comments/<int:comment_id>/images', methods=['POST'])
@approved_required
def api_upload_comment_image(comment_id):
    """Upload an image to a comment."""
    from werkzeug.utils import secure_filename
    import logging
    
    logger = logging.getLogger(__name__)
    session = get_session()
    current_user = get_current_user()
    
    try:
        # Check if comment exists
        comment = session.query(TicketComment).filter(TicketComment.comment_id == comment_id).first()
        if not comment:
            return jsonify({'error': 'Comment not found'}), 404
        
        # Check permissions (comment author or admin)
        if not (current_user.is_admin() or comment.user_id == current_user.user_id):
            return jsonify({'error': 'Permission denied'}), 403
        
        # Check if file was uploaded
        if 'image' not in request.files:
            logger.warning(
                f"No image file provided for comment {comment_id}",
                extra={'comment_id': comment_id, 'user_id': current_user.user_id, 'files': list(request.files.keys())}
            )
            return jsonify({'error': 'No image file provided'}), 400
        
        file = request.files['image']
        if file.filename == '':
            logger.warning(
                f"Empty filename for comment {comment_id} image upload",
                extra={'comment_id': comment_id, 'user_id': current_user.user_id}
            )
            return jsonify({'error': 'No file selected'}), 400
        
        # Log file details before processing
        logger.info(
            f"Received file for comment {comment_id}: filename={file.filename}, content_type={file.content_type}, "
            f"content_length={file.content_length if hasattr(file, 'content_length') else 'unknown'}",
            extra={'comment_id': comment_id, 'user_id': current_user.user_id, 'uploaded_filename': file.filename}
        )
        
        # CRITICAL: Reset file stream position before processing
        # This ensures we read the actual file content, especially important for mobile/iCloud uploads
        file.seek(0)
        
        # Save and optimize image
        file_path, file_name, width, height, thumbnail_path = save_uploaded_image(
            file, config.TICKET_IMAGES_DIR, f'comments/{comment_id}'
        )
        
        # Log image upload details for debugging
        logger.info(
            f"Uploading image to comment {comment_id}: file_path={file_path}, file_name={file_name}",
            extra={'comment_id': comment_id, 'user_id': current_user.user_id, 'file_path': file_path, 'file_name': file_name}
        )
        
        # Get file size
        full_path = Path(config.TICKET_IMAGES_DIR) / file_path
        file_size = full_path.stat().st_size
        
        # Determine MIME type
        mime_type = 'image/jpeg'  # All optimized images are JPEG
        
        # Create database record
        comment_image = CommentImage(
            comment_id=comment_id,
            file_path=file_path,
            file_name=secure_filename(file_name),
            file_size=file_size,
            mime_type=mime_type,
            width=width,
            height=height,
            thumbnail_path=thumbnail_path,
            uploaded_by=current_user.user_id
        )
        session.add(comment_image)
        session.commit()
        image_id = comment_image.image_id
        
        # Log the created image_id for debugging
        logger.info(
            f"Created comment image record: image_id={image_id}, comment_id={comment_id}, file_path={file_path}",
            extra={'image_id': image_id, 'comment_id': comment_id, 'file_path': file_path, 'user_id': current_user.user_id}
        )
        
        # Re-query with eager loading to get relationships
        from sqlalchemy.orm import joinedload
        comment_image = session.query(CommentImage).options(
            joinedload(CommentImage.uploader)
        ).filter(CommentImage.image_id == image_id).first()
        
        if comment_image:
            # Access relationship while session is open
            _ = comment_image.uploader
            # Expunge to detach from session but keep loaded relationship
            session.expunge(comment_image)
            from dashboard.tickets.models import _safe_expunge
            _safe_expunge(session, comment_image.uploader)
        
        # Log the returned image data
        image_dict = comment_image.to_dict()
        logger.info(
            f"Returning comment image: image_id={image_dict.get('image_id')}, file_path={image_dict.get('file_path')}",
            extra={'image_id': image_dict.get('image_id'), 'comment_id': comment_id, 'file_path': image_dict.get('file_path')}
        )
        
        return jsonify(image_dict), 201
        
    except ValueError as e:
        session.rollback()
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        error_details = traceback.format_exc()
        logger.error(
            f"ValueError uploading comment image for comment {comment_id}: {str(e)}",
            exc_info=True,
            extra={'comment_id': comment_id, 'user_id': current_user.user_id}
        )
        logger.debug(f"Full error traceback:\n{error_details}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        session.rollback()
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        error_details = traceback.format_exc()
        logger.error(
            f"Error uploading comment image for comment {comment_id}: {str(e)}",
            exc_info=True,
            extra={'comment_id': comment_id, 'user_id': current_user.user_id}
        )
        logger.debug(f"Full error traceback:\n{error_details}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@tickets_bp.route('/api/comments/<int:comment_id>/images', methods=['GET'])
@approved_required
def api_get_comment_images(comment_id):
    """Get all images for a comment."""
    from sqlalchemy.orm import joinedload
    session = get_session()
    try:
        comment = session.query(TicketComment).filter(TicketComment.comment_id == comment_id).first()
        if not comment:
            return jsonify({'error': 'Comment not found'}), 404
        
        images = session.query(CommentImage).options(
            joinedload(CommentImage.uploader)
        ).filter(
            CommentImage.comment_id == comment_id
        ).order_by(CommentImage.created_at.asc()).all()
        
        # Access relationships while session is open
        for img in images:
            _ = img.uploader
            session.expunge(img)
            from dashboard.tickets.models import _safe_expunge
            _safe_expunge(session, img.uploader)
        
        result = [img.to_dict() for img in images]
        return jsonify(result)
    finally:
        session.close()


@tickets_bp.route('/api/comments/<int:comment_id>/images/<int:image_id>', methods=['DELETE'])
@approved_required
def api_delete_comment_image(comment_id, image_id):
    """Delete an image from a comment."""
    session = get_session()
    current_user = get_current_user()
    
    try:
        comment_image = session.query(CommentImage).filter(
            CommentImage.image_id == image_id,
            CommentImage.comment_id == comment_id
        ).first()
        
        if not comment_image:
            return jsonify({'error': 'Image not found'}), 404
        
        # Check permissions
        comment = session.query(TicketComment).filter(TicketComment.comment_id == comment_id).first()
        if not comment:
            return jsonify({'error': 'Comment not found'}), 404
        
        if not (current_user.is_admin() or 
                comment.user_id == current_user.user_id or
                comment_image.uploaded_by == current_user.user_id):
            return jsonify({'error': 'Permission denied'}), 403
        
        # Delete files
        file_path = Path(config.TICKET_IMAGES_DIR) / comment_image.file_path
        if file_path.exists():
            file_path.unlink()
        
        if comment_image.thumbnail_path:
            thumb_path = Path(config.TICKET_IMAGES_DIR) / comment_image.thumbnail_path
            if thumb_path.exists():
                thumb_path.unlink()
        
        # Delete database record
        session.delete(comment_image)
        session.commit()
        
        return jsonify({'message': 'Image deleted successfully'}), 200
        
    except Exception as e:
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@tickets_bp.route('/api/images/<int:image_id>', methods=['GET'])
@approved_required
def api_serve_image(image_id):
    """Serve an image file."""
    session = get_session()
    try:
        # Try ticket image first
        ticket_image = session.query(TicketImage).filter(TicketImage.image_id == image_id).first()
        if ticket_image:
            file_path = Path(config.TICKET_IMAGES_DIR) / ticket_image.file_path
            if file_path.exists():
                return send_from_directory(
                    str(file_path.parent),
                    file_path.name,
                    mimetype=ticket_image.mime_type
                )
        
        # Try comment image
        comment_image = session.query(CommentImage).filter(CommentImage.image_id == image_id).first()
        if comment_image:
            file_path = Path(config.TICKET_IMAGES_DIR) / comment_image.file_path
            if file_path.exists():
                return send_from_directory(
                    str(file_path.parent),
                    file_path.name,
                    mimetype=comment_image.mime_type
                )
        
        return jsonify({'error': 'Image not found'}), 404
    finally:
        session.close()


@tickets_bp.route('/api/images/<int:image_id>/thumbnail', methods=['GET'])
@approved_required
def api_serve_thumbnail(image_id):
    """Serve a thumbnail image file."""
    session = get_session()
    try:
        # Try ticket image first
        ticket_image = session.query(TicketImage).filter(TicketImage.image_id == image_id).first()
        if ticket_image:
            # For large files, thumbnail_path might be same as file_path
            thumb_path_str = ticket_image.thumbnail_path or ticket_image.file_path
            thumb_path = Path(config.TICKET_IMAGES_DIR) / thumb_path_str
            if thumb_path.exists():
                return send_from_directory(
                    str(thumb_path.parent),
                    thumb_path.name,
                    mimetype='image/jpeg'
                )
        
        # Try comment image
        comment_image = session.query(CommentImage).filter(CommentImage.image_id == image_id).first()
        if comment_image:
            # For large files, thumbnail_path might be same as file_path
            thumb_path_str = comment_image.thumbnail_path or comment_image.file_path
            thumb_path = Path(config.TICKET_IMAGES_DIR) / thumb_path_str
            if thumb_path.exists():
                return send_from_directory(
                    str(thumb_path.parent),
                    thumb_path.name,
                    mimetype='image/jpeg'
                )
        
        return jsonify({'error': 'Thumbnail not found'}), 404
    finally:
        session.close()


@tickets_bp.route('/api/recurring/process', methods=['POST'])
@admin_required
def api_process_recurring_tasks():
    """Manually trigger recurring tasks processing (admin only)."""
    try:
        results = process_recurring_tasks()
        return jsonify({
            'message': 'Recurring tasks processed successfully',
            'results': results
        }), 200
    except Exception as e:
        logger.error(f"Error processing recurring tasks: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


def register_ticket_routes(app):
    """Register ticket routes with the Flask app."""
    # Initialize ticket database tables
    init_ticket_database()
    
    # Register blueprint
    app.register_blueprint(tickets_bp)
