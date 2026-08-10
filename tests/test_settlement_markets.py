from __future__ import annotations

import unittest

from betbot.settlement import _settle_from_score, _settle_from_totalcorner, _settle_total


class SettlementMarketTests(unittest.TestCase):
    def test_goal_lines_05_to_35(self) -> None:
        for line in (0.5, 1.5, 2.5, 3.5):
            winning_total = int(line + 0.5)
            self.assertEqual(_settle_total(winning_total, "over", line)[0], "WON")
            self.assertEqual(_settle_total(winning_total - 1, "over", line)[0], "LOST")
            self.assertEqual(_settle_total(winning_total - 1, "under", line)[0], "WON")

    def test_match_odds_home_away_and_draw(self) -> None:
        self.assertEqual(_settle_from_score(2, 1, "match_odds", "Casa FC", None, "Casa FC", "Fora FC")[0], "WON")
        self.assertEqual(_settle_from_score(1, 2, "match_odds", "Casa FC", None, "Casa FC", "Fora FC")[0], "LOST")
        self.assertEqual(_settle_from_score(1, 1, "match_odds", "draw", None, "Casa FC", "Fora FC")[0], "WON")

    def test_draw_no_bet_pushes_on_draw(self) -> None:
        self.assertEqual(_settle_from_score(1, 1, "draw_no_bet", "Casa FC", None, "Casa FC", "Fora FC")[0], "PUSH")

    def test_double_chance(self) -> None:
        self.assertEqual(_settle_from_score(1, 1, "double_chance", "Home or Draw", None)[0], "WON")
        self.assertEqual(_settle_from_score(0, 1, "double_chance", "Home or Draw", None)[0], "LOST")

    def test_first_half_never_uses_full_time_score(self) -> None:
        match = {"status": "FT", "hg": 2, "ag": 0, "h_hg": 0, "h_ag": 0, "h": "Casa", "a": "Fora"}
        settled = _settle_from_totalcorner(match, "first_half_goals", "over", 0.5)
        self.assertEqual(settled[0], "LOST")

    def test_first_half_without_period_score_stays_unsettled(self) -> None:
        match = {"status": "FT", "hg": 2, "ag": 0, "h": "Casa", "a": "Fora"}
        self.assertIsNone(_settle_from_totalcorner(match, "first_half_goals", "over", 0.5))


if __name__ == "__main__":
    unittest.main()
