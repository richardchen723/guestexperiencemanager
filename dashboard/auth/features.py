"""Feature catalog and role-independent access-control helpers."""

from collections import OrderedDict

from flask import g, has_request_context

from dashboard.auth.models import get_user_feature_overrides


FEATURES = OrderedDict((feature['key'], feature) for feature in (
    {
        'key': 'dashboard',
        'label': 'Dashboard',
        'description': 'Portfolio overview, occupancy, and operational metrics.',
        'default_access': True,
        'endpoint': 'dashboard.dashboard_page',
    },
    {
        'key': 'properties',
        'label': 'Properties',
        'description': 'Listings, insights, tags, and portfolio mapping.',
        'default_access': True,
        'endpoint': 'properties',
    },
    {
        'key': 'tickets',
        'label': 'Tickets',
        'description': 'Create and manage operational tickets.',
        'default_access': True,
        'endpoint': 'tickets.tickets_list',
    },
    {
        'key': 'reviews',
        'label': 'Reviews',
        'description': 'Review windows, guest risk, and review resolutions.',
        'default_access': True,
        'endpoint': 'reviews.reviews_page',
    },
    {
        'key': 'knowledge',
        'label': 'Knowledge',
        'description': 'Search and maintain the operating knowledge base.',
        'default_access': True,
        'endpoint': 'knowledge.knowledge_page',
    },
    {
        'key': 'bookkeeping',
        'label': 'Bookkeeping',
        'description': 'Financial workspaces, uploads, approvals, and exports.',
        'default_access': False,
        'endpoint': 'bookkeeping.bookkeeping_page',
    },
    {
        'key': 'boost',
        'label': 'Boost',
        'description': 'Ranking campaigns, sessions, and proxy operations.',
        'default_access': False,
        'endpoint': 'boost.boost_page',
    },
    {
        'key': 'activities',
        'label': 'Activities',
        'description': 'Activity history, performance, and reporting.',
        'default_access': False,
        'endpoint': 'admin.activities_page',
    },
    {
        'key': 'sync',
        'label': 'Sync',
        'description': 'Hostaway synchronization history and job details.',
        'default_access': True,
        'endpoint': 'sync.sync_history_page',
    },
))


def feature_catalog():
    """Return the public feature metadata used by the admin UI."""
    return [
        {
            'key': feature['key'],
            'label': feature['label'],
            'description': feature['description'],
            'default_access': feature['default_access'],
        }
        for feature in FEATURES.values()
    ]


def feature_label(feature_key: str) -> str:
    feature = FEATURES.get(feature_key)
    return feature['label'] if feature else str(feature_key or 'Feature').replace('_', ' ').title()


def normalize_feature_access(enabled_feature_keys):
    """Convert a list of enabled keys into an explicit full access map."""
    enabled = {
        str(feature_key).strip()
        for feature_key in (enabled_feature_keys or [])
        if str(feature_key).strip()
    }
    unknown = sorted(enabled - set(FEATURES))
    if unknown:
        raise ValueError(f"Unknown feature permission: {', '.join(unknown)}")
    return {feature_key: feature_key in enabled for feature_key in FEATURES}


def assigned_feature_access(user, overrides=None):
    """Return configured access, including grants staged before approval."""
    if not user:
        return {feature_key: False for feature_key in FEATURES}
    if getattr(user, 'role', None) in ('owner', 'admin'):
        return {feature_key: True for feature_key in FEATURES}

    if overrides is None:
        cache_key = f'_feature_access_{user.user_id}'
        if has_request_context() and hasattr(g, cache_key):
            return getattr(g, cache_key)
        overrides = get_user_feature_overrides([user.user_id]).get(user.user_id, {})
    access = {
        feature_key: bool(overrides.get(feature_key, feature['default_access']))
        for feature_key, feature in FEATURES.items()
    }
    if has_request_context():
        setattr(g, f'_feature_access_{user.user_id}', access)
    return access


def effective_feature_access(user, overrides=None):
    """Return effective access after account approval and role are considered."""
    if not user or not getattr(user, 'is_approved', False):
        return {feature_key: False for feature_key in FEATURES}
    return assigned_feature_access(user, overrides=overrides)


def user_can_access_feature(user, feature_key: str) -> bool:
    if feature_key not in FEATURES:
        return False
    return effective_feature_access(user).get(feature_key, False)


def accessible_feature_keys(user):
    access = effective_feature_access(user)
    return [feature_key for feature_key in FEATURES if access.get(feature_key)]


def first_accessible_endpoint(user) -> str:
    access = effective_feature_access(user)
    for feature_key, feature in FEATURES.items():
        if access.get(feature_key):
            return feature['endpoint']
    return 'auth.profile_page'
