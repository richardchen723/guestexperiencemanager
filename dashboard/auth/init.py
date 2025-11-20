#!/usr/bin/env python3
"""
Initialize owner account on first app run.
"""

import sys
import os

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.auth.models import init_user_database, get_user_by_email, create_user
from dashboard.tickets.models import init_ticket_database
import dashboard.config as config


def ensure_owner_exists():
    """Ensure the owner account exists, create if it doesn't."""
    # Initialize databases
    init_user_database()
    init_ticket_database()  # Initialize ticket tables
    
    # Check if owner exists
    owner = get_user_by_email(config.OWNER_EMAIL)
    
    if not owner:
        # Create owner account (name will be updated from Google when they log in)
        owner = create_user(
            email=config.OWNER_EMAIL,
            name=None,  # Will be set from Google account on first login
            role='owner',
            is_approved=True
        )
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Created owner account: {config.OWNER_EMAIL}")
    else:
        # Ensure owner has correct role and is approved
        if owner.role != 'owner':
            from dashboard.auth.models import update_user_role
            update_user_role(owner.user_id, 'owner')
        if not owner.is_approved:
            from dashboard.auth.models import approve_user
            approve_user(owner.user_id, owner.user_id)  # Self-approved
    
    return owner
