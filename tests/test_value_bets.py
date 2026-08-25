"""
Unit tests for Value Bet selective filtering in src/app.py.
"""

import unittest
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from app import evaluate_market_value, compute_vb_confidence


class TestValueBetSelectiveRules(unittest.TestCase):

    def test_low_confidence_blocked(self):
        """Match with low confidence (< 58%) must be blocked even if model estimates high edge."""
        vb = evaluate_market_value(
            prob=0.60,
            odds=2.10,
            opp_odds=1.75,
            market_name="Vainqueur Match",
            selection="Player A",
            match_confidence={"score": 50.0}
        )
        self.assertIsNotNone(vb)
        self.assertFalse(vb["is_value_bet"])
        self.assertEqual(vb["confidence_status"], "BLOCKED_LOW_CONFIDENCE")
        self.assertEqual(vb["badge"], "BLOCKED")

    def test_moderate_confidence_requires_solid_edge(self):
        """Match with moderate confidence (58-72%) requires Edge >= 6% and EV >= 7%."""
        # Case A: Small edge (2.5%) -> Not a VB
        vb_weak = evaluate_market_value(
            prob=0.52,
            odds=2.00,
            opp_odds=1.90,
            market_name="Vainqueur Match",
            selection="Player A",
            match_confidence={"score": 65.0}
        )
        self.assertIsNotNone(vb_weak)
        self.assertFalse(vb_weak["is_value_bet"])

        # Case B: Strong edge (8%) -> Qualified VB with 50% damping
        vb_strong = evaluate_market_value(
            prob=0.62,
            odds=2.05,
            opp_odds=1.85,
            market_name="Vainqueur Match",
            selection="Player A",
            match_confidence={"score": 65.0}
        )
        self.assertIsNotNone(vb_strong)
        self.assertTrue(vb_strong["is_value_bet"])
        self.assertEqual(vb_strong["confidence_damping"], 0.50)
        self.assertEqual(vb_strong["badge"], "VALUE_BET")

    def test_high_confidence_qualifies(self):
        """Match with high confidence (>= 72%) qualifies with Edge >= 4.5% and EV >= 5% at full stake."""
        vb = evaluate_market_value(
            prob=0.60,
            odds=1.95,
            opp_odds=1.95,
            market_name="Vainqueur Match",
            selection="Player A",
            match_confidence={"score": 78.0}
        )
        self.assertIsNotNone(vb)
        self.assertTrue(vb["is_value_bet"])
        self.assertEqual(vb["confidence_damping"], 1.0)
        self.assertEqual(vb["confidence_status"], "FULL_HIGH_CONFIDENCE")

    def test_longshot_filter(self):
        """Longshots (> 3.80 odds) require high edge and EV to avoid lottery noise."""
        vb_noisy_longshot = evaluate_market_value(
            prob=0.25,
            odds=4.50,
            opp_odds=1.20,
            market_name="Vainqueur Match",
            selection="Underdog",
            match_confidence={"score": 60.0}
        )
        self.assertIsNotNone(vb_noisy_longshot)
        self.assertFalse(vb_noisy_longshot["is_value_bet"])
        self.assertEqual(vb_noisy_longshot["confidence_status"], "BLOCKED_LONGSHOT")

    def test_ultra_low_odds_filter(self):
        """Ultra-low odds (< 1.15) must be blocked due to asymmetric retirement/injury risk."""
        vb_heavy_fav = evaluate_market_value(
            prob=0.95,
            odds=1.10,
            opp_odds=8.0,
            market_name="Vainqueur Match",
            selection="Heavy Fav",
            match_confidence={"score": 85.0}
        )
        self.assertIsNotNone(vb_heavy_fav)
        self.assertFalse(vb_heavy_fav["is_value_bet"])
        self.assertEqual(vb_heavy_fav["confidence_status"], "BLOCKED_ULTRA_LOW_ODDS")


if __name__ == "__main__":
    unittest.main()
