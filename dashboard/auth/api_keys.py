#!/usr/bin/env python3
"""
API key utilities for third-party access.
"""

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Optional

from flask import g, request

from dashboard.auth.models import ApiKey, get_session, get_or_create_service_user
from dashboard.config import API_KEY_SECRET


def _hash_api_key(raw_key: str) -> str:
    """Hash an API key using HMAC-SHA256."""
    if not raw_key:
        return ""
    return hmac.new(API_KEY_SECRET.encode("utf-8"), raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def _get_api_key_from_request(req) -> Optional[str]:
    """Extract API key from headers (X-API-Key or Authorization: Bearer)."""
    if not req:
        return None
    
    api_key = req.headers.get("X-API-Key")
    if api_key:
        return api_key.strip()
    
    auth_header = req.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    
    return None


def create_api_key(name: Optional[str], created_by: Optional[int]) -> str:
    """
    Create an API key and persist its hash.
    Returns the raw key (only shown once).
    """
    raw_key = f"hk_{secrets.token_urlsafe(32)}"
    key_hash = _hash_api_key(raw_key)
    key_prefix = raw_key[:12]
    
    session = get_session()
    try:
        api_key = ApiKey(
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            created_by=created_by,
            created_at=datetime.utcnow(),
            is_active=True
        )
        session.add(api_key)
        session.commit()
        return raw_key
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def verify_api_key(raw_key: str) -> Optional[ApiKey]:
    """Verify an API key and return the ApiKey record if valid."""
    if not raw_key:
        return None
    
    key_hash = _hash_api_key(raw_key)
    session = get_session()
    try:
        api_key = session.query(ApiKey).filter(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active == True,
            ApiKey.revoked_at.is_(None)
        ).first()
        
        if not api_key:
            return None
        
        api_key.last_used_at = datetime.utcnow()
        session.commit()
        session.expunge(api_key)
        return api_key
    except Exception:
        session.rollback()
        return None
    finally:
        session.close()


def authenticate_request_api_key() -> Optional[ApiKey]:
    """
    Authenticate API key in the current request.
    Sets g.api_key and g.api_user if valid.
    """
    raw_key = _get_api_key_from_request(request)
    if not raw_key:
        return None
    
    api_key = verify_api_key(raw_key)
    if not api_key:
        return None
    
    g.api_key = api_key
    g.api_user = get_or_create_service_user()
    return api_key
