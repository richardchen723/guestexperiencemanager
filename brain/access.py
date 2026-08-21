#!/usr/bin/env python3
"""Access helpers for STR Signal Brain."""

from __future__ import annotations

from typing import Iterable


def is_brain_admin(user) -> bool:
    """Return True when a user can administer Brain setup and run controls.

    Dashboard roles do not partition Brain. Once Flask route auth has verified
    the user is approved, every Brain user shares the same operating view and
    controls.
    """
    return bool(user)


def accessible_portfolio_ids(session, user) -> list[int] | None:
    """Return allowed portfolio ids; None means the shared Brain team view."""
    if not user:
        return []
    return None


def filter_portfolio_query(query, model, portfolio_ids: Iterable[int] | None):
    """Apply portfolio filtering unless the caller has unrestricted access."""
    if portfolio_ids is None:
        return query
    ids = list(portfolio_ids)
    if not ids:
        return query.filter(False)
    return query.filter(model.portfolio_id.in_(ids))
