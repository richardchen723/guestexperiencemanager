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
    # For PostgreSQL, create all schemas first before creating any tables
    # This prevents foreign key reference errors when tables reference each other
    import os
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        from dashboard.auth.models import get_engine
        from dashboard.ai.cache import get_engine as get_cache_engine
        import sqlalchemy
        
        # Create all schemas first (tickets uses same DB as users, just different schema)
        # Use the same engine for users and tickets since they share the same database
        users_engine = get_engine(config.USERS_DATABASE_PATH)
        cache_engine = get_cache_engine()
        
        schemas_to_create = [
            (users_engine, 'users'),
            (users_engine, 'tickets'),  # Same DB, different schema
            (cache_engine, 'cache'),
        ]
        
        for engine, schema_name in schemas_to_create:
            try:
                with engine.begin() as conn:
                    conn.execute(sqlalchemy.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
            except Exception as e:
                # Schema might already exist, or might be SQLite (ignore)
                pass
    
    # Initialize databases (now schemas exist)
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
