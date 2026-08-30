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

    def test_no_tomorrow_daytime_matches(self):
        # Verify that only today's matches and tonight's US session (< 09:00 AM tomorrow) are returned
        now = datetime.now()
        today_date_str = now.strftime("%Y-%m-%d")
        matches = fetch_tennisexplorer_matches(circuit="all")

        for m in matches:
            commence = m.get("commence_time")
            if commence and "T" in commence:
                date_part, time_part = commence.split("T")
                if date_part != today_date_str:
                    # If date is tomorrow, hour must be < 9 (night session)
                    h = int(time_part.split(":")[0])
                    self.assertLess(h, 9, f"Match {m['p1']} vs {m['p2']} at {commence} is from tomorrow daytime/evening and should not be included.")


if __name__ == "__main__":
    unittest.main()
