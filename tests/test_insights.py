"""Offline tests for insights.py: the event study, the top three headlines and the email subject. No network.

    python tests/test_insights.py -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import insights  # noqa: E402
import report  # noqa: E402

FIRST = insights.FIRST_RANKED          # index of the first candle that can be ranked


def coin_from(flags, name="BTC", n=None):
    """A coin with a straight-line price (close = 100 + index), so every expected return can be worked out by hand."""
    n = n or FIRST + len(flags)
    return {"coin": name, "closes": [100.0 + i for i in range(n)], "base_rate": {"flags": flags}}


def summary(coin, close_gap, ranks, flags=None, volume=None):
    s = {"coin": coin, "dist_from_30d_avg_pct": close_gap, "volatility_30d_daily_pct": 3.1, "max_drawdown_30d_pct": -25.0, "ranks": ranks,
         "breaches": [m for m, r in ranks.items() if r >= agent.ALERT_RANK], "flags": flags or [], "as_of": "2026-11-20", "history_days": 720}
    if volume:
        s["context"] = {"volume_ratio_30d": volume, "volume_rank": 99.0}
    return {"coin": coin, "summary": s}


def real_20_nov():
    """The numbers of the 20 Nov 2026 close, as the agent summarised them."""
    btc = summary("BTC", -20.46, {"volatility": 72.0, "distance": 99.6, "drawdown": 97.9}, ["volume"], 2.95)
    btc["summary"]["max_drawdown_30d_pct"] = -30.01
    eth = summary("ETH", -23.91, {"volatility": 43.1, "distance": 94.6, "drawdown": 96.3}, ["volume"], 2.74)
    xrp = summary("XRP", -21.07, {"volatility": 45.1, "distance": 94.9, "drawdown": 96.4}, ["volume"], 2.45)
    return [btc, eth, xrp]


class EventStudy(unittest.TestCase):
    def test_episodes_lengths_and_persistence_are_counted_by_hand(self):
        flags = [False, True, True, False, False, True, False, False, False, False, False]
        result = insights.event_study([coin_from(flags)])
        self.assertEqual(sorted(result["lengths"]), [1, 2])
        self.assertEqual(result["episodes"], 2)
        self.assertEqual(result["run_median"], 1.5)
        self.assertEqual(result["run_max"], 2)
        self.assertEqual(result["current_run"], {"BTC": 0})
        self.assertAlmostEqual(result["again"][0], 100.0 / 3)       # alert days: 3; only the first is followed by another one
        self.assertFalse(result["enough"])

    def test_the_move_after_an_episode_starts_is_measured_from_the_first_day(self):
        flags = [False, True, True, False, False, True, False, False, False, False, False]
        result = insights.event_study([coin_from(flags)])
        start = FIRST + 1                                              # first day of the first episode
        expected = ((100.0 + start + 7) / (100.0 + start) - 1) * 100
        stats = result["after_episode"][7]
        self.assertEqual(stats["n"], 1)                                # the second episode is too close to the end for a 7-day move
        self.assertAlmostEqual(stats["median"], expected)
        self.assertEqual(result["any_day"][7]["n"], 4)                 # ranked days 119..122 have a close 7 days later

    def test_the_current_run_counts_the_alert_days_at_the_end(self):
        result = insights.event_study([coin_from([False, False, True, True, True])])
        self.assertEqual(result["current_run"], {"BTC": 3})

    def test_a_coin_with_an_error_is_skipped_and_no_data_gives_none(self):
        self.assertIsNone(insights.event_study([{"coin": "BTC", "error": "no candles"}]))
        result = insights.event_study([{"coin": "ETH", "error": "x"}, coin_from([True, False])])
        self.assertEqual(set(result["current_run"]), {"BTC"})

    def test_enough_needs_the_minimum_number_of_episodes(self):
        flags = [True, False] * insights.MIN_EPISODES
        self.assertTrue(insights.event_study([coin_from(flags)])["enough"])
        self.assertFalse(insights.event_study([coin_from(flags[:-2])])["enough"])

    def test_the_stats_use_the_inclusive_quartiles(self):
        stats = insights._stats([-10.0, 0.0, 5.0, 10.0, 20.0])
        self.assertEqual((stats["median"], stats["q1"], stats["q3"], stats["low"], stats["high"], stats["n"]), (5.0, 0.0, 10.0, -10.0, 20.0, 5))
        self.assertEqual(stats["up_pct"], 60.0)
        self.assertIsNone(insights._stats([]))
        self.assertEqual(insights._stats([3.0])["q1"], 3.0)

    def test_the_real_demo_series_gives_sane_numbers(self):
        coins = report.demo_coins()
        result = insights.event_study(coins)
        self.assertGreater(result["episodes"], 0)
        self.assertTrue(all(0.0 <= v <= 100.0 for v in result["again"] if v is not None))
        self.assertEqual(len(result["again"]), insights.PERSIST_DAYS)


class Headlines(unittest.TestCase):
    def test_the_20_nov_numbers_give_these_three_headlines(self):
        items = insights.top_headlines(real_20_nov(), None)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["text"], "Bitcoin closes 20.46% below its 30-day average, rank 99.6 against its own history")
        self.assertEqual(items[1]["text"], "BTC drawdown 97.9; ETH drawdown 96.3; XRP drawdown 96.4 (ranks)")
        self.assertIn("2.45 to 2.95 times", items[2]["text"])

    def test_there_are_always_exactly_three_headlines(self):
        for coins in (report.demo_coins(), real_20_nov(), [{"coin": "BTC", "error": "x"}], []):
            self.assertEqual(len(insights.top_headlines(coins, None)), 3)
        calm = [summary("BTC", 0.5, {"volatility": 10.0, "distance": 20.0, "drawdown": 30.0})]
        items = insights.top_headlines(calm, None)
        self.assertEqual(items[0]["kicker"], "Calm market")
        self.assertIn("rank 30.0", items[0]["text"])

    def test_the_market_mood_is_added_only_when_the_source_answered(self):
        mood = {"sentiment": {"value": 12, "label": "Extreme Fear", "mean_prior_30d": 32.0}}
        with_mood = insights.top_headlines(real_20_nov(), mood)[2]["text"]
        self.assertIn("Fear and Greed reads 12 (Extreme Fear)", with_mood)
        without = insights.top_headlines(real_20_nov(), {"sentiment": {"unavailable": "timeout"}})[2]["text"]
        self.assertNotIn("Fear and Greed", without)

    def test_every_headline_is_free_of_advice_words(self):
        for coins in (report.demo_coins(), real_20_nov()):
            for item in insights.top_headlines(coins, None):
                self.assertTrue(insights.wording_ok(item["text"]), item)


class Subject(unittest.TestCase):
    def test_three_coins_at_the_alert_rank(self):
        self.assertEqual(insights.subject_line(real_20_nov()),
                         "Crypto Watch ALERT: 3 coins breach their own history, BTC closes 20.46% below its 30-day average")

    def test_one_coin_at_the_alert_rank(self):
        coins = [summary("BTC", -20.46, {"volatility": 50.0, "distance": 99.6, "drawdown": 60.0}),
                 summary("ETH", 1.0, {"volatility": 10.0, "distance": 20.0, "drawdown": 30.0})]
        self.assertEqual(insights.subject_line(coins), "Crypto Watch ALERT: BTC breaches its own history, closes 20.46% below its 30-day average")

    def test_a_calm_market_names_the_closest_coin(self):
        coins = [summary("BTC", 1.0, {"volatility": 10.0, "distance": 20.0, "drawdown": 30.0}),
                 summary("ETH", 1.0, {"volatility": 10.0, "distance": 88.2, "drawdown": 30.0})]
        self.assertEqual(insights.subject_line(coins), "Crypto Watch: calm market, no coin at its alert rank (closest ETH, rank 88)")

    def test_no_data_says_so_and_states_nothing(self):
        self.assertEqual(insights.subject_line([{"coin": "BTC", "error": "x"}]), "Crypto Watch: market data unavailable today, no statement made")

    def test_a_banned_word_falls_back_to_a_plain_line(self):
        with mock.patch.object(insights, "_phrase", return_value="goes long"):
            line = insights.subject_line(real_20_nov())
        self.assertEqual(line, "Crypto Watch ALERT: 3 of 3 coins at the alert rank")
        self.assertTrue(insights.wording_ok(line))

    def test_the_subject_is_short_enough_for_an_inbox(self):
        for coins in (real_20_nov(), report.demo_coins()):
            self.assertLessEqual(len(insights.subject_line(coins)), 110)

    def test_wording_ok(self):
        self.assertTrue(insights.wording_ok("BTC closes 20.46% below its 30-day average"))
        self.assertFalse(insights.wording_ok("time to buy"))
        self.assertTrue(insights.wording_ok(None))


if __name__ == "__main__":
    unittest.main()
