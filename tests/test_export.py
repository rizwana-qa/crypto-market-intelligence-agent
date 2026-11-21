"""Offline tests for export_data.py (the CSV export). No test calls the internet: Kraken's reply is a sample in the documented shape.

    python tests/test_export.py -v
"""

import csv
import math
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import export_data  # noqa: E402
import features  # noqa: E402

DAY = 86400
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
TODAY = int(NOW.timestamp()) // DAY * DAY
DONE_DAYS = 200                                     # completed days; one more row is today's unfinished candle


def kraken_rows(seed):
    """DONE_DAYS completed daily candles ending yesterday, plus today's unfinished one, in Kraken's shape:
    [time, open, high, low, close, vwap, volume, count], prices and volume as strings."""
    rows = []
    for k in range(DONE_DAYS + 1):
        stamp = TODAY - (DONE_DAYS - k) * DAY
        close = 100.0 + 10 * math.sin(k / 7.0 + seed) + 0.05 * k * seed
        if k == DONE_DAYS - 1:
            close *= 1.30                           # a jump on the last completed day, so a rank reaches the alert level
        rows.append([stamp, f"{close - 1:.1f}", f"{close + 2:.1f}", f"{close - 2:.1f}", f"{close:.1f}", f"{close:.2f}",
                     f"{1000 + 50 * math.cos(k / 3.0 + seed) + (900 if k == DONE_DAYS - 1 else 0):.4f}", 100 + k])
    return rows


ROWS = {pair: kraken_rows(seed) for pair, seed in (("XBTUSD", 1), ("ETHUSD", 2), ("XRPUSD", 3))}


def fake_fetch_rows(pair):
    return [list(row) for row in ROWS[pair]]


def read(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class Export(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "data_export"
        with mock.patch.object(agent, "fetch_rows", side_effect=fake_fetch_rows):
            self.result = export_data.export(self.out, NOW)
        self.candles = read(self.out / "kraken_candles.csv")
        self.factors = read(self.out / "daily_factors.csv")
        self.features = read(self.out / "market_features.csv")

    def test_four_files_with_the_documented_columns(self):
        for name in ("kraken_candles.csv", "daily_factors.csv", "market_features.csv", "columns_explained.csv"):
            self.assertTrue((self.out / name).exists(), name)
        self.assertEqual(list(self.candles[0].keys()), export_data.CANDLE_COLUMNS)
        self.assertEqual(list(self.factors[0].keys()), export_data.FACTOR_COLUMNS)
        self.assertEqual(list(self.features[0].keys()), features.NAMES)

    def test_row_counts_include_the_unfinished_day_in_candles_only(self):
        self.assertEqual(len(self.candles), 3 * (DONE_DAYS + 1))
        self.assertEqual(len(self.factors), 3 * DONE_DAYS)
        self.assertEqual(len(self.features), 3 * DONE_DAYS)
        self.assertEqual(self.result, {"candles": 3 * (DONE_DAYS + 1), "factors": 3 * DONE_DAYS, "features": 3 * DONE_DAYS, "dir": str(self.out)})

    def test_the_features_file_agrees_with_the_factors_file_on_every_shared_column(self):
        shared = {"coin": "coin", "date_utc": "date_utc", "close": "close", "volume": "volume", "ret_1d_pct": "daily_return_pct",
                  "volume_vs_30d_avg_x": "volume_vs_30d_avg_x", "vol_30d_pct": "volatility_30d_daily_pct",
                  "dist_sma30_pct": "distance_from_30d_avg_pct", "max_drawdown_30d_pct": "max_drawdown_30d_pct",
                  "rank_vol_30d": "rank_volatility", "rank_dist_30d": "rank_distance", "rank_drawdown_30d": "rank_drawdown",
                  "at_alert_rank": "at_alert_rank"}
        self.assertEqual(len(self.features), len(self.factors))
        for mine, theirs in zip(self.features, self.factors):
            for new, old in shared.items():
                if new in ("close", "volume", "coin", "date_utc", "at_alert_rank") or mine[new] == theirs[old] == "":
                    self.assertEqual(mine[new], theirs[old], (new, mine["coin"], mine["date_utc"]))
                else:
                    self.assertAlmostEqual(float(mine[new]), float(theirs[old]), delta=0.0002, msg=(new, mine["coin"], mine["date_utc"]))

    def test_all_eight_kraken_values_are_kept_exactly_as_received(self):
        first = [r for r in self.candles if r["coin"] == "BTC"][0]
        raw = ROWS["XBTUSD"][0]
        self.assertEqual(
            [first["time_unix"], first["open"], first["high"], first["low"], first["close"], first["vwap"], first["volume"], first["trades"]],
            [str(raw[0]), raw[1], raw[2], raw[3], raw[4], raw[5], raw[6], str(raw[7])])
        self.assertEqual(first["kraken_pair"], "XBTUSD")
        self.assertEqual(first["date_utc"], agent.format_date(raw[0]))

    def test_only_todays_candle_is_marked_unfinished(self):
        for coin in ("BTC", "ETH", "XRP"):
            rows = [r for r in self.candles if r["coin"] == coin]
            self.assertEqual([r["status"] for r in rows[:-1]], [export_data.COMPLETED] * DONE_DAYS)
            self.assertEqual(rows[-1]["status"], export_data.UNFINISHED)
            self.assertEqual(int(rows[-1]["time_unix"]), TODAY)

    def test_latest_factors_equal_what_the_agent_tells_the_ai(self):
        for coin, pair in agent.WATCHLIST.items():
            candles = agent.parse_candles(ROWS[pair], pair)
            stamps, closes = agent.completed_closes(candles, NOW)
            summary = agent.summarize(coin, stamps, closes)
            last = [r for r in self.factors if r["coin"] == coin][-1]
            self.assertEqual(last["date_utc"], summary["as_of"])
            self.assertAlmostEqual(float(last["close"]), summary["last_close"], places=4)
            self.assertAlmostEqual(float(last["daily_return_pct"]), summary["daily_return_pct"], delta=0.005)
            self.assertAlmostEqual(float(last["volatility_30d_daily_pct"]), summary["volatility_30d_daily_pct"], delta=0.005)
            self.assertAlmostEqual(float(last["distance_from_30d_avg_pct"]), summary["dist_from_30d_avg_pct"], delta=0.005)
            self.assertAlmostEqual(float(last["max_drawdown_30d_pct"]), summary["max_drawdown_30d_pct"], delta=0.005)
            self.assertAlmostEqual(float(last["rank_volatility"]), summary["ranks"]["volatility"], delta=0.05)
            self.assertAlmostEqual(float(last["rank_distance"]), summary["ranks"]["distance"], delta=0.05)
            self.assertAlmostEqual(float(last["rank_drawdown"]), summary["ranks"]["drawdown"], delta=0.05)
            self.assertEqual(last["at_alert_rank"] == "TRUE", bool(summary["breaches"]))

    def test_every_rank_column_holds_its_own_metric_on_days_where_the_ranks_differ(self):
        for coin, pair in agent.WATCHLIST.items():
            stamps, closes = agent.completed_closes(agent.parse_candles(ROWS[pair], pair), NOW)
            series, _ = agent.metric_series(closes)
            rows = [r for r in self.factors if r["coin"] == coin]
            checked = 0
            for i in range(agent.WINDOW + agent.MIN_HISTORY_DAYS - 1, len(closes), 7):
                ranks = agent.ranks_at(series, i)
                row = rows[i]
                self.assertEqual(row["date_utc"], agent.format_date(stamps[i]))
                self.assertAlmostEqual(float(row["rank_volatility"]), ranks["volatility"], delta=0.05)
                self.assertAlmostEqual(float(row["rank_distance"]), ranks["distance"], delta=0.05)
                self.assertAlmostEqual(float(row["rank_drawdown"]), ranks["drawdown"], delta=0.05)
                checked += len({round(v, 1) for v in ranks.values()}) > 1
            self.assertGreater(checked, 3, "the sample data must have days where the three ranks differ, or this test proves nothing")

    def test_close_and_volume_keep_the_digits_kraken_sent(self):
        rows = [r for r in self.factors if r["coin"] == "XRP"]
        candles = [r for r in self.candles if r["coin"] == "XRP" and r["status"] == export_data.COMPLETED]
        for factor, candle in zip(rows, candles):
            self.assertEqual(float(factor["close"]), float(candle["close"]))
            self.assertEqual(float(factor["volume"]), float(candle["volume"]))
        small = export_data.factor_rows("XRP", [[TODAY - (DONE_DAYS - k) * DAY, "0.9", "1", "0.8", "0.95904", "0.9", "44838828.05927659", 5]
                                                for k in range(DONE_DAYS + 1)], NOW)
        self.assertEqual(small[-1][2], "0.95904")                   # not 0.9590
        self.assertEqual(small[-1][3], "44838828.0593")             # 12 significant digits

    def test_the_jump_on_the_last_day_reaches_the_alert_rank(self):
        self.assertTrue(any(r["at_alert_rank"] == "TRUE" for r in self.factors if r["date_utc"] == agent.format_date(TODAY - DAY)))

    def test_alert_rank_days_match_the_base_rate_in_the_brief(self):
        for coin, pair in agent.WATCHLIST.items():
            stamps, closes = agent.completed_closes(agent.parse_candles(ROWS[pair], pair), NOW)
            expected = agent.base_rate(stamps, closes)
            rows = [r for r in self.factors if r["coin"] == coin and r["at_alert_rank"] != ""]
            self.assertEqual(len(rows), expected["ranked_days"])
            self.assertEqual(sum(r["at_alert_rank"] == "TRUE" for r in rows), expected["alert_days"])

    def test_ranks_start_on_the_same_day_as_the_agent_ranks(self):
        rows = [r for r in self.factors if r["coin"] == "BTC"]
        first = agent.WINDOW + agent.MIN_HISTORY_DAYS - 1
        self.assertEqual(rows[first - 1]["rank_volatility"], "")
        self.assertNotEqual(rows[first]["rank_volatility"], "")
        self.assertEqual(rows[0]["daily_return_pct"], "")           # no earlier close
        self.assertEqual(rows[0]["volatility_30d_daily_pct"], "")   # fewer than 30 returns

    def test_every_column_is_explained_and_nothing_else(self):
        explained = read(self.out / "columns_explained.csv")
        self.assertEqual(list(explained[0].keys()), export_data.DICTIONARY_COLUMNS)
        pairs = [(r["file"], r["column"]) for r in explained]
        self.assertEqual(len(pairs), len(set(pairs)))
        wanted = ({("kraken_candles.csv", c) for c in export_data.CANDLE_COLUMNS} | {("daily_factors.csv", c) for c in export_data.FACTOR_COLUMNS}
                  | {("market_features.csv", c) for c in features.NAMES})
        self.assertEqual(set(pairs), wanted)
        for row in explained:
            self.assertTrue(all(row[k].strip() for k in export_data.DICTIONARY_COLUMNS), row)

    def test_the_dictionary_says_the_agent_uses_time_close_and_volume_only_from_kraken(self):
        used = {r["column"]: r["used_by_agent"] for r in read(self.out / "columns_explained.csv") if r["file"] == "kraken_candles.csv"}
        self.assertEqual({c for c, v in used.items() if v == "yes"}, {"coin", "kraken_pair", "date_utc", "time_unix", "close", "volume", "status"})
        self.assertEqual({c for c, v in used.items() if v == "no"}, {"open", "high", "low", "vwap", "trades"})


class Failure(unittest.TestCase):
    def failing_fetch(self, pair):
        if pair == "XRPUSD":
            raise RuntimeError("XRPUSD: request to Kraken failed after 3 attempts (ConnectionError)")
        return fake_fetch_rows(pair)

    def test_nothing_is_written_when_one_coin_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "data_export"
            with mock.patch.object(agent, "fetch_rows", side_effect=self.failing_fetch):
                with self.assertRaises(RuntimeError):
                    export_data.export(out, NOW)
            self.assertFalse(out.exists())

    def test_a_bad_row_is_refused_with_the_agents_own_check(self):
        bad = {pair: fake_fetch_rows(pair) for pair in ROWS}
        bad["ETHUSD"][5][4] = "nan"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "data_export"
            with mock.patch.object(agent, "fetch_rows", side_effect=lambda pair: bad[pair]):
                with self.assertRaises(ValueError):
                    export_data.export(out, NOW)
            self.assertFalse(out.exists())

    def test_main_exits_with_1_and_says_nothing_was_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(agent, "fetch_rows", side_effect=self.failing_fetch), mock.patch("sys.stderr") as stderr:
                code = export_data.main(["--out", str(Path(tmp) / "x")])
            self.assertEqual(code, 1)
            self.assertIn("nothing written", "".join(str(c.args[0]) for c in stderr.write.call_args_list))

    def test_main_succeeds_and_reports_the_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(agent, "fetch_rows", side_effect=fake_fetch_rows):
                with mock.patch("export_data.datetime") as clock:
                    clock.now.return_value = NOW
                    code = export_data.main(["--out", str(Path(tmp) / "x")])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "x" / "kraken_candles.csv").exists())


class Refactor(unittest.TestCase):
    def test_fetch_candles_is_fetch_rows_plus_the_checks(self):
        with mock.patch.object(agent, "fetch_rows", side_effect=fake_fetch_rows):
            candles = agent.fetch_candles("XBTUSD")
        self.assertEqual(len(candles), DONE_DAYS + 1)
        self.assertEqual(set(candles[0]), {"timestamp", "close", "volume"})


if __name__ == "__main__":
    unittest.main()
