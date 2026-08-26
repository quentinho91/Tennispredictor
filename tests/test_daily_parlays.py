import unittest
from src.odds_scanner import generate_daily_parlays

class TestDailyParlays(unittest.TestCase):
    def setUp(self):
        self.sample_matches = [
            {'id': 'm1', 'p1': 'Alcaraz', 'p2': 'Coric', 'odds1': 1.25, 'odds2': 4.10, 'prediction': {'proba_p1': 0.85, 'proba_p2': 0.15, 'match_confidence': 88.0}},
            {'id': 'm2', 'p1': 'Sinner', 'p2': 'Popyrin', 'odds1': 1.30, 'odds2': 3.70, 'prediction': {'proba_p1': 0.82, 'proba_p2': 0.18, 'match_confidence': 85.0}},
            {'id': 'm3', 'p1': 'Swiatek', 'p2': 'Bronzetti', 'odds1': 1.15, 'odds2': 5.80, 'prediction': {'proba_p1': 0.90, 'proba_p2': 0.10, 'match_confidence': 92.0}}
        ]

    def test_empty(self):
        res = generate_daily_parlays([])
        self.assertFalse(res['has_parlays'])

    def test_parlays(self):
        res = generate_daily_parlays(self.sample_matches)
        self.assertTrue(res['has_parlays'])
        self.assertIsNotNone(res['max_odds'])
        self.assertGreaterEqual(len(res['max_odds']['selections']), 2)

if __name__ == '__main__':
    unittest.main()
