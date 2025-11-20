#!/usr/bin/env python3
"""
Admin user management routes.
"""

import sys
import os
from flask import Blueprint, render_template, jsonify, request

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.auth.decorators import admin_required
from dashboard.auth.models import (
    get_all_users, get_user_by_id, approve_user, revoke_user,
    update_user_role, delete_user
)
from dashboard.auth.session import get_current_user

# Create blueprint
admin_bp = Blueprint('admin', __name__, url_prefix='/admin', template_folder='../templates')


@admin_bp.route('/users')
@admin_required
def users_page():
    """User management page."""
    return render_template('admin/users.html')


@admin_bp.route('/api/users')
@admin_required
def api_list_users():
    """Get all users (admin only)."""
    try:
        users = get_all_users()
        result = []
        for user in users:
            result.append({
                'user_id': user.user_id,
                'email': user.email,
                'name': user.name,
                'picture_url': user.picture_url,
                'role': user.role,
                'is_approved': user.is_approved,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'approved_at': user.approved_at.isoformat() if user.approved_at else None,
                'last_login': user.last_login.isoformat() if user.last_login else None,
                'is_owner': user.role == 'owner'
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>/approve', methods=['POST'])
@admin_required
def api_approve_user(user_id):
    """Approve a user account."""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Not authenticated'}), 401
        
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        approve_user(user_id, current_user.user_id)
        return jsonify({'success': True, 'message': 'User approved'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>/revoke', methods=['POST'])
@admin_required
def api_revoke_user(user_id):
    """Revoke user access (unapprove)."""
    try:
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        if user.role == 'owner':
            return jsonify({'error': 'Cannot revoke owner account'}), 403
        
        revoke_user(user_id)
        return jsonify({'success': True, 'message': 'User access revoked'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>/role', methods=['POST'])
@admin_required
def api_update_user_role(user_id):
    """Update user role."""
    try:
        data = request.get_json()
        new_role = data.get('role')
        
        if new_role not in ('owner', 'admin', 'user'):
            return jsonify({'error': 'Invalid role'}), 400
        
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Prevent changing owner role
        if user.role == 'owner' and new_role != 'owner':
            return jsonify({'error': 'Cannot change owner role'}), 403
        
        # Prevent creating new owners
        if new_role == 'owner' and user.role != 'owner':
            return jsonify({'error': 'Cannot assign owner role'}), 403
        
        update_user_role(user_id, new_role)
        return jsonify({'success': True, 'message': 'User role updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@admin_required
def api_delete_user(user_id):
    """Delete a user (cannot delete owner)."""
    try:
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        if user.role == 'owner':
            return jsonify({'error': 'Cannot delete owner account'}), 403
        
        success = delete_user(user_id)
        if success:
            return jsonify({'success': True, 'message': 'User deleted'})
        else:
            return jsonify({'error': 'Failed to delete user'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def register_admin_routes(app):
    """Register admin routes with the Flask app."""
    app.register_blueprint(admin_bp)
