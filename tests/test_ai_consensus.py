from types import SimpleNamespace
import unittest

from betbot.main import _ai_confirms_signal, _alert_has_ai_consensus, _bfbm_signal_has_value, _strict_live_bfbm_export_alerts


class AiConsensusTests(unittest.TestCase):

    def test_live_feed_never_falls_back_to_stored_tip(self):
        stored = {"analysis_json": '{"final_decision":{"ai_checked":true}}'}
        self.assertEqual(_strict_live_bfbm_export_alerts([], limit=10), [])
        self.assertNotIn(stored, _strict_live_bfbm_export_alerts([], limit=10))

    def test_rejects_when_ai_vetoes(self):
        idea = SimpleNamespace(
            should_check_odds=False,
            market_family="goals",
            selection="over",
            line=0.5,
        )
        signal = SimpleNamespace(market_family="goals", selection="over", line=0.5)
        self.assertFalse(_ai_confirms_signal(idea, signal))

    def test_accepts_exact_goal_market(self):
        idea = SimpleNamespace(
            should_check_odds=True,
            market_family="first_half_goals",
            selection="over",
            line=0.5,
        )
        signal = SimpleNamespace(market_family="first_half_goals", selection="over", line=0.5)
        self.assertTrue(_ai_confirms_signal(idea, signal))

    def test_rejects_different_line(self):
        idea = SimpleNamespace(
            should_check_odds=True,
            market_family="goals",
            selection="over",
            line=1.5,
        )
        signal = SimpleNamespace(market_family="goals", selection="over", line=0.5)
        self.assertFalse(_ai_confirms_signal(idea, signal))

    def test_accepts_btts_without_line(self):
        idea = SimpleNamespace(
            should_check_odds=True,
            market_family="btts",
            selection="yes",
            line=None,
        )
        signal = SimpleNamespace(market_family="btts", selection="yes", line=None)
        self.assertTrue(_ai_confirms_signal(idea, signal))

    def test_value_requires_real_positive_edge(self):
        signal = SimpleNamespace(probability=0.60)
        self.assertTrue(_bfbm_signal_has_value(signal, 2.00)[0])
        self.assertFalse(_bfbm_signal_has_value(signal, 1.50)[0])
        self.assertFalse(_bfbm_signal_has_value(signal, None)[0])

    def test_export_requires_explicit_ai_consensus(self):
        approved = {"analysis_json": '{"final_decision":{"ai_checked":true}}'}
        rejected = {"analysis_json": '{"final_decision":{"ai_checked":false}}'}
        legacy = {"analysis_json": "{}"}
        self.assertTrue(_alert_has_ai_consensus(approved))
        self.assertFalse(_alert_has_ai_consensus(rejected))
        self.assertFalse(_alert_has_ai_consensus(legacy))


if __name__ == "__main__":
    unittest.main()
