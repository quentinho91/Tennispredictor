import unittest
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))

from telegram_notifier import format_daily_telegram_briefing, send_telegram_message


class TestTelegramNotifier(unittest.TestCase):

    def test_format_briefing_with_value_bets(self):
        sample_data = {
            "matches": [
                {
                    "p1": "Alexander Blockx",
                    "p2": "Flavio Cobolli",
                    "tournament": "US Open",
                    "surface": "Hard",
                    "time_display": "18:30",
                    "has_value_bet": True,
                    "top_value_bet": {
                        "selection": "Flavio Cobolli",
                        "market": "Vainqueur Match",
                        "offered_odds": 1.89,
                        "fair_odds": 1.72,
                        "prob": 58.0,
                        "edge_pct": 7.9,
                        "ev_pct": 9.9,
                        "confidence_status": "FULL_HIGH_CONFIDENCE",
                        "kelly_pct": 1.3
                    }
                }
            ],
            "daily_parlays": {
                "has_parlays": True,
                "max_odds": {
                    "total_odds": 5.9,
                    "combined_prob_pct": 30.9,
                    "confidence_score": 90.2,
                    "confidence_label": "Très haute",
                    "selections": [
                        {"match_display": "Blockx vs Cobolli", "tournament": "US Open", "selection": "Cobolli", "odds": 1.89, "prob_pct": 58.0}
                    ]
                }
            }
        }

        msg = format_daily_telegram_briefing(sample_data)
        self.assertIn("TENNIS PREDICTOR AI", msg)
        self.assertIn("Flavio Cobolli", msg)
        self.assertIn("1.89", msg)
        self.assertIn("+7.9%", msg)
        self.assertIn("VALUE BETS DÉTECTÉS (1)", msg)

    def test_format_briefing_no_value_bets(self):
        sample_data = {
            "matches": [
                {
                    "p1": "Player A",
                    "p2": "Player B",
                    "has_value_bet": False
                }
            ],
            "daily_parlays": {}
        }

        msg = format_daily_telegram_briefing(sample_data)
        self.assertIn("AUCUN VALUE BET", msg)
        self.assertIn("protège votre capital", msg)

    def test_send_telegram_missing_tokens(self):
        res = send_telegram_message("", "", "test")
        self.assertFalse(res["success"])
        self.assertIn("manquant", res["error"])


if __name__ == "__main__":
    unittest.main()
