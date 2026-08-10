from __future__ import annotations

import unittest

from betbot.deterministic import evaluate_game
from betbot.main import _live_match_query_score
from betbot.stats import compact_sofascore_statistics


def real_stats() -> dict[str, dict[str, float]]:
    return {
        "Casa": {"Total Shots": 9, "Shots on Goal": 3, "Corner Kicks": 3, "Dangerous Attacks": 31},
        "Fora": {"Total Shots": 7, "Shots on Goal": 2, "Corner Kicks": 2, "Dangerous Attacks": 27},
    }


class StrictLiveDataTests(unittest.TestCase):
    def evaluate(self, stats):
        return evaluate_game(
            minute=65, score_home=1, score_away=0, stats=stats,
            min_confidence=80,
            available_markets=[("goals", 1.5), ("corners", 5.5), ("btts", None)],
        )

    def test_missing_core_metric_blocks_all_markets(self) -> None:
        stats = real_stats()
        del stats["Fora"]["Total Shots"]
        signal = self.evaluate(stats)
        self.assertFalse(signal.approved)
        self.assertEqual(signal.strategy, "missing_real_data")

    def test_missing_dangerous_attacks_is_not_a_universal_block(self) -> None:
        stats = real_stats()
        del stats["Casa"]["Dangerous Attacks"]
        del stats["Fora"]["Dangerous Attacks"]
        self.assertNotEqual(self.evaluate(stats).strategy, "missing_real_data")

    def test_zero_is_kept_distinct_from_missing(self) -> None:
        stats = real_stats()
        stats["Casa"]["Dangerous Attacks"] = 0
        stats["Fora"]["Dangerous Attacks"] = 0
        self.assertNotEqual(self.evaluate(stats).strategy, "missing_real_data")

    def test_one_team_missing_blocks_the_game(self) -> None:
        signal = self.evaluate({"Casa": real_stats()["Casa"]})
        self.assertFalse(signal.approved)
        self.assertEqual(signal.strategy, "missing_real_data")

    def test_sofascore_does_not_invent_pressure_fields(self) -> None:
        match = {"homeTeam": {"name": "Casa"}, "awayTeam": {"name": "Fora"}}
        response = {"statistics": [{"groups": [{"statisticsItems": [
            {"name": "Total shots", "home": 8, "away": 6},
            {"name": "Shots on target", "home": 3, "away": 2},
            {"name": "Corner kicks", "home": 4, "away": 1},
        ]}]}]}
        stats = compact_sofascore_statistics(match, response)
        for values in stats.values():
            self.assertNotIn("Dangerous Attacks", values)
            self.assertNotIn("Attacks", values)

    def test_reserve_team_never_matches_first_team(self) -> None:
        match = {"homeTeam": {"name": "Austin FC"}, "awayTeam": {"name": "Sporting Kansas City"}}
        self.assertEqual(_live_match_query_score("Austin FC II", "Sporting KC II", match), 0.0)

    def test_reserve_team_matches_reserve_team(self) -> None:
        match = {"homeTeam": {"name": "Austin FC II"}, "awayTeam": {"name": "Sporting Kansas City II"}}
        self.assertGreaterEqual(_live_match_query_score("Aust FC II", "Sporting KC II", match), 0.78)


if __name__ == "__main__":
    unittest.main()
