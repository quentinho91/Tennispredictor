"""
Unit tests for the recent model and pipeline enhancements:
- Direct import & computation of travel strain & decayed Elo
- Shin de-vigging vs Proportional de-vigging
- Bayesian consensus probability
- Fractional Kelly Criterion staking
"""

import unittest
import sys
from pathlib import Path
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

import importlib.util

_fe_path = BASE_DIR / "src" / "02_feature_engineering.py"
_spec_fe = importlib.util.spec_from_file_location("fe", _fe_path)
fe = importlib.util.module_from_spec(_spec_fe)
_spec_fe.loader.exec_module(fe)

get_decayed_elo = fe.get_decayed_elo
compute_travel_strain = fe.compute_travel_strain
COUNTRY_CONTINENT = fe.COUNTRY_CONTINENT
get_altitude = fe.get_altitude

_pm_path = BASE_DIR / "src" / "05_predict_match.py"
_spec_pm = importlib.util.spec_from_file_location("pm", _pm_path)
pm = importlib.util.module_from_spec(_spec_pm)
_spec_pm.loader.exec_module(pm)


compute_rust_factor = fe.compute_rust_factor
compute_slump_indicator = fe.compute_slump_indicator
compute_bo5_stats = fe.compute_bo5_stats


class TestPipelineEnhancements(unittest.TestCase):

    def test_rust_factor_detection(self):
        """Player inactive > 75 days or with < 3 matches after a long break is flagged as rusty."""
        # 1. Player with 90 days of inactivity: must be flagged as rusty (is_rusty=1.0, 0 matches)
        is_rusty, matches = compute_rust_factor(day=1000, last_play_day=910, matches_since_break=10)
        self.assertEqual(is_rusty, 1.0)
        self.assertEqual(matches, 0)

        # 2. Player returning who has only played 1 match since the break
        is_rusty, matches = compute_rust_factor(day=1005, last_play_day=1002, matches_since_break=1)
        self.assertEqual(is_rusty, 1.0)
        self.assertEqual(matches, 1)

        # 3. Player who has played 3 matches since the break and active
        is_rusty, matches = compute_rust_factor(day=1010, last_play_day=1008, matches_since_break=3)
        self.assertEqual(is_rusty, 0.0)

    def test_bo5_stats(self):
        """Grand Slam best-of-5 matches must compute smoothed winrate and log experience, neutral otherwise."""
        # In Best-of-3: neutral (0.50, 0.0)
        wr_bo3, exp_bo3 = compute_bo5_stats(bo5_matches_count=20, bo5_wins_count=18, match_best_of=3)
        self.assertEqual(wr_bo3, 0.50)
        self.assertEqual(exp_bo3, 0.0)

        # In Best-of-5: smoothed winrate: (18 + 1) / (20 + 2) = 19/22 ≈ 0.8636
        wr_bo5, exp_bo5 = compute_bo5_stats(bo5_matches_count=20, bo5_wins_count=18, match_best_of=5)
        self.assertAlmostEqual(wr_bo5, 19.0 / 22.0, places=4)
        self.assertGreater(exp_bo5, 0.0)

    def test_slump_indicator(self):
        """Player with consecutive losses or frequent losses against lower-ranked players must be flagged in slump."""
        # Active streak of -4, 4 losses in last 5 matches, including 2 against lower-ranked players
        # format of recent_results: (day, win, opp_better_ranked, ...)
        recent_res = [
            (100, False, False),  # bad loss
            (105, False, True),   # loss to higher ranked
            (110, True, False),   # win
            (115, False, False),  # bad loss
            (120, False, False),  # bad loss
        ]
        is_in_slump, severity = compute_slump_indicator(recent_res, current_streak=-3)
        self.assertEqual(is_in_slump, 1.0)
        self.assertGreater(severity, 1.0)

        # Player with winning streak of +3
        good_res = [(100 + i, True, False) for i in range(5)]
        is_in_slump_good, severity_good = compute_slump_indicator(good_res, current_streak=3)
        self.assertEqual(is_in_slump_good, 0.0)
        self.assertEqual(severity_good, 0.0)

    def test_travel_strain_cross_continent(self):
        """Player traveling cross-continent with short rest must have positive travel strain."""
        strain, short_sc = compute_travel_strain(
            player="Djokovic",
            day=1003,
            t1_id="2024-560",
            t_country="USA",
            surf="Hard",
            last_tourney_id_dict={"Djokovic": "2024-520"},
            last_play_date_dict={"Djokovic": 1000},  # 3 days ago -> <= 4
            last_tourney_country_dict={"Djokovic": "FRA"},  # EU to NA -> continent change
            last_surface_dict={"Djokovic": "Clay"}  # Clay to Hard -> surface change
        )
        self.assertGreater(strain, 0.0)
        self.assertEqual(short_sc, 1.0)

    def test_get_decayed_elo_inactive(self):
        """Player inactive > 60 days must decay towards 1500."""
        active_elo = get_decayed_elo(2000.0, day=100, last_day=90)
        self.assertEqual(active_elo, 2000.0)

        inactive_elo = get_decayed_elo(2000.0, day=400, last_day=100)
        self.assertLess(inactive_elo, 2000.0)
        self.assertGreater(inactive_elo, 1500.0)

    def test_shin_devigging(self):
        """Shin method must properly remove overround and sum to 1.0."""
        odds1, odds2 = 1.40, 3.10
        p1_prop, p2_prop = pm.remove_overround_proportional(odds1, odds2)
        p1_shin, p2_shin = pm.remove_overround_shin(odds1, odds2)

        self.assertAlmostEqual(p1_prop + p2_prop, 1.0, places=5)
        self.assertAlmostEqual(p1_shin + p2_shin, 1.0, places=5)

        # Under Shin's method, underdogs get a larger adjustment than favorites
        # so favorite's true probability is slightly higher than proportional
        self.assertGreaterEqual(p1_shin, p1_prop)

    def test_bayesian_consensus(self):
        """Bayesian consensus must blend model probability with market probability."""
        p_model = 0.70
        p_market = 0.50
        p_cons = pm.compute_bayesian_consensus(p_model, p_market, market_weight=0.30)

        self.assertLess(p_cons, p_model)
        self.assertGreater(p_cons, p_market)

    def test_kelly_stake(self):
        """Kelly criterion must return 0 for negative EV and positive fraction for positive EV."""
        # Negative EV: prob=0.40, odds=2.00 -> EV = -0.20
        stake_neg = pm.compute_kelly_stake(0.40, 2.00, fraction=0.25)
        self.assertEqual(stake_neg, 0.0)

        # Positive EV: prob=0.60, odds=2.00 -> EV = +0.20 -> full Kelly = 0.20 -> quarter = 0.05
        stake_pos = pm.compute_kelly_stake(0.60, 2.00, fraction=0.25, max_cap=0.05)
        self.assertGreater(stake_pos, 0.0)
        self.assertLessEqual(stake_pos, 0.05)


if __name__ == "__main__":
    unittest.main()
