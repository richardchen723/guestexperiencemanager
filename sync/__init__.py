"""Sync module for the Hostaway data system.

Public objects are loaded lazily so running a leaf module such as
``python -m sync.sync_reviews`` does not import the entire synchronizer first.
"""

__all__ = [
    'HostawayAPIClient',
    'sync',
    'full_sync',
    'incremental_sync'
]


def __getattr__(name):
    if name == 'HostawayAPIClient':
        from .api_client import HostawayAPIClient
        return HostawayAPIClient
    if name in {'sync', 'full_sync', 'incremental_sync'}:
        from .sync_manager import full_sync, incremental_sync, sync
        return {
            'sync': sync,
            'full_sync': full_sync,
            'incremental_sync': incremental_sync,
        }[name]
    raise AttributeError(name)
