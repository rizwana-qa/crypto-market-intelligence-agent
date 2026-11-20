"""Offline tests for the executive brief (report.py): no Kraken, no Groq, no Sheet.

    python tests/test_report.py -v
"""

import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import report  # noqa: E402


def make_data(coins=None, decisions=None, note=""):
    return {"coins": coins if coins is not None else report.demo_coins(), "cross": report.demo_cross(), "decisions": decisions or [],
            "decisions_note": note, "demo": True, "generated": "2026-11-20 12:00"}


def calm_coins():
    now_day = 1_800_000_000 // agent.SECONDS_PER_DAY * agent.SECONDS_PER_DAY
    stamps = [now_day - (300 - i) * agent.SECONDS_PER_DAY for i in range(300)]
    return [report.analyse(coin, stamps, [100.0] * 300) for coin in agent.WATCHLIST]


def decision(**changes):
    base = {"time": "2026-11-20T09:17:41Z", "version": "Agent loop", "alert": True, "coins": ["BTC", "XRP"],
            "message": "BTC drawdown rank 96.6.", "sources": ["Alert rules"], "reasoning": "Ranks are at or above 95.", "blocked_reason": ""}
    base.update(changes)
    return base


class Page(unittest.TestCase):
    def test_the_page_has_every_section_the_reader_needs(self):
        page = report.build_html(make_data(decisions=[decision()]))
        for text in ("EXECUTIVE INTELLIGENCE BRIEF", "The question this agent answers", agent.QUESTION[:40], "Headlines",
                     "Market snapshot", "What the agent does in each situation", "AI analysis", "WRITTEN BY AI",
                     "PASSED THE CODE GUARDRAILS", "What to watch at the next review", "NOT FORECASTS", "Decision history", "Education only"):
            self.assertIn(text, page)
        self.assertIn(report.MONITORING_QUESTION[:60], page.replace("&#x27;", "'"))
        for text in ("Context: trend, volume and relationships", "Conditional watch scenarios", "CONDITIONS TO MONITOR, NOT PREDICTIONS",
                     "never raise an alert on their own", "How the coins move together"):
            self.assertIn(text, page)
        for coin in agent.WATCHLIST:
            self.assertIn(coin, page)

    def test_the_chart_data_is_valid_json_and_cannot_close_the_script_tag(self):
        page = report.build_html(make_data())
        body = re.search(r"var specs=(\{.*?\});var cfg", page, re.S).group(1)
        specs = json.loads(body.replace("<\\/", "</"))
        self.assertEqual(set(specs), {"price", "ranks", "history"})
        script = report.chart_script({"x": {"data": [{"text": "</script><b>"}], "layout": {}}})
        payload = re.search(r"var specs=(\{.*?\});var cfg", script, re.S).group(1)
        self.assertNotIn("</", payload)
        self.assertEqual(json.loads(payload.replace("<\\/", "</"))["x"]["data"][0]["text"], "</script><b>")

    def test_text_from_the_sheet_is_escaped(self):
        evil = decision(message="<script>alert(1)</script>", reasoning="<img src=x onerror=alert(1)>", sources=["<b>x</b>"])
        page = report.build_html(make_data(decisions=[evil]))
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_a_calm_market_says_so(self):
        data = make_data(coins=calm_coins())
        self.assertEqual(report.status_of(data)[0], "CALM")
        self.assertTrue(report.headlines(data)[0].startswith("No coin reached the alert rank"))
        self.assertEqual(report.breaching(data), [])

    def test_an_unusual_move_is_named_with_its_value_and_rank(self):
        data = make_data()
        self.assertEqual(report.status_of(data)[0], "ALERT")
        lines = " ".join(report.headlines(data))
        for coin in report.breaching(data):
            self.assertIn(coin["coin"], lines)
        self.assertIn("rank", lines)

    def test_a_coin_without_data_gets_no_numbers(self):
        coins = report.demo_coins()
        coins[2] = {"coin": "XRP", "error": "XRPUSD: request to Kraken failed after 3 attempts (X)"}
        data = make_data(coins=coins)
        page = report.build_html(data)
        self.assertIn("Data unavailable: XRPUSD", page)
        self.assertIn("Data unavailable, so no statement is made for: XRP", " ".join(report.headlines(data)))
        self.assertEqual({c["coin"] for c in report.good(data)}, {"BTC", "ETH"})

    def test_no_data_at_all_is_a_clear_status_not_a_crash(self):
        coins = [{"coin": c, "error": "down"} for c in agent.WATCHLIST]
        data = make_data(coins=coins)
        self.assertEqual(report.status_of(data)[0], "NO DATA")
        self.assertIn("NO DATA", report.build_html(data))

    def test_a_missing_decision_history_is_explained(self):
        page = report.build_html(make_data(note="The decision history could not be read from the Sheet: SHEET_URL is not set"))
        self.assertIn("SHEET_URL is not set", page)

    def test_a_blocked_decision_is_shown_as_blocked(self):
        page = report.build_html(make_data(decisions=[decision(alert=False, message="", blocked_reason="advice wording")]))
        self.assertIn("BLOCKED BY A GUARDRAIL", page)
        self.assertIn("advice wording", page)

    def test_the_cross_check_compares_the_ai_with_the_code(self):
        data = make_data(decisions=[decision(coins=[c["coin"] for c in report.breaching(make_data())])])
        self.assertTrue(report.agreement(data)[0])
        other = make_data(decisions=[decision(coins=["ETH"])])
        self.assertFalse(report.agreement(other)[0])


class Wording(unittest.TestCase):
    """The report must follow the same rule as the agent: describe, never advise or predict."""

    def test_the_text_written_by_the_code_has_no_advice_words(self):
        for data in (make_data(), make_data(coins=calm_coins())):
            texts = report.headlines(data) + report.triggers(data)
            for _, trigger, action in report.scenario_rows():
                texts += [trigger, action]
            for text in texts:
                self.assertIsNone(agent.ADVICE_RE.search(text), text)

    def test_the_numbers_in_the_scenario_table_come_from_the_agent_settings(self):
        text = " ".join(" ".join(row) for row in report.scenario_rows())
        for value in (agent.ALERT_RANK, agent.HISTORY_LIMIT, agent.MIN_HISTORY_DAYS, agent.MAX_MESSAGE_CHARS):
            self.assertIn(f"{value:g}" if isinstance(value, float) else str(value), text)


class Email(unittest.TestCase):
    def test_the_email_has_the_question_and_the_headlines_and_no_script(self):
        email = report.build_email(make_data(decisions=[decision()]))
        self.assertIn("EXECUTIVE BRIEF", email)
        self.assertIn(agent.QUESTION[:40], email)
        self.assertIn("BTC drawdown rank 96.6.", email)
        self.assertNotIn("<script", email.lower())

    def test_an_old_web_app_gives_a_warning_not_a_crash(self):
        err = agent.AgentError("The Sheet web app refused the request: INVALID_ACTION")
        out = io.StringIO()
        with mock.patch.object(agent, "sheet_post", side_effect=err), redirect_stdout(out):
            sent = report.send_email(make_data(), "<html></html>")
        self.assertFalse(sent)
        self.assertIn("::warning::", out.getvalue())
        self.assertIn("redeploy", out.getvalue())

    def test_the_email_request_carries_the_page_and_the_summary(self):
        with mock.patch.object(agent, "sheet_post", return_value={"success": True}) as post:
            report.send_email(make_data(), "<html>page</html>")
        sent = post.call_args.args[0]
        self.assertEqual((sent["action"], sent["html"]), ("report", "<html>page</html>"))
        self.assertIn("EXECUTIVE BRIEF", sent["summary_html"])


class Levels(unittest.TestCase):
    """Each level is checked by putting it in as the next close and recomputing the metric: it must land on the 95th-percentile value."""

    def test_every_level_lands_on_its_target(self):
        for coin, (stamps, closes, volumes) in report.demo_series().items():
            levels = report.trigger_levels(closes)
            for metric, side in (("distance", "up"), ("distance", "down"), ("volatility", "up"), ("volatility", "down"), ("drawdown", "down")):
                level = levels[metric][side]
                if not isinstance(level, float):
                    continue
                value = agent.metric_series(closes + [level])[0][metric][-1]
                value = abs(value) if metric == "distance" else value
                self.assertAlmostEqual(value * 100, levels[metric]["value_pct"], places=4, msg=f"{coin} {metric} {side}")

    def test_a_flat_history_has_no_level_and_does_not_crash(self):
        levels = report.trigger_levels([100.0] * 300)
        self.assertIsNone(levels["distance"])
        self.assertIsNone(levels["volatility"])
        page = report.build_html(make_data(coins=calm_coins()))
        self.assertIn("Context", page)

    def test_a_metric_that_is_already_at_the_alert_rank_is_said_so(self):
        closes = [100.0 + (i % 3) for i in range(100)] + [100.0 - 4 * i for i in range(1, 11)]   # a sharp fall: deep drawdown
        self.assertEqual(report.trigger_levels(closes)["drawdown"]["down"], "already")

    def test_value_at_rank(self):
        values = list(range(1, 101))
        found = report.value_at_rank(values, 95.0)
        self.assertEqual(found, 96)                                              # value v ranks at v - 0.5, so 95 ranks 94.5 and 96 ranks 95.5
        self.assertGreaterEqual(agent.percentile_rank(values, found), 95.0)
        self.assertLess(agent.percentile_rank(values, found - 1), 95.0)
        self.assertIsNone(report.value_at_rank([5.0, 5.0, 5.0], 95.0))           # all ties: the rank is 50, never 95


class BaseRate(unittest.TestCase):
    def test_episodes_count_runs_not_days(self):
        self.assertEqual(report.episodes([]), 0)
        self.assertEqual(report.episodes([True]), 1)
        self.assertEqual(report.episodes([True, True, True]), 1)
        self.assertEqual(report.episodes([False, True, True, False, True]), 2)

    def test_a_calm_history_never_reaches_the_alert_rank_and_a_shock_does(self):
        n = agent.WINDOW + agent.MIN_HISTORY_DAYS + 40
        stamps = [1_700_000_000 + 86_400 * i for i in range(n)]
        calm = report.base_rate(stamps, [100.0 + (i % 2) * 0.01 for i in range(n)])
        self.assertEqual(calm["ranked_days"], n - (agent.WINDOW + agent.MIN_HISTORY_DAYS - 1))
        shock = report.base_rate(stamps, [100.0 + (i % 2) * 0.01 for i in range(n - 1)] + [60.0])
        self.assertGreaterEqual(shock["alert_days"], 1)
        self.assertTrue(shock["flags"][-1])
        self.assertLessEqual(shock["episodes"], shock["alert_days"])
        self.assertEqual(shock["alert_days"], sum(shock["flags"]))

    def test_the_card_is_on_the_page_and_has_an_any_coin_row(self):
        page = report.build_html(make_data())
        self.assertIn("How often the alert rank is reached", page)
        self.assertIn("Any coin", page)

    def test_no_card_when_a_coin_has_no_history_data(self):
        self.assertEqual(report.base_rate_section(make_data(coins=[{"coin": "BTC", "error": "down"}])), "")


class Presentation(unittest.TestCase):
    def test_the_rank_heatmap_has_one_row_per_coin_and_five_measures(self):
        data = make_data()
        spec = report.charts(data)["ranks"]["data"][0]
        self.assertEqual(spec["type"], "heatmap")
        self.assertEqual(len(spec["z"]), len(report.good(data)))
        self.assertTrue(all(len(row) == 5 for row in spec["z"]))
        self.assertTrue(all(len(row) == 5 for row in spec["text"]))

    def test_a_very_low_relative_strength_rank_is_coloured_as_unusual(self):
        data = make_data()
        data["cross"]["relative_strength"][0]["rank"] = 2.0
        coin = data["cross"]["relative_strength"][0]["coin"]
        index = [c["coin"] for c in report.good(data)].index(coin)
        spec = report.charts(data)["ranks"]["data"][0]
        self.assertEqual(spec["text"][index][4], "2.0")
        self.assertEqual(spec["z"][index][4], 98.0)

    def test_the_levels_are_one_row_per_coin(self):
        data = make_data()
        section = report.watch_section(data)
        self.assertIn("Distance: close above", section)
        self.assertEqual(section.count("last $"), len(report.good(data)))

    def test_the_relationship_flags_are_one_headline(self):
        lines = [x for x in report.headlines(make_data()) if x.startswith("Unusual relationships")]
        self.assertEqual(len(lines), 1)

    def test_the_explanation_comes_after_the_findings(self):
        page = report.build_html(make_data())
        self.assertLess(page.index("Headlines"), page.index("How to read this report"))
        self.assertLess(page.index("Decision history"), page.index("What the agent does in each situation"))


class WatchSectionText(unittest.TestCase):
    def test_the_new_sections_contain_no_advice_words_and_no_forecast_words(self):
        data = make_data()
        text = re.sub(r"<[^>]+>", " ", report.watch_section(data) + report.context_section(data) + report.base_rate_section(data))
        self.assertIsNone(agent.ADVICE_RE.search(text), agent.ADVICE_RE.search(text))
        for word in ("will rise", "will fall", "target price", "expected to"):
            self.assertNotIn(word, text.lower())

    def test_relationships_unavailable_is_stated(self):
        data = make_data()
        data["cross"] = {"error": "only 1 coins"}
        self.assertIn("Relationships unavailable", report.context_section(data))

    def test_the_context_headlines_name_a_volume_flag(self):
        lines = report.headlines(make_data())
        self.assertTrue(any("volume is unusual" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
