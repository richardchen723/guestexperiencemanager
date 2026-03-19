#!/usr/bin/env python3
"""
Proxy management - parsing, random selection with rotation, and health tracking.
"""

import logging
import random
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from dashboard.boost.models import BoostProxy, get_session

logger = logging.getLogger(__name__)


def parse_proxy_list(text: str) -> List[Dict]:
    """
    Parse a text block of proxies in various formats into structured dicts.
    
    Supported formats:
        host:port
        host:port:username:password
        protocol://host:port
        protocol://username:password@host:port
        username:password@host:port
    """
    proxies = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = _parse_single_proxy(line)
        if parsed:
            proxies.append(parsed)
    return proxies


def _parse_single_proxy(line: str) -> Optional[Dict]:
    """Parse a single proxy line into a structured dict."""
    protocol = "http"
    username = None
    password = None
    host = None
    port = None

    # URL-style: protocol://user:pass@host:port or protocol://host:port
    if "://" in line:
        try:
            parsed = urlparse(line)
            protocol = parsed.scheme or "http"
            host = parsed.hostname
            port = parsed.port
            username = parsed.username
            password = parsed.password
            if host and port:
                return {
                    "host": host, "port": port, "protocol": protocol,
                    "username": username, "password": password,
                }
        except Exception:
            pass

    # user:pass@host:port
    if "@" in line:
        cred_part, host_part = line.rsplit("@", 1)
        parts = cred_part.split(":", 1)
        if len(parts) == 2:
            username, password = parts
        host_parts = host_part.split(":")
        if len(host_parts) == 2:
            host = host_parts[0]
            try:
                port = int(host_parts[1])
            except ValueError:
                return None
            return {
                "host": host, "port": port, "protocol": protocol,
                "username": username, "password": password,
            }

    # host:port or host:port:user:pass
    parts = line.split(":")
    if len(parts) == 2:
        host = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            return None
        return {"host": host, "port": port, "protocol": protocol, "username": None, "password": None}
    elif len(parts) == 4:
        host = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            return None
        username = parts[2]
        password = parts[3]
        return {"host": host, "port": port, "protocol": protocol, "username": username, "password": password}

    return None


def import_proxies(text: str) -> Tuple[int, int]:
    """
    Parse proxy text and upsert into the database.
    Returns (added_count, skipped_count).
    """
    parsed = parse_proxy_list(text)
    session = get_session()
    added = 0
    skipped = 0
    try:
        for proxy_data in parsed:
            existing = (
                session.query(BoostProxy)
                .filter_by(host=proxy_data["host"], port=proxy_data["port"])
                .first()
            )
            if existing:
                existing.username = proxy_data.get("username")
                existing.password = proxy_data.get("password")
                existing.protocol = proxy_data.get("protocol", "http")
                existing.is_active = True
                existing.fail_count = 0
                skipped += 1
            else:
                proxy = BoostProxy(
                    host=proxy_data["host"],
                    port=proxy_data["port"],
                    username=proxy_data.get("username"),
                    password=proxy_data.get("password"),
                    protocol=proxy_data.get("protocol", "http"),
                )
                session.add(proxy)
                added += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return added, skipped


def get_random_proxy() -> Optional[BoostProxy]:
    """
    Pick a random active proxy, weighted away from recently-used ones.
    Proxies with more failures are less likely to be selected.
    """
    session = get_session()
    try:
        proxies = (
            session.query(BoostProxy)
            .filter_by(is_active=True)
            .all()
        )
        if not proxies:
            return None

        weights = []
        for p in proxies:
            weight = max(1.0, 10.0 - p.fail_count * 2)
            if p.last_used_at:
                minutes_since = (datetime.utcnow() - p.last_used_at).total_seconds() / 60
                weight *= min(3.0, 1.0 + minutes_since / 30)
            weights.append(weight)

        chosen = random.choices(proxies, weights=weights, k=1)[0]

        # Detach from session so caller can use it freely
        session.expunge(chosen)
        return chosen
    finally:
        session.close()


def mark_proxy_used(proxy_id: int):
    """Mark a proxy as recently used."""
    session = get_session()
    try:
        proxy = session.query(BoostProxy).get(proxy_id)
        if proxy:
            proxy.last_used_at = datetime.utcnow()
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def mark_proxy_failed(proxy_id: int):
    """Increment fail count; deactivate after 5 consecutive failures."""
    session = get_session()
    try:
        proxy = session.query(BoostProxy).get(proxy_id)
        if proxy:
            proxy.fail_count += 1
            if proxy.fail_count >= 5:
                proxy.is_active = False
                logger.warning(f"Proxy {proxy.host}:{proxy.port} deactivated after {proxy.fail_count} failures")
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_all_proxies() -> List[Dict]:
    """Get all proxies for the management UI."""
    session = get_session()
    try:
        proxies = session.query(BoostProxy).order_by(BoostProxy.id).all()
        return [p.to_dict() for p in proxies]
    finally:
        session.close()


def clear_all_proxies():
    """Remove all proxies from the database."""
    session = get_session()
    try:
        session.query(BoostProxy).delete()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def toggle_proxy(proxy_id: int, is_active: bool):
    """Enable or disable a specific proxy."""
    session = get_session()
    try:
        proxy = session.query(BoostProxy).get(proxy_id)
        if proxy:
            proxy.is_active = is_active
            if is_active:
                proxy.fail_count = 0
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def delete_proxies(proxy_ids: List[int]) -> int:
    """Delete one or more proxies by ID. Returns count deleted."""
    session = get_session()
    try:
        count = (
            session.query(BoostProxy)
            .filter(BoostProxy.id.in_(proxy_ids))
            .delete(synchronize_session="fetch")
        )
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
