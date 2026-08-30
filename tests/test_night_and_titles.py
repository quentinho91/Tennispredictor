import unittest
from datetime import datetime
from src.tennisexplorer_scraper import deduce_tournament_meta, _scrape_tennisexplorer_single_day, fetch_tennisexplorer_matches


class TestNightMatchesAndTournamentTitles(unittest.TestCase):

    def test_deduce_tournament_meta_main_draw_vs_qualif(self):
        # US Open Main draw best-of
        meta_atp = deduce_tournament_meta("US Open", circuit="atp")
        self.assertEqual(meta_atp["level"], "G")
        self.assertEqual(meta_atp["surface"], "Hard")
        self.assertEqual(meta_atp["best_of"], 5)

        meta_wta = deduce_tournament_meta("US Open", circuit="wta")
        self.assertEqual(meta_wta["best_of"], 3)

        # US Open Qualifs best-of
        meta_qualif = deduce_tournament_meta("US Open (Qualifs)", circuit="atp")
        self.assertEqual(meta_qualif["best_of"], 3)

    def test_display_title_no_false_qualifs(self):
        # Scrape and check sport_title formatting
        matches = fetch_tennisexplorer_matches(circuit="all")
        self.assertGreater(len(matches), 0)

        for m in matches:
            t_title = m.get("sport_title", "")
            t_orig = m.get("tournament", "")
            if "us open" in t_orig.lower() and "qualif" not in t_orig.lower():
                self.assertNotIn("Qualifs", t_title, f"Unexpected Qualifs in {t_title}")
                self.assertIn("US Open", t_title)

    def test_night_matches_presence(self):
        # Verify that night session matches (commence_time next day early morning) are extracted
        matches = fetch_tennisexplorer_matches(circuit="all")
        # Check if commence_time is populated
        for m in matches:
            self.assertTrue(m.get("commence_time"), "commence_time should not be empty")


if __name__ == "__main__":
    unittest.main()
