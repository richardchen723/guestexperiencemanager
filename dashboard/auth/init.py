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
from dashboard.ai.cache import init_cache_db
import dashboard.config as config


def initialize_all_databases():
    """
    Initialize all databases and create all necessary tables.
    This is idempotent - safe to call multiple times.
    """
    import os
    import logging
    logger = logging.getLogger(__name__)
    
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        from dashboard.auth.models import get_engine
        from dashboard.ai.cache import get_engine as get_cache_engine
        from database.models import get_engine as get_main_engine
        from database.schema import get_database_path
        import sqlalchemy
        
        # Create all schemas first before creating any tables
        # This prevents foreign key reference errors when tables reference each other
        users_engine = get_engine(config.USERS_DATABASE_PATH)
        cache_engine = get_cache_engine()
        main_db_path = get_database_path()
        main_engine = get_main_engine(main_db_path)
        
        # Create all schemas first (public schema already exists in PostgreSQL, but we ensure others exist)
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
                # Schema might already exist, ignore
                pass
        
        # Initialize main database (public schema) - contains sync_logs, listings, etc.
        try:
            from database.models import init_models
            logger.info("Initializing main database (public schema)...")
            init_models(main_db_path)
            logger.info("Main database initialized")
        except Exception as e:
            logger.warning(f"Error initializing main database: {e}")
            # Continue with other databases
    
    # Initialize all databases (now schemas exist)
    try:
        logger.info("Initializing user database...")
        init_user_database()
        logger.info("User database initialized")
    except Exception as e:
        logger.warning(f"Error initializing user database: {e}")
    
    try:
        logger.info("Initializing ticket database...")
        init_ticket_database()
        logger.info("Ticket database initialized")
    except Exception as e:
        logger.warning(f"Error initializing ticket database: {e}")
    
    try:
        logger.info("Initializing cache database...")
        init_cache_db()
        logger.info("Cache database initialized")
    except Exception as e:
        logger.warning(f"Error initializing cache database: {e}")


def ensure_owner_exists():
    """Ensure the owner account exists, create if it doesn't."""
    # Initialize all databases first (idempotent - safe to call multiple times)
    initialize_all_databases()
    
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
