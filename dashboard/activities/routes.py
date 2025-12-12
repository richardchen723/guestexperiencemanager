#!/usr/bin/env python3
"""
Activity query and reporting API routes.
"""

import sys
import os
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from typing import Optional, List, Dict, Any

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.auth.decorators import admin_required
from dashboard.auth.session import get_current_user
from dashboard.tickets.models import ActivityLog, Ticket, get_session
from dashboard.auth.models import get_all_users, get_user_by_id
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import joinedload

activities_bp = Blueprint('activities', __name__, url_prefix='/admin/api/activities')


@activities_bp.route('', methods=['GET'])
@admin_required
def api_query_activities():
    """Query activities with filters."""
    try:
        # Parse query parameters
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        user_id = request.args.get('user_id', type=int)
        activity_type = request.args.get('activity_type')
        action = request.args.get('action')
        entity_type = request.args.get('entity_type')
        entity_id = request.args.get('entity_id', type=int)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        
        session = get_session()
        try:
            query = session.query(ActivityLog).options(
                joinedload(ActivityLog.user)
            )
            
            # Apply filters
            if start_date_str:
                try:
                    start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                    query = query.filter(ActivityLog.created_at >= start_date)
                except ValueError:
                    pass
            
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                    query = query.filter(ActivityLog.created_at <= end_date)
                except ValueError:
                    pass
            
            if user_id:
                query = query.filter(ActivityLog.user_id == user_id)
            
            if activity_type:
                query = query.filter(ActivityLog.activity_type == activity_type)
            
            if action:
                query = query.filter(ActivityLog.action == action)
            
            if entity_type:
                query = query.filter(ActivityLog.entity_type == entity_type)
            
            if entity_id:
                query = query.filter(ActivityLog.entity_id == entity_id)
            
            # Order by created_at desc
            query = query.order_by(ActivityLog.created_at.desc())
            
            # Pagination
            total = query.count()
            activities = query.offset((page - 1) * per_page).limit(per_page).all()
            
            return jsonify({
                'activities': [activity.to_dict() for activity in activities],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            })
        finally:
            session.close()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error querying activities: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@activities_bp.route('/reports/ticket-metrics', methods=['GET'])
@admin_required
def api_ticket_metrics_report():
    """Get ticket metrics report."""
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        group_by = request.args.get('group_by', 'day')  # day, week, month
        
        # Parse dates
        if start_date_str:
            try:
                start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            except ValueError:
                start_date = datetime.now() - timedelta(days=30)
        else:
            start_date = datetime.now() - timedelta(days=30)
        
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except ValueError:
                end_date = datetime.now()
        else:
            end_date = datetime.now()
        
        session = get_session()
        try:
            # Query ticket creation activities
            created_query = session.query(ActivityLog).filter(
                and_(
                    ActivityLog.activity_type == 'ticket',
                    ActivityLog.action == 'create',
                    ActivityLog.created_at >= start_date,
                    ActivityLog.created_at <= end_date
                )
            )
            created_count = created_query.count()
            
            # Query ticket resolution activities (status_change to Resolved or Closed)
            resolved_query = session.query(ActivityLog).filter(
                and_(
                    ActivityLog.activity_type == 'ticket',
                    ActivityLog.action == 'status_change',
                    ActivityLog.created_at >= start_date,
                    ActivityLog.created_at <= end_date
                )
            )
            resolved_activities = resolved_query.all()
            
            # Filter for actual resolutions
            resolved_count = 0
            resolvers = {}  # user_id -> count
            for activity in resolved_activities:
                metadata = activity.activity_metadata or {}
                new_status = metadata.get('new_status', '')
                if new_status in ('Resolved', 'Closed'):
                    resolved_count += 1
                    user_id = activity.user_id
                    resolvers[user_id] = resolvers.get(user_id, 0) + 1
            
            # Get user names for resolvers
            users = get_all_users()
            user_map = {user.user_id: user for user in users}
            
            resolver_list = [
                {
                    'user_id': user_id,
                    'user_name': user_map.get(user_id).name if user_map.get(user_id) else None,
                    'user_email': user_map.get(user_id).email if user_map.get(user_id) else None,
                    'count': count
                }
                for user_id, count in sorted(resolvers.items(), key=lambda x: x[1], reverse=True)
            ]
            
            # Get tickets by status
            tickets_query = session.query(Ticket).filter(
                Ticket.created_at >= start_date,
                Ticket.created_at <= end_date
            )
            tickets = tickets_query.all()
            
            by_status = {}
            for ticket in tickets:
                status = ticket.status
                by_status[status] = by_status.get(status, 0) + 1
            
            return jsonify({
                'created_count': created_count,
                'resolved_count': resolved_count,
                'resolution_rate': (resolved_count / created_count * 100) if created_count > 0 else 0,
                'by_status': by_status,
                'resolvers': resolver_list
            })
        finally:
            session.close()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error generating ticket metrics report: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@activities_bp.route('/reports/user-performance', methods=['GET'])
@admin_required
def api_user_performance_report():
    """Get user performance report."""
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        user_id = request.args.get('user_id', type=int)
        
        # Parse dates
        if start_date_str:
            try:
                start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            except ValueError:
                start_date = datetime.now() - timedelta(days=30)
        else:
            start_date = datetime.now() - timedelta(days=30)
        
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except ValueError:
                end_date = datetime.now()
        else:
            end_date = datetime.now()
        
        session = get_session()
        try:
            # Get all users or specific user
            if user_id:
                users = [u for u in get_all_users() if u.user_id == user_id]
            else:
                users = get_all_users()
            
            performance_data = []
            
            for user in users:
                # Count tickets created
                created_count = session.query(ActivityLog).filter(
                    and_(
                        ActivityLog.user_id == user.user_id,
                        ActivityLog.activity_type == 'ticket',
                        ActivityLog.action == 'create',
                        ActivityLog.created_at >= start_date,
                        ActivityLog.created_at <= end_date
                    )
                ).count()
                
                # Count tickets resolved (status_change to Resolved/Closed by this user)
                resolved_activities = session.query(ActivityLog).filter(
                    and_(
                        ActivityLog.user_id == user.user_id,
                        ActivityLog.activity_type == 'ticket',
                        ActivityLog.action == 'status_change',
                        ActivityLog.created_at >= start_date,
                        ActivityLog.created_at <= end_date
                    )
                ).all()
                
                resolved_count = 0
                for activity in resolved_activities:
                    metadata = activity.activity_metadata or {}
                    if metadata.get('new_status') in ('Resolved', 'Closed'):
                        resolved_count += 1
                
                # Count tickets assigned to this user
                assigned_count = session.query(Ticket).filter(
                    and_(
                        Ticket.assigned_user_id == user.user_id,
                        Ticket.created_at >= start_date,
                        Ticket.created_at <= end_date
                    )
                ).count()
                
                # Calculate average resolution time (simplified - time from assignment to resolution)
                # This is a simplified calculation
                avg_resolution_time = None  # Could be enhanced with more complex query
                
                performance_data.append({
                    'user_id': user.user_id,
                    'user_name': user.name,
                    'user_email': user.email,
                    'tickets_created': created_count,
                    'tickets_resolved': resolved_count,
                    'tickets_assigned': assigned_count,
                    'avg_resolution_time': avg_resolution_time
                })
            
            # Sort by tickets_resolved desc
            performance_data.sort(key=lambda x: x['tickets_resolved'], reverse=True)
            
            return jsonify(performance_data)
        finally:
            session.close()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error generating user performance report: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@activities_bp.route('/reports/unresolved-assignments', methods=['GET'])
@admin_required
def api_unresolved_assignments_report():
    """Get current unresolved tickets grouped by assigned user."""
    try:
        session = get_session()
        try:
            # Get all unresolved tickets
            unresolved_statuses = ['Open', 'Assigned', 'In Progress', 'Blocked']
            unresolved_tickets = session.query(Ticket).filter(
                Ticket.status.in_(unresolved_statuses),
                Ticket.assigned_user_id.isnot(None)
            ).all()
            
            # Group by assigned_user_id
            assignments = {}
            for ticket in unresolved_tickets:
                user_id = ticket.assigned_user_id
                if user_id not in assignments:
                    assignments[user_id] = []
                assignments[user_id].append({
                    'ticket_id': ticket.ticket_id,
                    'title': ticket.title,
                    'status': ticket.status,
                    'priority': ticket.priority,
                    'due_date': ticket.due_date.isoformat() if ticket.due_date else None
                })
            
            # Get user names
            users = get_all_users()
            user_map = {user.user_id: user for user in users}
            
            # Build result list
            result = []
            for user_id, tickets in assignments.items():
                user = user_map.get(user_id)
                result.append({
                    'user_id': user_id,
                    'user_name': user.name if user else None,
                    'user_email': user.email if user else None,
                    'ticket_count': len(tickets),
                    'tickets': tickets
                })
            
            # Sort by ticket_count desc
            result.sort(key=lambda x: x['ticket_count'], reverse=True)
            
            return jsonify(result)
        finally:
            session.close()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error generating unresolved assignments report: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@activities_bp.route('/reports/trends', methods=['GET'])
@admin_required
def api_trends_report():
    """Get trend analysis data for charts."""
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        metric = request.args.get('metric', 'created')  # created, resolved, assigned
        
        # Parse dates
        if start_date_str:
            try:
                start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            except ValueError:
                start_date = datetime.now() - timedelta(days=30)
        else:
            start_date = datetime.now() - timedelta(days=30)
        
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except ValueError:
                end_date = datetime.now()
        else:
            end_date = datetime.now()
        
        session = get_session()
        try:
            if metric == 'created':
                # Tickets created over time
                activities = session.query(ActivityLog).filter(
                    and_(
                        ActivityLog.activity_type == 'ticket',
                        ActivityLog.action == 'create',
                        ActivityLog.created_at >= start_date,
                        ActivityLog.created_at <= end_date
                    )
                ).order_by(ActivityLog.created_at).all()
                
                # Group by date
                by_date = {}
                for activity in activities:
                    date_key = activity.created_at.date().isoformat()
                    by_date[date_key] = by_date.get(date_key, 0) + 1
                
                return jsonify({
                    'metric': 'created',
                    'data': [{'date': date, 'count': count} for date, count in sorted(by_date.items())]
                })
            
            elif metric == 'resolved':
                # Tickets resolved over time
                activities = session.query(ActivityLog).filter(
                    and_(
                        ActivityLog.activity_type == 'ticket',
                        ActivityLog.action == 'status_change',
                        ActivityLog.created_at >= start_date,
                        ActivityLog.created_at <= end_date
                    )
                ).order_by(ActivityLog.created_at).all()
                
                # Filter for resolutions
                resolved_by_date = {}
                for activity in activities:
                    metadata = activity.activity_metadata or {}
                    if metadata.get('new_status') in ('Resolved', 'Closed'):
                        date_key = activity.created_at.date().isoformat()
                        resolved_by_date[date_key] = resolved_by_date.get(date_key, 0) + 1
                
                return jsonify({
                    'metric': 'resolved',
                    'data': [{'date': date, 'count': count} for date, count in sorted(resolved_by_date.items())]
                })
            
            elif metric == 'assigned':
                # Tickets assigned over time
                activities = session.query(ActivityLog).filter(
                    and_(
                        ActivityLog.activity_type == 'ticket',
                        ActivityLog.action == 'assign',
                        ActivityLog.created_at >= start_date,
                        ActivityLog.created_at <= end_date
                    )
                ).order_by(ActivityLog.created_at).all()
                
                # Group by date
                by_date = {}
                for activity in activities:
                    date_key = activity.created_at.date().isoformat()
                    by_date[date_key] = by_date.get(date_key, 0) + 1
                
                return jsonify({
                    'metric': 'assigned',
                    'data': [{'date': date, 'count': count} for date, count in sorted(by_date.items())]
                })
            
            else:
                return jsonify({'error': 'Invalid metric'}), 400
        finally:
            session.close()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error generating trends report: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


def register_activities_routes(app):
    """Register activities blueprint with the Flask app."""
    app.register_blueprint(activities_bp)

