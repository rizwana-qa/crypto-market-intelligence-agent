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
    return {"coins": coins if coins is not None else report.demo_coins(), "decisions": decisions or [],
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
        self.assertIn("not part of the agent yet", page)
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


if __name__ == "__main__":
    unittest.main()
