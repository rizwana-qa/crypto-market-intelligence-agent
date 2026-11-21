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
import insights  # noqa: E402
import report  # noqa: E402


def make_data(coins=None, decisions=None, note=""):
    coins = coins if coins is not None else report.demo_coins()
    return {"coins": coins, "cross": report.demo_cross(), "decisions": decisions or [], "decisions_note": note, "demo": True,
            "generated": "2026-11-20 12:00", "generated_long": "Friday 20 November 2026", "events": insights.event_study(coins)}


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
        for text in ("Executive Intelligence Brief", "Breach scoreboard", "Top 3 headlines", "The charts", "What the history says",
                     "The question this agent answers", agent.QUESTION[:40], "Everything the code found today",
                     "Market snapshot", "What the agent does in each situation", "AI analysis", "WRITTEN BY AI",
                     "PASSED THE CODE GUARDRAILS", "What to watch at the next review", "Decision history", "Not investment advice"):
            self.assertIn(text, page)
        self.assertIn("not forecasts", page.lower())
        self.assertNotIn("Education only", page)
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
        self.assertEqual(set(specs), {"average", "price", "ranks", "history", "persist", "moves"})
        for key in ("average", "price"):                     # the range buttons are plain data the page script turns into buttons
            self.assertGreaterEqual(len(specs[key]["ranges"]), 2)
            self.assertTrue(all(len(r["visible"]) == len(specs[key]["data"]) for r in specs[key]["ranges"]))
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
            texts += [report.hero_title(data), report.hero_deck(data), insights.subject_line(data["coins"])]
            texts += [item["text"] for item in insights.top_headlines(data["coins"])]
            texts += [re.sub(r"<[^>]+>", " ", report.outlook_section(data))]
            for text in texts:
                self.assertIsNone(agent.ADVICE_RE.search(text), text)

    def test_the_numbers_in_the_scenario_table_come_from_the_agent_settings(self):
        text = " ".join(" ".join(row) for row in report.scenario_rows())
        for value in (agent.ALERT_RANK, agent.HISTORY_LIMIT, agent.MIN_HISTORY_DAYS, agent.MAX_MESSAGE_CHARS):
            self.assertIn(f"{value:g}" if isinstance(value, float) else str(value), text)


class Email(unittest.TestCase):
    def test_the_email_has_the_question_and_the_headlines_and_no_script(self):
        email = report.build_email(make_data(decisions=[decision()]))
        self.assertIn("DAILY BRIEF", email)
        self.assertIn("Top 3 headlines", email)
        self.assertIn("BTC drawdown rank 96.6.", email)
        self.assertIn("Rizwana Zahoor", email)
        self.assertIn("python rag_server.py", email)           # said plainly as a command to run, not offered as a link that cannot open
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
        self.assertIn("DAILY BRIEF", sent["summary_html"])
        self.assertEqual(sent["subject"], insights.subject_line(make_data()["coins"]))
        self.assertTrue(sent["subject"].startswith("Crypto Watch"))


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

    def test_every_scoreboard_row_has_the_same_cells_so_the_columns_line_up(self):
        data = make_data()
        board = report.scoreboard(data)
        rows = re.findall(r'<div class="srow">(.*?)</dl></div>', board, flags=re.S)
        self.assertEqual(len(rows), len(report.good(data)))
        for row in rows:
            self.assertEqual(row.count("<dt>"), len(agent.METRICS))
            self.assertEqual(row.count('<dd class="v">'), len(agent.METRICS))
            self.assertEqual(row.count('<dd class="r">'), len(agent.METRICS))
            self.assertEqual(row.count('class="who"'), 1)

    def test_the_scoreboard_adapts_to_the_card_width_not_to_the_screen_width(self):
        css = report.CSS
        self.assertIn("container:score/inline-size", css)
        self.assertIn("@container score (min-width:", css)
        # The default (narrow) layout must not need container query support to stay inside the card.
        self.assertIn(".srow{display:grid;grid-template-columns:minmax(0,1fr)", css)

    def test_the_relationship_flags_are_one_headline(self):
        lines = [x for x in report.headlines(make_data()) if x.startswith("Unusual relationships")]
        self.assertEqual(len(lines), 1)

    def test_the_explanation_comes_after_the_findings(self):
        page = report.build_html(make_data())
        self.assertLess(page.index("Top 3 headlines"), page.index("How to read this report"))
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


class NewDesign(unittest.TestCase):
    def test_the_byline_is_on_the_page_and_the_email_and_names_only_the_author(self):
        data = make_data(decisions=[decision()])
        for text in (report.build_html(data), report.build_email(data)):
            self.assertIn("Created by", text)
            self.assertIn("Rizwana Zahoor", text)
            self.assertNotIn("Densight", text)

    def test_the_hero_names_the_coins_that_breach(self):
        data = make_data()
        self.assertEqual(report.hero_title(data), "Two coins breach their own history")
        self.assertIn("of ", report.hero_deck(data))                     # the rarity sentence: "only k of n days in its history, today included"
        self.assertIn("today included", report.hero_deck(data))
        self.assertEqual(report.hero_title(make_data(coins=calm_coins())), "Calm market: no coin at its alert rank")
        self.assertTrue(report.hero_deck(make_data(coins=calm_coins())).startswith("No coin reached the alert rank"))

    def test_the_rarity_counts_the_day_itself_and_every_as_extreme_day(self):
        closes = [100.0 + (i % 5) for i in range(200)] + [60.0]            # one huge fall on the last day
        coin = {"closes": closes}
        found, ranked = report.rarity(coin, "distance")
        self.assertEqual(found, 1)                                          # only today
        self.assertEqual(ranked, len(closes) - agent.WINDOW)

    def test_the_page_has_three_headlines_and_the_deck_has_no_duplicate_sentence(self):
        page = report.build_html(make_data())
        self.assertEqual(page.count('class="hl"'), 3)
        deck = report.hero_deck(make_data())
        sentences = [s.strip() for s in deck.split(". ") if s.strip()]
        self.assertEqual(len(sentences), len(set(sentences)))

    def test_without_the_candles_in_the_data_the_page_still_builds(self):
        data = make_data()
        for c in data["coins"]:
            c.pop("closes"), c.pop("stamps")
        data["events"] = None
        page = report.build_html(data)
        self.assertNotIn("What the history says", page)
        self.assertNotIn('data-chart="average"', page)
        self.assertIn('data-chart="ranks"', page)

    def test_too_few_episodes_show_no_outcome_numbers(self):
        data = make_data()
        data["events"] = {**data["events"], "enough": False, "episodes": 3}
        text = re.sub(r"<[^>]+>", " ", report.outlook_section(data))
        self.assertIn("fewer than", text)
        self.assertNotIn("Median 30-day move", text)
        self.assertNotIn('data-chart="moves"', report.build_html(data))

    def test_a_small_negative_move_never_reads_as_minus_zero(self):
        self.assertEqual(report._band(-0.4), "\u22120.4%")
        self.assertEqual(report._band(-0.02), "0.0%")
        self.assertEqual(report._band(14.6), "+15%")
        self.assertEqual(report._band(-14.2), "\u221214%")

    def test_the_outlook_headline_follows_the_numbers(self):
        base = {"again": [90.0] + [60.0] * 6 + [40.0] * 14, "after_episode": {7: {"up_pct": 41.0}}, "any_day": {7: {"up_pct": 46.0}}}
        self.assertEqual(report.outlook_headline(base), "Stress is sticky. Direction is not.")
        fading = {**base, "again": [30.0] + [5.0] * 20}
        self.assertTrue(report.outlook_headline(fading).startswith("Stress fades fast."))
        edge = {**base, "after_episode": {7: {"up_pct": 80.0}}}
        self.assertIn("differs", report.outlook_headline(edge))

    def test_the_page_script_loads_plotly_once_and_has_no_inline_event_attributes(self):
        page = report.build_html(make_data())
        self.assertEqual(page.count("plotly-2.35.2.min.js"), 1)
        self.assertNotRegex(page, r"\son(click|load|error)=")

    def test_main_prints_the_subject_and_emails_with_it(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(report.main(["--demo", "--out", str(Path(__file__).parent / "_demo_out.html")]), 0)
        (Path(__file__).parent / "_demo_out.html").unlink()
        self.assertIn("[report] subject: Crypto Watch ALERT", out.getvalue())


if __name__ == "__main__":
    unittest.main()
