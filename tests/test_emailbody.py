"""Offline tests for the email body (emailbody.py and report.email_facts): no Kraken, no Groq, no Sheet.

    python tests/test_emailbody.py -v

An email program runs no scripts and ignores most CSS, so these tests check the HTML itself: tables and inline styles only,
every tag closed, nothing the common mail programs strip, the size under the budget, and the facts the owner asked to see
(the AI analysis even when the agent sent no alert, a link that works or a plain statement that there is none).
"""
import io
import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import emailbody  # noqa: E402
import insights  # noqa: E402
import report  # noqa: E402


def make_data(decisions=None, coins=None):
    coins = coins if coins is not None else report.demo_coins()
    return {"coins": coins, "cross": report.demo_cross(), "context": report.demo_context(coins), "decisions": decisions if decisions is not None else report.demo_decisions()[0],
            "decisions_note": "", "demo": True, "generated": "2026-11-21 08:30", "generated_long": "Saturday 21 November 2026", "events": insights.event_study(coins)}


def decision(**changes):
    base = {"time": "2026-11-21T08:28:41Z", "version": "Agent loop", "alert": False, "coins": [], "message": "", "sources": ["Alert rules", "Repeat rule"],
            "reasoning": "A prior alert today already covers these coins, so the repeat rule applies.", "blocked_reason": ""}
    base.update(changes)
    return base


class Structure(HTMLParser):
    """Collects the problems an email program would trip on: unclosed or crossed tags, cells outside rows, rows outside tables."""

    VOID = {"br", "hr", "img", "meta", "link", "input"}

    def __init__(self):
        super().__init__()
        self.stack, self.problems, self.tags = [], [], set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        if tag in self.VOID:
            return
        parent = self.stack[-1] if self.stack else None
        if tag == "td" and parent != "tr":
            self.problems.append(f"td inside {parent}")
        if tag == "tr" and parent not in ("table", "tbody"):
            self.problems.append(f"tr inside {parent}")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"</{tag}> closes {self.stack[-1] if self.stack else 'nothing'}")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()


def lint(html):
    parser = Structure()
    parser.feed(html)
    parser.close()
    if parser.stack:
        parser.problems.append("unclosed: " + ", ".join(parser.stack))
    return parser


class Markup(unittest.TestCase):
    def test_the_email_is_well_formed_tables_with_inline_styles_only(self):
        for data in (make_data(), make_data(decisions=[decision()]), make_data(decisions=[])):
            html = report.build_email(data)
            parser = lint(html)
            self.assertEqual(parser.problems, [])
            self.assertTrue({"table", "tr", "td"} <= parser.tags)

    def test_nothing_an_email_program_strips_or_breaks_on(self):
        html = report.build_email(make_data(decisions=[decision()]))
        lower = html.lower()
        for banned in ("<script", "<style", "<link", "<img", "<iframe", "<svg", "<canvas", "@import", "@media", "javascript:", "onclick=", "onload=", "onerror="):
            self.assertNotIn(banned, lower)
        for css in ("display:flex", "display:grid", "position:absolute", "position:fixed", "gradient(", "var(--", "calc("):
            self.assertNotIn(css, lower.replace(" ", ""))

    def test_it_is_one_column_that_shrinks_on_a_phone(self):
        html = report.build_email(make_data())
        self.assertIn("max-width:640px;width:100%", html)
        self.assertIn('width="640"', html)
        self.assertNotRegex(html, r"width:(\d{4,})px")

    def test_every_colour_pair_the_email_uses_is_readable(self):
        def luminance(hex_color):
            r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
            lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
            return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

        def ratio(a, b):
            hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        for text in (emailbody.TEXT, emailbody.MUTED, emailbody.AMBER, emailbody.VIOLET, "#D5DAE4"):
            for background in (emailbody.CARD, emailbody.PANEL, "#1A1530"):
                self.assertGreaterEqual(ratio(text, background), 4.5, (text, background))
        for tone, background in emailbody.RANK_BG.items():
            if tone != "none":
                self.assertGreaterEqual(ratio("#FFFFFF", background), 4.5, tone)
        for background in list(emailbody.STATUS_BG.values()) + list(emailbody.PILL_BG.values()):
            self.assertGreaterEqual(ratio("#FFFFFF", background), 4.5, background)

    def test_text_from_the_data_is_escaped(self):
        evil = decision(message="<script>alert(1)</script> & <b>bold</b>", reasoning="<img src=x onerror=alert(1)>", sources=["<i>x</i>"])
        html = report.build_email(make_data(decisions=[evil]))
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("<img", html.lower())
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(lint(html).problems, [])


class Content(unittest.TestCase):
    def test_the_email_carries_the_whole_brief_not_just_a_summary(self):
        html = report.build_email(make_data(decisions=[decision(alert=True, coins=["BTC"], message="BTC drawdown rank 96.6.")]))
        for text in ("Created by", "Rizwana Zahoor", "Breach scoreboard", "Top 3 headlines", "Rank heatmap", "Highest rank, last 30 days", "AI analysis", "WRITTEN BY AI",
                     "What the history says", "How many days the alert rank lasts", "Where prices went afterwards", "How far to trust this", "Five questions this data raises",
                     "What to watch at the next review", "Closes that would reach the alert rank", "Conditional watch scenarios", "Context: trend, volume, relationships",
                     "Market context", "Decision history", "Not investment advice"):
            self.assertIn(text, html)
        for coin in agent.WATCHLIST:
            self.assertIn(coin, html)

    def test_the_numbers_are_the_ones_on_the_page(self):
        data = make_data()
        page, email = report.build_html(data), report.build_email(data)
        for c in report.good(data):
            for metric in agent.METRICS:
                rank = f"{c['summary']['ranks'][metric]:.1f}"
                self.assertIn(rank, page)
                self.assertIn(rank, email)
            self.assertIn(insights.metric_value(c["summary"], "drawdown"), email)
        for item in insights.top_headlines(data["coins"], data["context"]):
            self.assertIn(item["text"].replace("'", "&#x27;"), email)
        self.assertIn(report.hero_title(data), email)
        self.assertIn(report.outlook_headline(data["events"]), email)

    def test_the_ai_analysis_is_there_even_when_the_agent_sent_no_alert(self):
        html = report.build_email(make_data(decisions=[decision()]))
        self.assertIn("AI analysis", html)
        self.assertIn("A prior alert today already covers these coins", html)
        self.assertIn("No alert message with this decision", html)
        self.assertIn("PASSED THE CODE GUARDRAILS", html)
        self.assertIn("Alert: no", html)

    def test_a_blocked_message_says_a_guardrail_removed_it(self):
        html = report.build_email(make_data(decisions=[decision(alert=True, coins=["BTC"], message="", blocked_reason="advice wording")]))
        self.assertIn("BLOCKED BY A GUARDRAIL", html)
        self.assertIn("removed by a guardrail", html)
        self.assertIn("advice wording", html)

    def test_without_a_saved_decision_the_email_says_so(self):
        html = report.build_email(make_data(decisions=[]))
        self.assertIn("No decision has been saved to the Sheet yet.", html)
        self.assertNotIn("Decision history", html)

    def test_a_coin_without_data_is_named_and_not_estimated(self):
        coins = report.demo_coins()
        coins[1] = {"coin": coins[1]["coin"], "error": "Kraken returned no candles"}
        html = report.build_email(make_data(coins=coins))
        self.assertIn("Kraken returned no candles", html)
        self.assertIn("No statement is made for this coin", html)
        self.assertEqual(lint(html).problems, [])

    def test_a_calm_market_reads_calm(self):
        html = report.build_email(make_data(coins=calm_coins()))
        self.assertIn("CALM MARKET", html)
        self.assertIn("Calm market: no coin at its alert rank", html)

    def test_there_is_no_price_forecast_or_advice_wording_outside_the_disclaimer(self):
        html = report.build_email(make_data(decisions=[decision()]))
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        facts = report.email_facts(make_data(decisions=[decision()]))
        for sentence in (facts["disclaimer_lead"], facts["disclaimer"]):          # the disclaimer says "not advice" and "no recommendation" on purpose
            self.assertIn(sentence, text.replace("&#x27;", "'"))
            text = text.replace(sentence, " ")
        self.assertEqual([m.group(0) for m in agent.ADVICE_RE.finditer(text)], [])

    def test_the_heatmap_colours_follow_the_alert_and_near_ranks(self):
        facts = report.email_facts(make_data())
        tones = {(r["coin"], i): cell["tone"] for r in facts["heat"]["rows"] for i, cell in enumerate(r["cells"])}
        for c in report.good(make_data()):
            for i, metric in enumerate(agent.METRICS):
                rank = c["summary"]["ranks"][metric]
                self.assertEqual(tones[(c["coin"], i)], "hi" if rank >= agent.ALERT_RANK else "mid" if rank >= report.NEAR_RANK else "lo")

    def test_the_rank_strip_has_one_block_per_day_for_every_coin(self):
        facts = report.email_facts(make_data())
        days = facts["history"]["days"]
        self.assertEqual(days, report.STRIP_DAYS)
        self.assertTrue(all(len(r["cells"]) == days for r in facts["history"]["rows"]))
        html = report.build_email(make_data())
        self.assertEqual(html.count('height="18"'), days * len(facts["history"]["rows"]))


def calm_coins():
    now_day = 1_800_000_000 // agent.SECONDS_PER_DAY * agent.SECONDS_PER_DAY
    stamps = [now_day - (300 - i) * agent.SECONDS_PER_DAY for i in range(300)]
    return [report.analyse(coin, stamps, [100.0] * 300) for coin in agent.WATCHLIST]


class Links(unittest.TestCase):
    def render(self, **env):
        with mock.patch.dict(os.environ, env, clear=False):
            for name in ("REPORT_URL", "ASK_URL"):
                if name not in env:
                    os.environ.pop(name, None)
            return report.build_email(make_data(decisions=[decision()]))

    def test_without_published_addresses_the_email_has_no_link_that_cannot_work(self):
        html = self.render()
        self.assertNotIn("127.0.0.1", html)
        self.assertNotIn("href=", html)
        self.assertIn("attached to this email", html)
        self.assertIn("not a web link", html)
        self.assertIn(report.LAUNCHER, html)

    def test_a_published_address_becomes_a_button(self):
        html = self.render(REPORT_URL="https://example.github.io/crypto-watch/report.html", ASK_URL="https://example.github.io/crypto-watch/ask.html")
        self.assertIn('href="https://example.github.io/crypto-watch/report.html"', html)
        self.assertIn('href="https://example.github.io/crypto-watch/ask.html"', html)
        self.assertIn("Open the interactive report", html)
        self.assertIn("Ask the playbook", html)
        self.assertNotIn("not a web link", html)

    def test_only_https_addresses_without_markup_characters_are_accepted(self):
        for bad in ("http://example.com/x", "javascript:alert(1)", "https://x.example/a b", 'https://x.example/"onmouseover="x', "https://x.example/<b>", "", "   ", "https://" + "a" * 400):
            with mock.patch.dict(os.environ, {"ASK_URL": bad}):
                self.assertIsNone(report.public_url("ASK_URL"), bad)
        with mock.patch.dict(os.environ, {"ASK_URL": " https://ok.example/ask "}):
            self.assertEqual(report.public_url("ASK_URL"), "https://ok.example/ask")

    def test_the_page_link_is_local_only_when_nothing_is_published(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASK_URL", None)
            self.assertIn(report.RAG_URL, report.ask_link())
            self.assertIn(report.LAUNCHER, report.ask_link())
        with mock.patch.dict(os.environ, {"ASK_URL": "https://example.org/ask"}):
            self.assertIn('href="https://example.org/ask"', report.ask_link())
            self.assertNotIn("127.0.0.1", report.ask_link())


class Size(unittest.TestCase):
    def test_the_full_email_fits_the_budget_and_gmail_does_not_clip_it(self):
        html, dropped = report.build_email_parts(make_data())
        size = len(html.encode("utf-8"))
        self.assertLessEqual(size, emailbody.EMAIL_BUDGET)
        self.assertLess(size, 95_000)            # Gmail clips an HTML email near 102 KB
        self.assertEqual(dropped, [])

    def test_a_long_decision_history_cannot_push_it_over(self):
        many = [decision(time=f"2026-10-{d:02d}T09:17:00Z", message="x" * 400) for d in range(1, 29)]
        html = report.build_email(make_data(decisions=many))
        self.assertLessEqual(len(html.encode("utf-8")), emailbody.EMAIL_BUDGET)
        self.assertLessEqual(html.count("ALERT") + html.count("no alert"), 40)

    def test_over_budget_the_least_important_sections_go_first_and_the_core_stays(self):
        facts = report.email_facts(make_data(decisions=[decision()]))
        full, _ = emailbody.render(facts, 10 ** 9)
        mid, dropped = emailbody.render(facts, len(full.encode()) - 3000)
        self.assertGreaterEqual(len(dropped), 1)
        self.assertEqual(dropped[0], "decisions")
        self.assertEqual(dropped, [k for k in emailbody.DROP_ORDER if k in dropped])        # dropped in the stated order, nothing out of turn
        self.assertNotIn("Decision history", mid)
        tiny, dropped = emailbody.render(facts, 1)
        for core in ("Breach scoreboard", "Top 3 headlines", "Rank heatmap", "AI analysis", "Not investment advice", "Created by"):
            self.assertIn(core, tiny)
        self.assertEqual(set(dropped), set(emailbody.DROP_ORDER))
        self.assertEqual(lint(tiny).problems, [])

    def test_the_compact_body_fits_the_limit_of_the_first_web_app(self):
        html, _ = report.build_email_parts(make_data(decisions=[decision()]), emailbody.COMPACT_BUDGET)
        self.assertLessEqual(len(html), 30000)
        self.assertLessEqual(len(html.encode("utf-8")), emailbody.COMPACT_BUDGET)


class Sending(unittest.TestCase):
    def test_the_full_body_goes_out_first_and_once_when_the_web_app_accepts_it(self):
        with mock.patch.object(agent, "sheet_post", return_value={"success": True}) as post, redirect_stdout(io.StringIO()) as out:
            self.assertTrue(report.send_email(make_data(decisions=[decision()]), "<html>page</html>"))
        self.assertEqual(post.call_count, 1)
        self.assertGreater(len(post.call_args.args[0]["summary_html"]), 30000)
        self.assertIn("sections left out: none", out.getvalue())

    def test_an_older_web_app_that_limits_the_body_gets_the_shorter_one_and_a_warning(self):
        refuse = agent.AgentError("The Sheet web app refused the request: INVALID_PAYLOAD")
        results = [refuse, {"success": True}]
        with mock.patch.object(agent, "sheet_post", side_effect=results) as post, redirect_stdout(io.StringIO()) as out:
            self.assertTrue(report.send_email(make_data(decisions=[decision()]), "<html>page</html>"))
        first, second = (c.args[0]["summary_html"] for c in post.call_args_list)
        self.assertGreater(len(first), 30000)
        self.assertLessEqual(len(second), 30000)
        self.assertIn("::warning::", out.getvalue())
        self.assertIn("Code.gs", out.getvalue())
        self.assertEqual(post.call_args_list[0].args[0]["html"], post.call_args_list[1].args[0]["html"])

    def test_a_refusal_that_is_not_about_the_size_is_not_retried(self):
        with mock.patch.object(agent, "sheet_post", side_effect=agent.AgentError("The Sheet web app refused the request: UNAUTHORIZED")) as post, redirect_stdout(io.StringIO()):
            self.assertFalse(report.send_email(make_data(), "<html></html>"))
        self.assertEqual(post.call_count, 1)

    def test_the_compact_body_is_also_refused_means_a_warning_and_no_crash(self):
        refuse = agent.AgentError("The Sheet web app refused the request: INVALID_PAYLOAD")
        with mock.patch.object(agent, "sheet_post", side_effect=[refuse, refuse]) as post, redirect_stdout(io.StringIO()) as out:
            self.assertFalse(report.send_email(make_data(), "<html></html>"))
        self.assertEqual(post.call_count, 2)
        self.assertIn("Report email not sent", out.getvalue())


class Launcher(unittest.TestCase):
    def test_the_windows_launcher_starts_the_ask_page_and_keeps_the_key_out_of_files(self):
        text = (Path(__file__).resolve().parent.parent / report.LAUNCHER).read_text(encoding="utf-8")
        self.assertIn("rag_server.py", text)
        self.assertIn("--open", text)
        self.assertIn("set /p", text.lower())                       # the key is typed at the prompt, in this window only
        self.assertIn("GROQ_API_KEY", text)
        self.assertNotRegex(text, r"gsk_[A-Za-z0-9]{10,}")           # no key in the file
        self.assertNotRegex(text, r"(?i)\bcurl\b|\bpowershell\b|\bwget\b|\bdel\b|\brmdir\b")
        self.assertIn("setlocal", text.lower())                     # the variable ends with the window, not in the user's settings
        self.assertNotIn("setx", text.lower())


if __name__ == "__main__":
    unittest.main()
