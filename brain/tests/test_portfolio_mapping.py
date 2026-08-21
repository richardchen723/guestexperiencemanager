import unittest

from brain.services import normalize_tag_name, portfolio_name_for_listing, portfolio_name_for_tags, sanitize_listing_reference_text


class PortfolioMappingTests(unittest.TestCase):
    def test_pt300_maps_to_urban_stays(self):
        self.assertEqual(portfolio_name_for_tags(["pt300"]), "Urban Stays")

    def test_smoky_cabins_is_ignored(self):
        self.assertIsNone(portfolio_name_for_tags(["smoky cabins"]))
        self.assertIsNone(portfolio_name_for_listing(311151, ["smoky cabins"]))

    def test_known_cotton_candy_tags_map_to_canonical_portfolios(self):
        self.assertEqual(portfolio_name_for_tags([" enchanted   havens "]), "Enchanted Havens")
        self.assertEqual(portfolio_name_for_tags(["luminary resorts"]), "Luminary Resorts")
        self.assertEqual(portfolio_name_for_tags(["middlefork ridge"]), "Middlefork")
        self.assertEqual(portfolio_name_for_tags(["Crockett's Run"]), "Middlefork")
        self.assertEqual(portfolio_name_for_tags(["Crockett’s Run"]), "Middlefork")
        self.assertEqual(portfolio_name_for_tags(["crocketts run"]), "Middlefork")
        self.assertEqual(portfolio_name_for_tags(["crestwood"]), "crestwood")
        self.assertEqual(portfolio_name_for_tags(["LA St Gabe"]), "LA St Gabe")
        self.assertEqual(portfolio_name_for_tags(["st gabe units"]), "LA St Gabe")
        self.assertEqual(portfolio_name_for_tags(["san gabriel units"]), "LA St Gabe")

    def test_no_tag_crestwood_listings_map_by_listing_id(self):
        for listing_id in (558675, 558676, 558677, 558678):
            with self.subTest(listing_id=listing_id):
                self.assertEqual(portfolio_name_for_listing(listing_id, []), "crestwood")

    def test_crestwood_tag_maps_new_listing_without_explicit_listing_id(self):
        self.assertEqual(portfolio_name_for_listing(571917, ["crestwood"]), "crestwood")

    def test_tag_normalization_collapses_spaces_and_case(self):
        self.assertEqual(normalize_tag_name("  PT300  "), "pt300")
        self.assertEqual(normalize_tag_name("Urban   Stays"), "urban stays")

    def test_listing_id_references_are_replaced_with_internal_names(self):
        names = {295672: "PT300-2M"}
        self.assertEqual(
            sanitize_listing_reference_text("Booking pace needs review for Listing 295672", names),
            "Booking pace needs review for PT300-2M",
        )
        self.assertEqual(
            sanitize_listing_reference_text("Guest message for listing ID 295672 needs follow-up", names),
            "Guest message for PT300-2M needs follow-up",
        )


if __name__ == "__main__":
    unittest.main()
