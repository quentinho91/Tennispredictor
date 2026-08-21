"""
Unit tests for src/markov_tennis.py using standard unittest.
"""

import unittest
import math
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from markov_tennis import (
    p_game,
    p_tiebreak,
    p_set,
    p_set_exact,
    p_match,
    estimate_point_probabilities,
    price_game_handicap,
    price_total_games
)


class TestMarkovTennis(unittest.TestCase):

    def test_p_game_symmetry_and_values(self):
        # Symmetric case
        self.assertAlmostEqual(p_game(0.5), 0.5, places=5)

        # Boundary cases
        self.assertEqual(p_game(0.0), 0.0)
        self.assertEqual(p_game(1.0), 1.0)

        # Realistic tennis values: at p=0.64, hold probability is ~81-82%
        g_64 = p_game(0.64)
        self.assertTrue(0.80 < g_64 < 0.84, f"g_64 was {g_64}")

        # Monotonicity
        self.assertTrue(p_game(0.70) > p_game(0.60) > p_game(0.50))

    def test_p_tiebreak_symmetry(self):
        # Symmetric servers
        p_tb_sym = p_tiebreak(0.62, 0.62, a_serves_first=True)
        self.assertAlmostEqual(p_tb_sym, 0.5, delta=0.03)

        # Server dominance
        p_tb_dom = p_tiebreak(0.70, 0.55, a_serves_first=True)
        self.assertTrue(p_tb_dom > 0.70)

    def test_p_set_and_distributions(self):
        # Symmetric match
        p_s, scores, exp_games, exp_diff = p_set(0.64, 0.64)
        self.assertAlmostEqual(p_s, 0.5, places=3)
        self.assertAlmostEqual(exp_diff, 0.0, places=2)
        self.assertTrue(9.0 < exp_games < 10.5, f"exp_games was {exp_games}")

        # Sum of scores must be 1.0
        total_score_prob = sum(scores.values())
        self.assertAlmostEqual(total_score_prob, 1.0, places=4)

    def test_p_match_best_of_3_and_5(self):
        res_b3 = p_match(0.65, 0.62, best_of=3)
        self.assertTrue(res_b3["proba_a"] > 0.5)
        self.assertAlmostEqual(res_b3["proba_a"] + res_b3["proba_b"], 1.0, places=4)

        # Set scores must sum to 1.0
        total_set_scores = sum(res_b3["set_scores"].values())
        self.assertAlmostEqual(total_set_scores, 1.0, places=4)

        # Best of 5 amplifies edge
        res_b5 = p_match(0.65, 0.62, best_of=5)
        self.assertTrue(res_b5["proba_a"] > res_b3["proba_a"])

    def test_estimate_point_probabilities(self):
        # Identical ratings
        pa, pb = estimate_point_probabilities(1500, 1500, 1500, 1500, surface="Hard")
        self.assertAlmostEqual(pa, pb, places=3)

        # Strong server vs weak returner
        pa_dom, pb_dom = estimate_point_probabilities(1700, 1400, 1450, 1550, surface="Hard")
        self.assertTrue(pa_dom > pb_dom)
        self.assertTrue(pa_dom > 0.65)

    def test_handicap_and_totals(self):
        # Handicap
        cov_a, cov_b = price_game_handicap(expected_diff=3.5, line=-2.5)
        self.assertTrue(cov_a > cov_b)
        self.assertAlmostEqual(cov_a + cov_b, 1.0, places=3)

        # Totals
        p_ov, p_un = price_total_games(expected_total=23.5, line=21.5)
        self.assertTrue(p_ov > p_un)
        self.assertAlmostEqual(p_ov + p_un, 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
