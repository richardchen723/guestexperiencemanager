import unittest
from datetime import datetime

from database.models import Listing
from sync.api_client import HostawayAPIClient
from sync.sync_listings import listing_tag_names, mark_missing_listings_deleted


class ListingTagTests(unittest.TestCase):
    def test_portfolio_tags_with_straight_and_curly_apostrophes_are_preserved(self):
        self.assertEqual(
            listing_tag_names({'listingTags': [{'name': "Crockett's Run"}]}),
            ["crockett's run"],
        )
        self.assertEqual(
            listing_tag_names({'listingTags': [{'name': 'Crockett’s Run'}]}),
            ['crockett’s run'],
        )

    def test_new_hostaway_portfolio_tags_are_normalized(self):
        self.assertEqual(
            listing_tag_names({'listingTags': [
                {'name': ' San Gabriel Units '},
                {'name': 'Crestwood'},
            ]}),
            ['crestwood', 'san gabriel units'],
        )


class ListingRemovalTests(unittest.TestCase):
    def test_missing_hostaway_listings_are_retained_and_marked_deleted(self):
        synced_at = datetime(2026, 8, 7, 12, 0, 0)
        active = Listing(listing_id=1, status=None)
        removed = Listing(listing_id=2, status=None)
        already_removed = Listing(listing_id=3, status='deleted')

        changed = mark_missing_listings_deleted(
            [active, removed, already_removed],
            {1},
            synced_at=synced_at,
        )

        self.assertEqual(changed, 1)
        self.assertIsNone(active.status)
        self.assertEqual(removed.status, 'deleted')
        self.assertEqual(removed.last_synced_at, synced_at)
        self.assertEqual(already_removed.status, 'deleted')

    def test_empty_response_never_deactivates_inventory(self):
        listing = Listing(listing_id=1, status=None)

        changed = mark_missing_listings_deleted([listing], set())

        self.assertEqual(changed, 0)
        self.assertIsNone(listing.status)


class ListingApiPageTests(unittest.TestCase):
    def test_page_fetch_distinguishes_api_failure_from_empty_last_page(self):
        client = HostawayAPIClient.__new__(HostawayAPIClient)
        client._make_request = lambda *_args, **_kwargs: None
        self.assertIsNone(client.get_listings_page(limit=100, offset=100))

        client._make_request = lambda *_args, **_kwargs: {'result': []}
        self.assertEqual(client.get_listings_page(limit=100, offset=100), [])


if __name__ == '__main__':
    unittest.main()
