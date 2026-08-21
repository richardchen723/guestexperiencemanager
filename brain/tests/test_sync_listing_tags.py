import os
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from sync.sync_listings import enrich_listing_data, listing_field_value, listing_tag_names


class SyncListingTagTests(unittest.TestCase):
    def test_listing_tag_names_reads_hostaway_listing_tags(self):
        self.assertEqual(
            listing_tag_names({"listingTags": [{"id": 46080, "name": "Enchanted Havens"}]}),
            ["enchanted havens"],
        )

    def test_listing_tag_names_ignores_invalid_tags(self):
        self.assertEqual(
            listing_tag_names({"listingTags": [{"name": ""}, {"name": "PT300"}, {"name": "bad/tag"}]}),
            ["pt300"],
        )

    def test_listing_detail_aliases_supply_property_profile(self):
        payload = {
            "personCapacity": 6,
            "bedroomsNumber": 3,
            "bathroomsNumber": 2,
            "bedsNumber": 4,
            "price": 250,
        }
        self.assertEqual(listing_field_value(payload, "personCapacity", "accommodates"), 6)
        self.assertEqual(listing_field_value(payload, "bedroomsNumber", "bedrooms"), 3)
        self.assertEqual(listing_field_value(payload, "price", "basePrice"), 250)

    def test_listing_summary_is_enriched_from_detail_when_profile_is_missing(self):
        class Client:
            @staticmethod
            def get_listing(listing_id):
                return {"id": listing_id, "personCapacity": 6, "bedroomsNumber": 3}

        enriched = enrich_listing_data(Client(), {"id": 146889, "name": "Blue Haven"})

        self.assertEqual(enriched["name"], "Blue Haven")
        self.assertEqual(enriched["personCapacity"], 6)
        self.assertEqual(enriched["bedroomsNumber"], 3)


if __name__ == "__main__":
    unittest.main()
