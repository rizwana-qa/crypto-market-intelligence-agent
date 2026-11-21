"""Offline tests for the free market-context sources (sources.py), the two new tools and the context_data tab.
No test calls the internet: every reply is a sample written to the shape in each provider's documentation.

    python tests/test_sources.py -v

Every expected number is worked out by hand in a comment next to the test.
"""

import json
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import scenarios  # noqa: E402
import sources  # noqa: E402

CODE_GS = (Path(__file__).resolve().parent.parent / "apps_script" / "Code.gs").read_text(encoding="utf-8")
DAY = 86400
NOW = 1_800_000_000.0                      # a fixed "now"; its UTC day is NOW_DAY
NOW_DAY = datetime.fromtimestamp(NOW, timezone.utc).date()
SECRET = "SECRET-DEMO-KEY-123"


def tab_width(name):
    block = re.search(r"const EXTRA_TABS = \{(.*?)\n\};", CODE_GS, re.S).group(1)
    row = re.search(name + r": \[(.*?)\]", block, re.S).group(1)
    return len(re.findall(r"'[^']*'", row))


def tab_headers(name):
    block = re.search(r"const EXTRA_TABS = \{(.*?)\n\};", CODE_GS, re.S).group(1)
    row = re.search(name + r": \[(.*?)\]", block, re.S).group(1)
    return re.findall(r"'([^']*)'", row)


def cell(row, header, tab="context_data"):
    """The value of a Sheet row under a column header taken from Code.gs, so a swapped column cannot pass."""
    return row[tab_headers(tab).index(header)]


def day_start(days_ago):
    """Unix seconds at 00:00 UTC, `days_ago` days before NOW's UTC day."""
    midnight = int(NOW // DAY) * DAY
    return midnight - days_ago * DAY


# ---- sample replies ------------------------------------------------------------------------------------------------------
def depth_body(mid=100.0, half_spread=0.05, levels=100, step=0.02, size=10.0, ask_levels=None):
    """Bids go down from mid - half_spread, asks up from mid + half_spread, `step` apart, each `size` coins."""
    bids = [[f"{mid - half_spread - i * step:.4f}", f"{size}", 1700000000] for i in range(levels)]
    asks = [[f"{mid + half_spread + i * step:.4f}", f"{size}", 1700000000] for i in range(ask_levels or levels)]
    return {"error": [], "result": {"XBTUSD": {"bids": bids, "asks": asks}}}


def ticker(symbol, pair, mark=100.0, oi=500.0, funding=None, relative=None, tag="perpetual", suspended=False):
    t = {"symbol": symbol, "pair": pair, "tag": tag, "markPrice": mark, "openInterest": oi, "suspended": suspended}
    if funding is not None:
        t["fundingRate"] = funding
    if relative is not None:
        t["relativeFundingRate"] = relative
    return t


def futures_body(*tickers):
    return {"result": "success", "tickers": list(tickers)}


def dvol_body(days=120, last=55.0, include_today=True):
    """`days` completed daily candles with closes 40, 41, 42, ... then (optionally) today's candle with close `last`."""
    candles = [[(day_start(days - i)) * 1000, 0, 0, 0, 40.0 + i] for i in range(days)]
    if include_today:
        candles.append([day_start(0) * 1000, 0, 0, 0, last])
    return {"result": {"data": candles, "continuation": None}}


def volume_body(days=200, today=True, flat=1000.0, last=None):
    """Constant volume, except an optional last completed day. Today's point (a partial day) is added when `today`."""
    points = []
    for i in range(days):
        volume = flat if (last is None or i < days - 1) else last
        points.append([day_start(days - i) * 1000, volume])
    if today:
        points.append([int(NOW * 1000), 5.0])    # a partial day: must be ignored
    return {"prices": [], "total_volumes": points}


def markets_body():
    return [{"id": "bitcoin", "current_price": 67000.0, "total_volume": 3.1e10, "market_cap": 1.3e12, "market_cap_rank": 1,
             "price_change_percentage_24h": 0.456},
            {"id": "ethereum", "current_price": 3100.0, "total_volume": 1.4e10, "market_cap": 3.7e11, "market_cap_rank": 2,
             "price_change_percentage_24h": -0.3},
            {"id": "ripple", "current_price": 2.3, "total_volume": 3e9, "market_cap": 1.4e11, "market_cap_rank": 5,
             "price_change_percentage_24h": 0.9}]


def fng_body(values=(52,) + (50,) * 30, label="Neutral"):
    return {"data": [{"value": str(v), "value_classification": label if i == 0 else "x", "timestamp": "1"} for i, v in enumerate(values)]}


class FakeResponse:
    def __init__(self, status=200, payload=None, json_error=False):
        self.status_code, self.payload, self.json_error = status, payload, json_error

    def json(self):
        if self.json_error:
            raise ValueError("not json")
        return self.payload


# ---- order book --------------------------------------------------------------------------------------------------------
class OrderBook(unittest.TestCase):
    def test_spread_and_depth_are_worked_out_by_hand(self):
        # best bid 99.95, best ask 100.05, mid 100.00, spread 0.10 / 100 = 0.1000 %
        # band = 99.00 to 101.00. Bid levels at 99.95 - 0.02 i >= 99.00: i <= 47.5, so i = 0..47 = 48 levels of 10 coins
        # price sum for i = 0..47 of (99.95 - 0.02 i) = 48 * 99.95 - 0.02 * (47 * 48 / 2) = 4797.6 - 22.56 = 4775.04; times 10 = 47750.4
        # ask levels at 100.05 + 0.02 i <= 101.00: i <= 47.5, so 48 levels: 48 * 100.05 + 22.56 = 4802.4 + 22.56 = 4824.96; times 10 = 48249.6
        # both sides reach 2.03 %, so the band is the full 1 %
        result = sources.parse_depth(depth_body())
        self.assertEqual(result["mid_price"], 100.0)
        self.assertEqual(result["spread_pct"], 0.1)
        self.assertEqual(result["bid_depth_usd"], 47750.4)
        self.assertEqual(result["ask_depth_usd"], 48249.6)
        self.assertEqual(result["depth_band_pct"], 1.0)

    def test_a_book_that_does_not_reach_1_percent_uses_the_range_it_covers_and_says_so(self):
        # 5 levels each side: bids 99.95, 99.93, 99.91, 99.89, 99.87 and asks 100.05 ... 100.13, so each side reaches 0.13 % from the mid
        # bid depth = (99.95 + 99.93 + 99.91 + 99.89 + 99.87) * 10 = 4995.5 ; ask depth = (100.05 + 100.07 + 100.09 + 100.11 + 100.13) * 10 = 5004.5
        result = sources.parse_depth(depth_body(levels=5))
        self.assertEqual(result["depth_band_pct"], 0.13)
        self.assertEqual(result["bid_depth_usd"], 4995.5)
        self.assertEqual(result["ask_depth_usd"], 5004.5)

    def test_bid_and_ask_depth_always_use_the_same_band(self):
        # bids reach 2.03 % but asks only 0.13 %: the band is 0.13 % for both, so the bid at 99.85 (0.15 % away) is left out
        result = sources.parse_depth(depth_body(levels=100, ask_levels=5))
        self.assertEqual(result["depth_band_pct"], 0.13)
        self.assertEqual(result["bid_depth_usd"], 4995.5)
        self.assertEqual(result["ask_depth_usd"], 5004.5)

    def test_bad_books_are_refused(self):
        for body in ({"error": ["EQuery:Unknown asset pair"], "result": {}}, {}, [], {"error": [], "result": {}},
                     {"error": [], "result": {"A": {"bids": [], "asks": []}}}):
            with self.assertRaises(sources.SourceError, msg=str(body)):
                sources.parse_depth(body)
        crossed = {"error": [], "result": {"A": {"bids": [["101", "1", 0]], "asks": [["100", "1", 0]]}}}
        with self.assertRaises(sources.SourceError):
            sources.parse_depth(crossed)

    def test_a_text_price_is_read_and_a_garbage_one_is_not(self):
        garbage = {"error": [], "result": {"A": {"bids": [["x", "1", 0]], "asks": [["100", "1", 0]]}}}
        with self.assertRaises(ValueError):
            sources.parse_depth(garbage)
        self.assertFalse(sources.is_data(sources.safe(sources.parse_depth, garbage)))      # safe() turns it into "unavailable"


# ---- futures ---------------------------------------------------------------------------------------------------------
class Futures(unittest.TestCase):
    def test_linear_contract_uses_the_absolute_funding_rate_over_the_mark_price(self):
        # fundingRate 0.0005 per hour on a mark price of 100 -> 0.0005 / 100 * 100 = 0.0005 % ; open interest 500 coins * 100 = 50000
        body = futures_body(ticker("PF_ETHUSD", "ETH:USD", mark=100.0, oi=500.0, funding=0.0005))
        result = sources.parse_futures(body, "ETH")
        self.assertEqual(result["funding_rate_pct_per_hour"], 0.0005)
        self.assertEqual(result["funding_pays"], "longs pay shorts")
        self.assertEqual(result["open_interest_usd_approx"], 50000.0)

    def test_the_relative_funding_rate_is_used_when_given_and_negative_means_shorts_pay(self):
        body = futures_body(ticker("PF_XRPUSD", "XRP:USD", relative=-0.00002, funding=0.5))
        result = sources.parse_futures(body, "XRP")
        self.assertEqual(result["funding_rate_pct_per_hour"], -0.002)     # -0.00002 * 100
        self.assertEqual(result["funding_pays"], "shorts pay longs")

    def test_bitcoin_matches_xbt_and_a_pf_contract_is_preferred(self):
        body = futures_body(ticker("PI_XBTUSD", "XBT:USD", relative=0.00001), ticker("PF_XBTUSD", "XBT:USD", relative=0.00002, mark=70000.0, oi=10.0))
        result = sources.parse_futures(body, "BTC")
        self.assertEqual(result["symbol"], "PF_XBTUSD")
        self.assertEqual(result["open_interest_usd_approx"], 700000.0)

    def test_an_inverse_contract_gets_no_dollar_open_interest(self):
        body = futures_body(ticker("PI_XBTUSD", "XBT:USD", relative=0.00001, oi=5_000_000.0))
        result = sources.parse_futures(body, "BTC")
        self.assertIsNone(result["open_interest_usd_approx"])
        self.assertEqual(result["open_interest"], 5_000_000.0)

    def test_unclear_or_implausible_funding_is_unavailable_and_never_guessed(self):
        with self.assertRaises(sources.SourceError):                      # inverse contract with only an absolute rate: unit unclear
            sources.parse_futures(futures_body(ticker("PI_XBTUSD", "XBT:USD", funding=0.001)), "BTC")
        with self.assertRaises(sources.SourceError):                      # 5 % per hour is not plausible
            sources.parse_futures(futures_body(ticker("PF_ETHUSD", "ETH:USD", relative=0.05)), "ETH")

    def test_no_perpetual_suspended_or_wrong_reply(self):
        for body in (futures_body(ticker("FI_ETHUSD_261225", "ETH:USD", tag="month", relative=0.0)),
                     futures_body(ticker("PF_ETHUSD", "ETH:USD", relative=0.0, suspended=True)),
                     futures_body(), {"result": "error"}, [], {"result": "success", "tickers": "x"}):
            with self.assertRaises(sources.SourceError, msg=str(body)):
                sources.parse_futures(body, "ETH")


# ---- implied volatility --------------------------------------------------------------------------------------------------
class ImpliedVolatility(unittest.TestCase):
    def test_rank_uses_the_completed_days_and_the_latest_value(self):
        # completed closes 40..159 (120 days). latest = today's candle 55.0. below 55: closes 40..54 = 15, equal: the close 55 = 1
        # rank = 100 * (15 + 0.5 * 1) / 120 = 12.9167 -> 12.9
        result = sources.parse_dvol(dvol_body(), NOW)
        self.assertEqual(result["implied_vol_pct"], 55.0)
        self.assertEqual(result["history_days"], 120)
        self.assertEqual(result["rank_1y"], 12.9)

    def test_under_90_days_gives_a_value_but_no_rank(self):
        result = sources.parse_dvol(dvol_body(days=60), NOW)
        self.assertIsNone(result["rank_1y"])
        self.assertEqual(result["implied_vol_pct"], 55.0)

    def test_today_is_not_part_of_the_history_it_is_ranked_against(self):
        result = sources.parse_dvol(dvol_body(days=100, last=1000.0), NOW)
        self.assertEqual(result["history_days"], 100)
        self.assertEqual(result["rank_1y"], 100.0)

    def test_bad_replies(self):
        for body in ({}, {"result": {"data": []}}, {"result": {}}, []):
            with self.assertRaises(sources.SourceError):
                sources.parse_dvol(body, NOW)


# ---- CoinGecko -----------------------------------------------------------------------------------------------------------
class CoinGecko(unittest.TestCase):
    def test_markets_are_read_by_name(self):
        result = sources.parse_markets(markets_body())
        self.assertEqual(result["bitcoin"]["price_usd"], 67000.0)
        self.assertEqual(result["bitcoin"]["market_cap_rank"], 1)
        self.assertEqual(result["bitcoin"]["change_24h_pct"], 0.46)
        self.assertEqual(set(result), {"bitcoin", "ethereum", "ripple"})

    def test_bad_markets_replies(self):
        for body in ({}, [], [{"nothing": 1}], {"error": "rate limit"}):
            with self.assertRaises(sources.SourceError):
                sources.parse_markets(body)

    def test_flat_volume_gives_a_ratio_of_one_and_a_middle_rank_and_drops_the_partial_day(self):
        # 200 completed days of 1000. ratio = 1000 / mean(previous 30 = 1000) = 1 for days 30..199 -> 170 ratios, all equal
        # rank of a value equal to all of them = 100 * (0 + 0.5 * 170) / 170 = 50. The partial point of 5.0 would give a ratio of 0.005 if it were kept.
        result = sources.parse_volume_history(volume_body(), NOW)
        self.assertEqual(result["market_volume_ratio_30d"], 1.0)
        self.assertEqual(result["market_volume_rank"], 50.0)
        self.assertEqual(result["history_days"], 200)
        self.assertEqual(result["as_of"], datetime.fromtimestamp(day_start(1), timezone.utc).date().isoformat())

    def test_a_volume_surge_ranks_at_the_top(self):
        result = sources.parse_volume_history(volume_body(last=5000.0), NOW)
        self.assertEqual(result["market_volume_ratio_30d"], 5.0)
        # 170 ratios: 169 equal to 1.0 and one at 5.0: below = 169 -> rank 100 * 169 / 170 + 0.5 / 170 * 100 = 99.41 + 0.29 = 99.7
        self.assertEqual(result["market_volume_rank"], 99.7)

    def test_too_little_volume_history_is_an_error_and_not_a_rank(self):
        with self.assertRaises(sources.SourceError):
            sources.parse_volume_history(volume_body(days=100), NOW)             # 70 ratios < 90
        with self.assertRaises(sources.SourceError):
            sources.parse_volume_history({"total_volumes": []}, NOW)

    def test_the_demo_key_goes_in_a_header_and_never_in_the_url(self):
        calls = []
        with mock.patch.object(sources, "get_json", side_effect=lambda url, params=None, headers=None, attempts=2: calls.append((url, params, headers)) or markets_body()):
            sources.fetch_markets(SECRET, ["BTC", "ETH", "XRP"])
        url, params, headers = calls[0]
        self.assertEqual(headers["x-cg-demo-api-key"], SECRET)
        self.assertNotIn(SECRET, url + json.dumps(params))
        self.assertEqual(params["ids"], "bitcoin,ethereum,ripple")


# ---- fear and greed ------------------------------------------------------------------------------------------------------
class FearAndGreed(unittest.TestCase):
    def test_value_label_change_and_mean(self):
        # today 52, yesterday 50 -> change +2; the prior 30 values are all 50 -> mean 50.0
        result = sources.parse_fear_greed(fng_body())
        self.assertEqual(result, {"value": 52, "label": "Neutral", "change_vs_yesterday": 2, "mean_prior_30d": 50.0})

    def test_bad_replies(self):
        for body in ({}, {"data": []}, {"data": [{"value": "5"}]}, fng_body(values=(150, 50))):
            with self.assertRaises((sources.SourceError, ValueError, KeyError)):
                sources.parse_fear_greed(body)


# ---- price check and flags ---------------------------------------------------------------------------------------------
class PriceCheckAndFlags(unittest.TestCase):
    def test_the_tolerance_is_one_percent_and_inclusive(self):
        self.assertTrue(sources.price_check(101.0, 100.0)["agree"])          # exactly +1.000 %
        self.assertFalse(sources.price_check(101.01, 100.0)["agree"])
        self.assertEqual(sources.price_check(97.0, 100.0)["difference_pct"], -3.0)

    def build(self, **overrides):
        coins = ["BTC", "ETH", "XRP"]
        depth = {c: sources.parse_depth(depth_body(mid=67000.0 if c == "BTC" else 100.0)) for c in coins}
        futures = {c: sources.unavailable("x") for c in coins}
        dvol = {"BTC": sources.parse_dvol(dvol_body(days=120, last=500.0), NOW), "ETH": sources.parse_dvol(dvol_body(days=120, last=50.0), NOW),
                "XRP": sources.unavailable("none")}
        markets = sources.parse_markets(markets_body())
        volume = {c: sources.parse_volume_history(volume_body(last=5000.0), NOW) for c in coins}
        fng = sources.parse_fear_greed(fng_body(values=(10,) + (50,) * 30, label="Extreme Fear"))
        args = dict(coins=coins, depth=depth, futures=futures, dvol=dvol, markets=markets, volume=volume, fear_greed=fng, as_of="t")
        args.update(overrides)
        return sources.build_context(**args)

    def test_each_flag_is_raised_by_its_own_condition(self):
        flags = self.build()["flags"]
        self.assertIn("implied volatility BTC", flags)              # 500 is far above every past close -> rank 100
        self.assertNotIn("implied volatility ETH", flags)           # 50 is mid-range (rank 8.3)
        self.assertIn("market volume BTC", flags)
        self.assertIn("price sources XRP disagree", flags)          # Kraken mid 100.0 vs CoinGecko 2.3
        self.assertIn("sentiment extreme fear", flags)

    def test_unavailable_pieces_are_never_flagged_and_never_estimated(self):
        gone = sources.unavailable("down")
        ctx = self.build(dvol={c: gone for c in ("BTC", "ETH", "XRP")}, markets=gone, volume={}, fear_greed=gone)
        self.assertEqual(ctx["flags"], [])
        self.assertEqual(ctx["coins"]["BTC"]["implied_volatility"], gone)
        self.assertEqual(ctx["coins"]["BTC"]["market_wide"], gone)
        self.assertIn("unavailable", ctx["coins"]["BTC"]["price_check"])

    def test_funding_and_liquidity_never_raise_flags(self):
        extreme = {c: sources.parse_futures(futures_body(ticker("PF_ETHUSD", "ETH:USD", relative=0.005, oi=1e9)), "ETH") for c in ("BTC", "ETH", "XRP")}
        ctx = self.build(futures=extreme)
        self.assertFalse([f for f in ctx["flags"] if "funding" in f or "open interest" in f or "liquidity" in f])


# ---- fetching: fail-soft, retries, no secret in any message -----------------------------------------------------------------------
class Fetching(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sources.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_error_never_contains_the_url_or_the_key(self):
        boom = sources.requests.exceptions.ConnectionError(f"failed https://example.com/x?key={SECRET}")
        with mock.patch.object(sources.requests, "get", side_effect=boom):
            with self.assertRaises(sources.SourceError) as caught:
                sources.get_json("https://example.com/x", {"key": SECRET}, {"x-cg-demo-api-key": SECRET})
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn("example.com", str(caught.exception))
        self.assertEqual(str(caught.exception), "ConnectionError")

    def test_429_is_retried_once_and_then_succeeds(self):
        replies = [FakeResponse(429), FakeResponse(200, {"ok": 1})]
        with mock.patch.object(sources.requests, "get", side_effect=replies) as get:
            self.assertEqual(sources.get_json("https://x"), {"ok": 1})
        self.assertEqual(get.call_count, 2)

    def test_a_client_error_is_not_retried(self):
        with mock.patch.object(sources.requests, "get", return_value=FakeResponse(401)) as get:
            with self.assertRaises(sources.SourceError) as caught:
                sources.get_json("https://x")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(str(caught.exception), "HTTP 401")

    def test_a_reply_that_is_not_json_is_an_error(self):
        with mock.patch.object(sources.requests, "get", return_value=FakeResponse(200, json_error=True)):
            with self.assertRaises(sources.SourceError):
                sources.get_json("https://x")

    def test_every_source_down_still_returns_a_context_and_never_raises(self):
        with mock.patch.object(sources.requests, "get", side_effect=sources.requests.exceptions.Timeout("slow")):
            ctx = sources.fetch_context(agent.WATCHLIST, NOW, SECRET)
        self.assertEqual(ctx["flags"], [])
        for coin in agent.WATCHLIST:
            for name in ("liquidity", "derivatives", "implied_volatility", "market_wide", "price_check"):
                self.assertIn("unavailable", ctx["coins"][coin][name], msg=f"{coin} {name}")
        self.assertIn("unavailable", ctx["sentiment"])
        self.assertNotIn(SECRET, json.dumps(ctx))

    def test_without_the_key_coingecko_says_not_configured_and_makes_no_coingecko_call(self):
        urls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            urls.append(url)
            raise sources.requests.exceptions.ConnectionError("down")

        with mock.patch.object(sources.requests, "get", side_effect=fake_get):
            ctx = sources.fetch_context(agent.WATCHLIST, NOW, "")
        self.assertFalse([u for u in urls if "coingecko" in u])
        self.assertIn("not configured", ctx["coins"]["BTC"]["market_wide"]["unavailable"])

    def test_one_bad_source_does_not_hide_the_others(self):
        def fake_get(url, params=None, headers=None, timeout=None):
            if "kraken.com/0/public/Depth" in url:
                return FakeResponse(200, depth_body(mid=100.0))
            if "alternative.me" in url:
                return FakeResponse(200, fng_body())
            raise sources.requests.exceptions.ConnectionError("down")

        with mock.patch.object(sources.requests, "get", side_effect=fake_get):
            ctx = sources.fetch_context(agent.WATCHLIST, NOW, "")
        self.assertEqual(ctx["coins"]["BTC"]["liquidity"]["mid_price"], 100.0)
        self.assertEqual(ctx["sentiment"]["value"], 52)
        self.assertIn("unavailable", ctx["coins"]["BTC"]["derivatives"])

    def test_the_agent_tool_never_raises(self):
        with mock.patch.object(sources, "fetch_context", side_effect=RuntimeError("bug")):
            self.assertEqual(agent.tool_market_context(), {"error": "market context failed (RuntimeError)"})

    def test_the_data_check_prints_no_key_and_counts_failures(self):
        with mock.patch.object(sources.requests, "get", side_effect=sources.requests.exceptions.Timeout("slow")):
            lines, failed = sources.check_sources(agent.WATCHLIST, SECRET)
        self.assertGreater(failed, 0)
        self.assertNotIn(SECRET, "\n".join(lines))
        self.assertTrue(any(line.startswith("SKIP Deribit DVOL XRP") for line in lines))
        with mock.patch.object(sources.requests, "get", side_effect=sources.requests.exceptions.Timeout("slow")):
            lines, _ = sources.check_sources(agent.WATCHLIST, "")
        self.assertTrue(any("COINGECKO_API_KEY is not set" in line for line in lines))


# ---- the same numbers as the agent's own code ---------------------------------------------------------------------------------------------
class SameMathAsTheAgent(unittest.TestCase):
    def test_percentile_rank_matches(self):
        values = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]
        for latest in (0.5, 1.0, 4.0, 9.0, 10.0):
            self.assertEqual(sources.percentile_rank(values, latest), agent.percentile_rank(values, latest))

    def test_volume_ratio_series_matches(self):
        volumes = [10.0 + (i * 7) % 13 for i in range(80)]
        self.assertEqual(sources.volume_ratio_series(volumes), agent.volume_ratio_series(volumes))


# ---- the fakes have the real shape ---------------------------------------------------------------------------------------------------------
class FakesMatchReality(unittest.TestCase):
    def real(self):
        return PriceCheckAndFlags().build()

    def test_fake_and_real_context_have_the_same_structure(self):
        real, fake = self.real(), scenarios.fake_context()
        self.assertEqual(set(real), set(fake))
        for coin in agent.WATCHLIST:
            self.assertEqual(set(real["coins"][coin]), set(fake["coins"][coin]))
            for name in ("liquidity", "price_check"):
                self.assertEqual(set(real["coins"][coin][name]), set(fake["coins"][coin][name]), msg=name)
        self.assertEqual(set(real["coins"]["BTC"]["implied_volatility"]), set(fake["coins"]["BTC"]["implied_volatility"]))
        self.assertEqual(set(real["coins"]["BTC"]["market_wide"]), set(fake["coins"]["BTC"]["market_wide"]))
        self.assertEqual(set(real["sentiment"]), set(fake["sentiment"]))
        real_futures = sources.parse_futures(futures_body(ticker("PF_ETHUSD", "ETH:USD", relative=0.00001)), "ETH")
        self.assertEqual(set(real_futures), set(fake["coins"]["ETH"]["derivatives"]))

    def test_the_fake_flags_follow_the_real_rules(self):
        self.assertEqual(scenarios.fake_context()["flags"], [])
        self.assertEqual(scenarios.fake_context(btc_price_gap=3.2)["flags"], ["price sources BTC disagree"])
        self.assertEqual(scenarios.fake_context(fear_greed=(14, "Extreme Fear"))["flags"], ["sentiment extreme fear"])
        self.assertEqual(scenarios.fake_context(iv_rank=97.0, volume_rank=96.0)["flags"], ["implied volatility BTC", "market volume BTC", "market volume ETH", "market volume XRP"])


# ---- alert history stats tool ---------------------------------------------------------------------------------------------------------------------
class AlertHistoryStats(unittest.TestCase):
    def load(self, calm_tail=True):
        """A coin whose price jumps once, so exactly one stretch of days reaches the alert rank."""
        n = agent.WINDOW + agent.MIN_HISTORY_DAYS + 120
        closes = [100.0 + (i % 3) * 0.5 for i in range(n)]
        closes[-40] = 160.0                       # one large spike, then back to the old range
        stamps = [DAY * (20000 + i) for i in range(n)]
        return stamps, closes, [1.0] * n

    def test_counts_match_the_report_base_rate_and_the_union(self):
        stamps, closes, volumes = self.load()
        with mock.patch.object(agent, "load_coin", return_value=(stamps, closes, volumes)):
            result = agent.tool_alert_history_stats()
        expected = agent.base_rate(stamps, closes)
        for coin in agent.WATCHLIST:
            piece = result["coins"][coin]
            self.assertEqual(piece["ranked_days"], expected["ranked_days"])
            self.assertEqual(piece["alert_days"], expected["alert_days"])
            self.assertEqual(piece["episodes"], expected["episodes"])
            self.assertEqual(piece["share_pct"], round(100.0 * expected["alert_days"] / expected["ranked_days"], 1))
        self.assertGreater(expected["alert_days"], 0)
        # the three coins are identical, so the union equals one coin
        self.assertEqual(result["any_coin"]["episodes"], expected["episodes"])
        self.assertEqual(result["any_coin"]["ranked_days"], expected["ranked_days"])

    def test_the_last_alert_day_is_the_latest_flagged_day(self):
        stamps, closes, volumes = self.load()
        expected = agent.base_rate(stamps, closes)
        last = [d for d, f in zip(expected["days"], expected["flags"]) if f][-1]
        with mock.patch.object(agent, "load_coin", return_value=(stamps, closes, volumes)):
            self.assertEqual(agent.tool_alert_history_stats()["coins"]["BTC"]["last_alert_day"], last)

    def test_a_coin_that_fails_is_listed_as_unavailable_and_the_others_still_count(self):
        stamps, closes, volumes = self.load()

        def load(coin, now):
            if coin == "XRP":
                raise RuntimeError("Kraken is down")
            return stamps, closes, volumes

        with mock.patch.object(agent, "load_coin", side_effect=load):
            result = agent.tool_alert_history_stats()
        self.assertEqual(set(result["coins"]), {"BTC", "ETH"})
        self.assertIn("XRP", result["unavailable"])

    def test_every_coin_failing_is_an_error_object(self):
        with mock.patch.object(agent, "load_coin", side_effect=RuntimeError("down")):
            self.assertIn("error", agent.tool_alert_history_stats())

    def test_too_short_a_history_is_unavailable_and_not_a_zero(self):
        short = ([DAY * (20000 + i) for i in range(60)], [100.0 + i % 4 for i in range(60)], [1.0] * 60)
        with mock.patch.object(agent, "load_coin", return_value=short):
            result = agent.tool_alert_history_stats()
        self.assertIn("error", result)


# ---- Sheet rows ----------------------------------------------------------------------------------------------------------------------------------------------
class ContextRows(unittest.TestCase):
    def evidence(self, context=None, stats=None):
        items = []
        if context is not False:
            items.append({"tool": "get_market_context", "args": {}, "result": context or scenarios.fake_context(btc_price_gap=3.2)})
        if stats is not False:
            items.append({"tool": "get_alert_history_stats", "args": {}, "result": stats or scenarios.fake_alert_stats()})
        return items

    def test_rows_have_the_width_of_the_context_data_tab(self):
        rows = agent.context_rows(self.evidence(), "Agent loop", "2026-11-20T05:17:00Z", "1")
        self.assertEqual([r[3] for r in rows], ["BTC", "ETH", "XRP"])
        for row in rows:
            self.assertEqual(len(row), tab_width("context_data"))
        self.assertEqual(tab_width("context_data"), 25)

    def test_values_land_under_the_right_headers(self):
        row = agent.context_rows(self.evidence(), "Agent loop", "t", "1")[0]
        expected = {"Coin": "BTC", "Kraken mid (USD)": 67210.5, "Spread %": 0.0012, "Bid depth in band (USD)": 14.2e6,
                    "Ask depth in band (USD)": 13.8e6, "Depth band % of price": 1.0, "Funding rate % per hour": 0.004,
                    "Funding pays": "longs pay shorts", "Open interest (USD, approx)": 9.0e8, "Implied vol % (annualised)": 46.8,
                    "Implied vol rank (1y)": 48.0, "Market volume 24h (USD)": 31.5e9, "Market volume vs 30d avg (x)": 1.04,
                    "Market volume rank": 44.0, "CoinGecko price (USD)": 65126.45, "Price difference %": 3.2, "Fear and greed": 52,
                    "Fear and greed label": "Neutral", "Alert-rank days %": 8.4, "Alert episodes": 19,
                    "Context flags": "price sources BTC disagree", "Status": "ok"}
        for header, value in expected.items():
            self.assertEqual(cell(row, header), value, msg=header)
        self.assertEqual(set(expected) | {"Time (UTC)", "Run ID", "Version"}, set(tab_headers("context_data")))   # every column is checked

    def test_unavailable_pieces_are_blank_cells_and_the_status_names_them(self):
        row = agent.context_rows(self.evidence(context=scenarios.fake_context(unavailable_all=True), stats={"error": "x"}), "v", "t", "1")[2]
        self.assertEqual(cell(row, "Kraken mid (USD)"), "")
        self.assertEqual(cell(row, "Funding rate % per hour"), "")
        self.assertTrue(cell(row, "Status").startswith("partial: liquidity, derivatives, implied_volatility, market_wide"))
        self.assertEqual(cell(row, "Alert-rank days %"), "")

    def test_xrp_has_no_implied_volatility_and_the_status_says_so(self):
        row = agent.context_rows(self.evidence(), "v", "t", "1")[2]
        self.assertEqual(cell(row, "Implied vol % (annualised)"), "")
        self.assertEqual(cell(row, "Status"), "partial: implied_volatility")

    def test_no_rows_when_the_context_tool_failed_or_was_not_called(self):
        self.assertEqual(agent.context_rows([{"tool": "get_market_context", "args": {}, "result": {"error": "x"}}], "v", "t", "1"), [])
        self.assertEqual(agent.context_rows([], "v", "t", "1"), [])


# ---- context never alerts alone ---------------------------------------------------------------------------------------------------------------------
class ContextNeverAlertsAlone(unittest.TestCase):
    def evidence(self):
        items = [{"tool": "get_market_summary", "args": {"coin": c}, "result": r} for c, r in scenarios.calm().items()]
        items.append({"tool": "get_market_context", "args": {}, "result": scenarios.fake_context(
            iv_rank=99.0, volume_rank=99.0, btc_price_gap=5.0, fear_greed=(9, "Extreme Fear"), funding=0.09)})
        return items

    def test_an_alert_that_rests_only_on_context_is_blocked(self):
        draft = {"alert": True, "coins": ["BTC"], "message": "BTC implied volatility rank is 99.0 and fear and greed is 9.",
                 "reasoning": "r", "sources": ["Implied volatility"]}
        titles = [s["title"] for s in agent.load_playbook()]
        final = agent.apply_guardrails(draft, titles, self.evidence())
        self.assertFalse(final["alert"])
        self.assertIn("context never alerts alone", final["blocked_reason"])

    def test_an_error_in_the_context_tool_does_not_stop_a_justified_alert(self):
        evidence = [{"tool": "get_market_summary", "args": {"coin": "BTC"}, "result": scenarios.btc_spike(scenarios.calm())["BTC"]},
                    {"tool": "get_market_context", "args": {}, "result": {"error": "down"}}]
        draft = {"alert": True, "coins": ["BTC"], "message": "BTC 30-day volatility rank is 99.3.", "reasoning": "r", "sources": ["Volatility rule"]}
        titles = [s["title"] for s in agent.load_playbook()]
        self.assertTrue(agent.apply_guardrails(draft, titles, evidence)["alert"])


# ---- tools, prompts and the playbook ---------------------------------------------------------------------------------------------------------------------
class Wiring(unittest.TestCase):
    def test_both_tools_take_no_arguments_and_are_registered_everywhere(self):
        for name in ("get_market_context", "get_alert_history_stats"):
            self.assertIn(name, agent.TOOLS)
            self.assertEqual(agent.TOOL_SPECS[name]["params"], {})
            self.assertIn(name, agent.SYSTEM_PROMPT)
            self.assertIn(name, agent.PLAN_PROMPT)

    def test_the_agent_loop_has_room_for_one_call_per_tool(self):
        # market x3, cross, context, stats, history, playbook, then the answer = 9 AI calls if the model calls one tool at a time
        self.assertGreaterEqual(agent.MAX_STEPS, 9)

    def test_the_playbook_search_finds_every_new_section(self):
        wanted = {"funding rate open interest": "Derivatives (funding rate and open interest)",
                  "order book spread depth": "Liquidity (order book)",
                  "implied volatility DVOL": "Implied volatility",
                  "market-wide volume CoinGecko": "Market-wide volume",
                  "fear and greed sentiment": "Sentiment index",
                  "price sources disagree": "Price source check",
                  "how often base rate alert days": "Alert history base rate"}
        for query, title in wanted.items():
            titles = [s["title"] for s in agent.tool_search_playbook(query)["sections"]]
            self.assertEqual(titles[0], title, msg=query)

    def test_the_playbook_says_context_never_alerts_and_unavailable_is_never_estimated(self):
        text = (Path(__file__).resolve().parent.parent / "playbook.md").read_text(encoding="utf-8")
        rule6 = re.search(r"6\. (.*)", text).group(1)
        for phrase in ("get_market_context", "never raise an alert on their own", "never estimated"):
            self.assertIn(phrase, rule6)

    def test_only_free_sources_are_used(self):
        for url in (sources.KRAKEN_DEPTH_URL, sources.KRAKEN_FUTURES_URL, sources.DERIBIT_URL, sources.COINGECKO_URL, sources.FEAR_GREED_URL):
            self.assertTrue(url.startswith("https://"))
        self.assertEqual(sources.coingecko_headers("k")["x-cg-demo-api-key"], "k")            # the free Demo key header, not the paid pro one
        self.assertNotIn("pro-api", sources.COINGECKO_URL)


if __name__ == "__main__":
    unittest.main()
