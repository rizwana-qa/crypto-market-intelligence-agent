"""Offline tests for the volume, relative strength, correlation and Sheet-tab code. No Kraken, no Groq, no Sheet.

    python tests/test_context.py -v

Every expected number is worked out by hand in a comment next to the test.
"""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import scenarios  # noqa: E402

CODE_GS = (Path(__file__).resolve().parent.parent / "apps_script" / "Code.gs").read_text(encoding="utf-8")
STAMPS = [86400 * (20000 + i) for i in range(100)]


def tab_width(name):
    block = re.search(r"const EXTRA_TABS = \{(.*?)\n\};", CODE_GS, re.S).group(1)
    row = re.search(name + r": \[(.*?)\]", block, re.S).group(1)
    return len(re.findall(r"'[^']*'", row))


class Quiet(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(agent, "log", lambda message: None)
        patcher.start()
        self.addCleanup(patcher.stop)


class Candles(unittest.TestCase):
    GOOD = [1700000000, "1", "2", "0.5", "1.5", "1", "10.5", 3]

    def test_the_volume_is_kept(self):
        self.assertEqual(agent.parse_candles([self.GOOD], "XBTUSD")[0]["volume"], 10.5)

    def test_a_bad_volume_is_refused(self):
        for bad in ("nan", "-1", "abc", "inf"):
            row = self.GOOD[:6] + [bad, 3]
            with self.assertRaises(ValueError, msg=bad):
                agent.parse_candles([row], "XBTUSD")


class Volume(unittest.TestCase):
    def test_ratio_is_today_over_the_mean_of_the_30_days_before(self):
        ratios = agent.volume_ratio_series([10.0] * 30 + [30.0])   # 30 / mean(ten thirty times) = 3
        self.assertEqual(ratios[:30], [None] * 30)
        self.assertEqual(ratios[30], 3.0)

    def test_missing_volume_or_a_zero_mean_gives_no_ratio(self):
        self.assertEqual(agent.volume_ratio_series([None] + [10.0] * 40), [None] * 41)
        self.assertEqual(agent.volume_ratio_series([0.0] * 31), [None] * 31)

    def test_a_volume_spike_is_flagged_and_does_not_touch_the_alert_breaches(self):
        closes = [100.0 + (i % 3) for i in range(100)]
        volumes = [10.0] * 99 + [50.0]
        summary = agent.add_context(agent.summarize("BTC", STAMPS, closes), closes, volumes)
        # 70 ratios (days 30 to 99): 69 are 1.0 and the last is 5.0 -> rank (69 + 0.5) / 70 = 99.3
        self.assertEqual(summary["context"]["volume_ratio_30d"], 5.0)
        self.assertEqual(summary["context"]["volume_rank"], 99.3)
        self.assertEqual(summary["flags"], ["volume"])
        self.assertEqual(summary["breaches"], [])

    def test_ordinary_volume_is_not_flagged(self):
        closes = [100.0] * 100
        summary = agent.add_context(agent.summarize("ETH", STAMPS, closes), closes, [10.0] * 100)
        self.assertEqual(summary["flags"], [])
        self.assertEqual(summary["context"]["volume_rank"], 50.0)   # 70 equal ratios: ties count half

    def test_candles_without_volume_give_a_summary_without_volume(self):
        closes = [100.0] * 100
        summary = agent.add_context(agent.summarize("XRP", STAMPS, closes), closes, [None] * 100)
        self.assertNotIn("volume_rank", summary["context"])
        self.assertEqual(summary["flags"], [])


class Trend(unittest.TestCase):
    def test_up_down_and_mixed(self):
        rising = [float(i) for i in range(1, 101)]
        falling = rising[::-1]
        self.assertEqual(agent.trend_label(rising)[0], "up")       # +42.9% in 30 days, +166.7% in 60, above its average
        self.assertEqual(agent.trend_label(falling)[0], "down")
        bounce = [100.0] * 40 + [150.0] * 30 + [120.0] * 30         # 30-day change -20%, 60-day change +20%
        self.assertEqual(agent.trend_label(bounce)[0], "mixed")

    def test_changes_are_returned(self):
        label, ret30, ret60 = agent.trend_label([100.0] * 40 + [110.0] * 21)
        self.assertEqual(label, "up")
        self.assertAlmostEqual(ret30, 0.10)
        self.assertAlmostEqual(ret60, 0.10)


def wave(scale, offset=0.0, n=100):
    """Closes with ups and downs that never repeat exactly, so every 30-day window has some spread."""
    out, price = [], 100.0
    for i in range(n):
        out.append(price)
        price *= 1 + scale * ((i * 7 + 3) % 11 - 5) / 100 + offset
    return out


class CrossAsset(unittest.TestCase):
    def series(self, **closes):
        return {coin: (STAMPS[:len(c)], c) for coin, c in closes.items()}

    def test_relative_strength_is_the_return_minus_the_group_mean(self):
        up = [100.0] * 70 + [100.0 + i * (10.0 / 30) for i in range(1, 31)]   # 100 -> 110: +10% over the last 30 days
        flat = [100.0] * 100
        result = agent.cross_asset_summary(self.series(BTC=up, ETH=flat))
        strength = {x["coin"]: x for x in result["relative_strength"]}
        self.assertEqual(strength["BTC"]["ret_30d_pct"], 10.0)
        self.assertEqual(strength["BTC"]["vs_group_pct"], 5.0)       # 10 - mean(10, 0)
        self.assertEqual(strength["ETH"]["vs_group_pct"], -5.0)      # 0 - 5

    def test_identical_returns_give_a_correlation_of_one(self):
        a = wave(1.0)
        b = [2 * x for x in a]                                       # the same daily returns
        result = agent.cross_asset_summary(self.series(BTC=a, ETH=b))
        self.assertEqual(result["correlations"][0]["pair"], "BTC-ETH")
        self.assertEqual(result["correlations"][0]["corr_30d"], 1.0)

    def test_a_flat_coin_has_no_correlation_and_no_crash(self):
        result = agent.cross_asset_summary(self.series(BTC=wave(1.0), ETH=wave(2.0), XRP=[1.0] * 100))
        pairs = [c["pair"] for c in result["correlations"]]
        self.assertEqual(pairs, ["BTC-ETH"])                         # a flat series has no defined correlation

    def test_a_correlation_far_from_its_history_is_flagged(self):
        a = wave(1.0)
        b = [x * (1 + 0.0) for x in a]
        # b follows a for 70 days, then does the opposite of a for the last 30: correlation drops from 1 to about -1
        returns_a = [y / x - 1 for x, y in zip(a, a[1:])]
        closes_b, price = [100.0], 100.0
        for i, r in enumerate(returns_a):
            price *= 1 + (r if i < 69 else -r)
            closes_b.append(price)
        result = agent.cross_asset_summary(self.series(BTC=a, ETH=closes_b))
        self.assertLessEqual(result["correlations"][0]["rank"], agent.UNUSUAL_LOW)
        self.assertIn("correlation BTC-ETH", result["flags"])

    def test_only_the_common_days_are_used_and_too_few_is_an_error(self):
        short = (STAMPS[50:], wave(1.0, n=50))
        with self.assertRaises(ValueError):
            agent.cross_asset_summary({"BTC": (STAMPS, wave(1.0)), "ETH": short})

    def test_the_tool_needs_two_coins(self):
        def only_btc(coin, now):
            if coin == "BTC":
                return STAMPS, wave(1.0), [1.0] * 100
            raise RuntimeError(f"{coin} down")
        with mock.patch.object(agent, "load_coin", only_btc):
            self.assertIn("error", agent.tool_cross_asset_summary())

    def test_the_tool_reports_the_coin_that_is_down(self):
        def two_of_three(coin, now):
            if coin == "XRP":
                raise RuntimeError("XRPUSD down")
            return STAMPS, wave(1.0 if coin == "BTC" else 2.0), [1.0] * 100
        with mock.patch.object(agent, "load_coin", two_of_three):
            result = agent.tool_cross_asset_summary()
        self.assertEqual(result["coins"], ["BTC", "ETH"])
        self.assertIn("XRP", result["unavailable"])


class NoAlertOnContextAlone(Quiet):
    TITLES = [section["title"] for section in agent.load_playbook()]

    def evidence(self, breaches):
        summary = scenarios.fake_market("BTC", 67000.0, 0.5, 1.0, 2.0, -3.0, 40.0, 40.0, 40.0, volume_ratio=3.4, volume_rank=98.2)
        summary["breaches"] = breaches
        return [{"tool": "get_market_summary", "args": {"coin": "BTC"}, "result": summary}]

    def alert(self, coins=("BTC",)):
        return {"alert": True, "coins": list(coins), "message": "BTC volume was 3.4 times its 30-day average, rank 98.2.",
                "sources": ["Volume rule"], "reasoning": "Volume rank 98.2."}

    def test_a_volume_flag_without_a_price_breach_cannot_alert(self):
        result = agent.apply_guardrails(self.alert(), self.TITLES, self.evidence([]))
        self.assertFalse(result["alert"])
        self.assertIn("context never alerts alone", result["blocked_reason"])

    def test_a_price_breach_lets_the_alert_through(self):
        self.assertTrue(agent.apply_guardrails(self.alert(), self.TITLES, self.evidence(["drawdown"]))["alert"])

    def test_every_alerted_coin_needs_its_own_breach(self):
        result = agent.apply_guardrails(self.alert(("BTC", "ETH")), self.TITLES, self.evidence(["drawdown"]))
        self.assertFalse(result["alert"])
        self.assertIn("ETH", result["blocked_reason"])


class SheetTabs(Quiet):
    def evidence(self):
        market = {c: scenarios.fake_market(c, 100.0, 1.0, 2.0, 3.0, -4.0, 50.0, 60.0, 70.0) for c in agent.WATCHLIST}
        items = [{"tool": "get_market_summary", "args": {"coin": c}, "result": r} for c, r in market.items()]
        items[2] = {"tool": "get_market_summary", "args": {"coin": "XRP"}, "result": scenarios.outage("XRP")}
        items.append({"tool": "get_cross_asset_summary", "args": {}, "result": scenarios.fake_cross(corr_ranks=(2.0, 50.0, 50.0))})
        return items

    def test_market_rows_have_the_width_of_the_market_data_tab(self):
        rows = agent.market_rows(self.evidence(), "Agent loop", "2026-11-20T05:17:00Z", "123")
        self.assertEqual([r[3] for r in rows], ["BTC", "ETH", "XRP"])
        for row in rows:
            self.assertEqual(len(row), tab_width("market_data"))
        self.assertTrue(rows[2][-1].startswith("ERROR"))             # a coin that failed is shown, not hidden
        self.assertEqual(rows[0][-1], "ok")

    def test_the_relative_strength_of_each_coin_is_in_its_row(self):
        rows = agent.market_rows(self.evidence(), "Agent loop", "t", "1")
        self.assertEqual(rows[0][20], 0.8)                           # BTC vs_group_pct from fake_cross

    def test_cross_rows_have_the_width_of_the_cross_asset_tab_and_mark_the_unusual_pair(self):
        rows = agent.cross_rows(self.evidence(), "Agent loop", "t", "1")
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(len(row), tab_width("cross_asset"))
        self.assertEqual([r[7] for r in rows], ["unusual", "", ""])

    def test_no_cross_rows_when_the_cross_tool_failed(self):
        evidence = [{"tool": "get_cross_asset_summary", "args": {}, "result": {"error": "down"}}]
        self.assertEqual(agent.cross_rows(evidence, "v", "t", "1"), [])

    def test_a_refused_append_is_a_warning_and_not_an_error(self):
        with mock.patch.object(agent, "sheet_post", side_effect=agent.AgentError("INVALID_ACTION")):
            self.assertFalse(agent.sheet_append("market_data", [["x"]]))

    def test_scenario_rows_have_the_width_of_the_scenarios_tab(self):
        captured = []
        result = {"name": "n", "passed": True, "expected": "e", "calls": 3,
                  "decision": {"alert": True, "coins": ["BTC"], "message": "m", "sources": ["a", "b"], "blocked_reason": ""}}
        with mock.patch.object(agent, "sheet_append", lambda tab, rows: captured.append((tab, rows))):
            scenarios.publish([(1, result)], "Agent loop")
        tab, rows = captured[0]
        self.assertEqual(tab, "scenarios")
        self.assertEqual(len(rows[0]), tab_width("scenarios"))

    def test_the_web_app_only_knows_the_four_extra_tabs(self):
        self.assertEqual(set(re.findall(r"^\s{2}(\w+): \[", re.search(r"const EXTRA_TABS = \{(.*?)\n\};", CODE_GS, re.S).group(1), re.M)),
                         {"market_data", "cross_asset", "context_data", "scenarios"})


class Scenarios(unittest.TestCase):
    def test_there_are_20_scenarios_with_unique_names(self):
        names = [s["name"] for s in scenarios.scenarios()]
        self.assertEqual(len(names), 20)
        self.assertEqual(len(set(names)), 20)

    def test_only_selects_by_number(self):
        self.assertIsNone(scenarios.parse_only(""))
        self.assertEqual(scenarios.parse_only("8-10"), {8, 9, 10})
        self.assertEqual(scenarios.parse_only("1,3,12-13"), {1, 3, 12, 13})
        for bad in ("x", "1;2", "8-", "-3", "1, 2"):
            with self.assertRaises(agent.AgentError, msg=bad):
                scenarios.parse_only(bad)

    def test_the_fake_summaries_have_the_same_fields_as_the_real_one(self):
        real = agent.add_context(agent.summarize("BTC", STAMPS, [100.0 + i % 5 for i in range(100)]), [100.0] * 100, [10.0] * 100)
        fake = scenarios.fake_market("BTC", 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
        self.assertEqual(set(real), set(fake))
        self.assertEqual(set(real["context"]), set(fake["context"]))

    def test_the_fake_cross_summary_has_the_same_fields_as_the_real_one(self):
        series = {c: (STAMPS, wave(float(i + 1))) for i, c in enumerate(agent.WATCHLIST)}
        self.assertEqual(set(agent.cross_asset_summary(series)), set(scenarios.fake_cross()))

    def test_every_tool_the_agent_can_call_has_a_fake(self):
        self.assertEqual(set(scenarios.fake_tools({}, [])), set(agent.TOOLS))

    def test_a_full_plan_fits_the_step_limit(self):
        steps = ([{"tool": "get_market_summary", "args": {"coin": c}, "reason": "r"} for c in agent.WATCHLIST]
                 + [{"tool": "get_cross_asset_summary", "args": {}, "reason": "r"},
                    {"tool": "get_market_context", "args": {}, "reason": "r"},
                    {"tool": "get_alert_history_stats", "args": {}, "reason": "r"},
                    {"tool": "get_recent_decisions", "args": {}, "reason": "r"}]
                 + [{"tool": "search_playbook", "args": {"query": q}, "reason": "r"}
                    for q in ("volatility rule", "drawdown rule", "alert rules repeat alerts wording", "volume correlation relative strength")])
        self.assertLessEqual(len(steps), agent.MAX_PLAN_STEPS)
        self.assertEqual(len(agent.validate_plan({"steps": steps})), 11)


class PlaybookCoversTheNewMetrics(unittest.TestCase):
    def test_each_new_metric_has_a_section_and_a_search_finds_it(self):
        for query, title in (("volume spike", "Volume rule"), ("relative strength outperform", "Relative strength rule"),
                             ("correlation decouple pair", "Correlation rule")):
            self.assertEqual(agent.tool_search_playbook(query)["sections"][0]["title"], title)

    def test_the_sections_state_that_context_never_alerts_alone(self):
        text = Path(agent.PLAYBOOK_PATH).read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("never raises an alert on its own"), 4)


if __name__ == "__main__":
    unittest.main()
