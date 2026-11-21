"""Offline tests for features.py (the market features written to market_features.csv). No internet, no AI.

Every formula is checked against a number worked out by hand (or by plain arithmetic in the test), not against the code under test.
The same formulas were also compared once with pandas, numpy and scipy (133 comparisons, largest relative difference 3e-13);
that check lives outside the repository because the project uses the standard library only.

    python tests/test_features.py -v
"""

import math
import statistics
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import features  # noqa: E402

DAY = 86400
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
TODAY = int(NOW.timestamp()) // DAY * DAY


def rows_from(days, make):
    """Kraken-shaped rows for `days` completed days ending yesterday, plus today's unfinished candle.
    make(i) returns (open, high, low, close, vwap, volume, trades) for day i."""
    rows = []
    for i in range(days + 1):
        o, h, l, c, w, v, t = make(i)
        rows.append([TODAY - (days - i) * DAY, str(o), str(h), str(l), str(c), str(w), str(v), t])
    return rows


def linear(i):
    """Close 100 + i, a day range of 2.5 (high +1, low -1.5), open -0.5, vwap -0.25: every result has a closed form."""
    c = 100 + i
    return c - 0.5, c + 1, c - 1.5, c, c - 0.25, 10 + i % 7, 100 + i


LINEAR_DAYS = 250
LINEAR = features.build({"BTC": rows_from(LINEAR_DAYS, linear)}, NOW)["BTC"]
LAST = LINEAR[-1]                      # day 249: close 349


def ret(i):                            # the linear series' simple return on day i
    return (100 + i) / (99 + i) - 1


class BuildingBlocks(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(features.sma([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])

    def test_ema_is_seeded_with_the_plain_average_and_skips_leading_blanks(self):
        # seed = mean(1,2,3) = 2; alpha = 0.5: 0.5*4 + 0.5*2 = 3; 0.5*5 + 0.5*3 = 4
        self.assertEqual(features.ema([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])
        self.assertEqual(features.ema([None, None, 1, 2, 3, 4, 5], 3), [None, None, None, None, 2, 3, 4])
        self.assertEqual(features.ema([1, 2], 3), [None, None])

    def test_wilder_smoothing(self):
        # seed = mean(4, 2) = 3 on index 2; then 3 * 1/2 + 6 / 2 = 4.5
        self.assertEqual(features.wilder([None, 4, 2, 6], 2, 1), [None, None, 3, 4.5])

    def test_true_range_takes_the_largest_of_three_distances(self):
        # day 1: max(12-10=2, |12-9.5|=2.5, |10-9.5|=0.5) = 2.5; day 2: max(3, |11-11|=0, |8-11|=3) = 3
        self.assertEqual(features.true_range([10, 12, 11], [9, 10, 8], [9.5, 11, 9]), [None, 2.5, 3])

    def test_true_range_each_of_the_three_terms_can_be_the_largest(self):
        self.assertEqual(features.true_range([10, 11], [9, 10], [10, 10.5])[1], 1.0)          # the day's own range
        self.assertEqual(features.true_range([10, 20], [9, 19], [10, 19.5])[1], 10.0)         # gap up: |high - previous close|
        self.assertEqual(features.true_range([20, 11], [19, 10], [20, 10.5])[1], 10.0)        # gap down: |low - previous close|

    def test_rsi_by_hand(self):
        # changes +1, +1, -1. Seed over the first 2 changes: gain 1, loss 0 -> 100. Then gain (1*1+0)/2 = 0.5, loss (0*1+1)/2 = 0.5 -> RS 1 -> 50
        self.assertEqual(features.rsi([10, 11, 12, 11], 2), [None, None, 100.0, 50.0])
        self.assertEqual(features.rsi([5, 5, 5, 5], 2), [None, None, 50.0, 50.0])       # no move at all is neutral
        self.assertEqual(features.rsi([4, 3, 2, 1], 2)[2:], [0.0, 0.0])                  # only down days

    def test_skewness_and_excess_kurtosis_by_hand(self):
        # values 1,2,3,4,10: mean 4, deviations -3,-2,-1,0,6. m2 = 10, m3 = 36, m4 = 278.8
        values = [1, 2, 3, 4, 10]
        self.assertAlmostEqual(features.skewness(values), 36 / 10 ** 1.5, places=12)
        self.assertAlmostEqual(features.excess_kurtosis(values), 278.8 / 100 - 3, places=12)
        self.assertIsNone(features.skewness([3, 3, 3]))
        self.assertIsNone(features.excess_kurtosis([3, 3, 3]))

    def test_value_at_risk_takes_the_fifth_worst_of_ninety(self):
        returns = [i / 1000 for i in range(90)]                    # 0.000 ... 0.089, already sorted
        worst = features.worst_k(returns)
        self.assertEqual(len(worst), 5)                            # ceil(0.05 * 90) = 5
        self.assertEqual(worst[-1], 0.004)                         # the 5th worst
        self.assertAlmostEqual(statistics.fmean(worst), 0.002, places=12)
        self.assertEqual(len(features.worst_k(list(range(20)))), 1)
        self.assertEqual(len(features.worst_k(list(range(100)))), 5)

    def test_correlation_and_beta(self):
        y = [1.0, -2.0, 3.0, -1.0, 2.0]
        corr, beta = features.correlation_and_beta([2 * v for v in y], y)
        self.assertAlmostEqual(corr, 1.0, places=12)
        self.assertAlmostEqual(beta, 2.0, places=12)
        corr, beta = features.correlation_and_beta([-0.5 * v for v in y], y)
        self.assertAlmostEqual(corr, -1.0, places=12)
        self.assertAlmostEqual(beta, -0.5, places=12)
        self.assertEqual(features.correlation_and_beta([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]), (None, None))   # Bitcoin did not move
        self.assertIsNone(features.correlation_and_beta([5.0, 5.0, 5.0], [1.0, 2.0, 3.0])[0])            # the coin did not move

    def test_rank_series_starts_at_the_ninetieth_value_and_is_the_agents_rank(self):
        values = [float(v) for v in range(1, 101)]
        ranks = features.rank_series(values)
        self.assertEqual(ranks[:89], [None] * 89)
        self.assertAlmostEqual(ranks[89], 100 * (89 + 0.5) / 90, places=12)       # 89 lower days, itself counts half
        self.assertEqual(ranks[89], agent.percentile_rank(values[:90], values[89]))
        self.assertAlmostEqual(ranks[99], 100 * (99 + 0.5) / 100, places=12)

    def test_rank_series_counts_a_tie_as_half_and_ignores_blanks(self):
        values = [None] * 5 + [1.0] * 90                                           # 90 equal values after 5 blanks
        ranks = features.rank_series(values)
        self.assertEqual(ranks[:94], [None] * 94)
        self.assertEqual(ranks[94], 50.0)                                          # nothing lower, all tied: half

    def test_usd_volume_uses_vwap_and_falls_back_to_the_close(self):
        self.assertEqual(features.usd_volume(10.0, 12.0, 3.0), 30.0)
        self.assertEqual(features.usd_volume(0.0, 12.0, 3.0), 36.0)
        self.assertEqual(features.usd_volume(None, 12.0, 3.0), 36.0)

    def test_ratio_never_divides_by_zero(self):
        self.assertIsNone(features.ratio(1, 0))
        self.assertIsNone(features.ratio(None, 2))
        self.assertEqual(features.ratio(3, 2), 1.5)


class Labels(unittest.TestCase):
    def test_vol_regime_boundaries(self):
        got = [features.vol_regime(r) for r in (None, 0, 24.9, 25, 74.9, 75, 94.9, 95, 100)]
        self.assertEqual(got, [None, "low", "low", "normal", "normal", "high", "high", "extreme", "extreme"])

    def test_momentum_state_boundaries(self):
        got = [features.momentum_state(r) for r in (None, 0, 30, 30.01, 69.99, 70, 100)]
        self.assertEqual(got, [None, "oversold", "oversold", "neutral", "neutral", "overbought", "overbought"])

    def test_band_state_boundaries(self):
        got = [features.band_state(p) for p in (None, -0.01, 0, 0.5, 1, 1.01)]
        self.assertEqual(got, [None, "below_lower", "inside", "inside", "inside", "above_upper"])

    def test_trend_50_200(self):
        self.assertEqual(features.trend_50_200(110, 105, 100), "up")
        self.assertEqual(features.trend_50_200(90, 95, 100), "down")
        self.assertEqual(features.trend_50_200(90, 105, 100), "mixed")     # the 50-day is above, but the close fell under the 200-day
        self.assertEqual(features.trend_50_200(110, 95, 100), "mixed")

    def test_stress_count_counts_each_condition_once(self):
        calm = (50, 50, 50, 50, 1.0, -5.0)
        self.assertEqual(features.stress_count(*calm), 0)
        self.assertEqual(features.stress_count(90, 50, 50, 50, 1.0, -5.0), 1)          # a rank at 90 counts
        self.assertEqual(features.stress_count(89.9, 50, 50, 50, 1.0, -5.0), 0)
        self.assertEqual(features.stress_count(50, 50, 50, 50, 2.0, -5.0), 1)          # volume at twice the average counts
        self.assertEqual(features.stress_count(50, 50, 50, 50, 1.99, -5.0), 0)
        self.assertEqual(features.stress_count(50, 50, 50, 50, 1.0, -20.0), 1)         # 20% under the 90-day high counts
        self.assertEqual(features.stress_count(50, 50, 50, 50, 1.0, -19.9), 0)
        self.assertEqual(features.stress_count(99, 99, 99, 99, 3.0, -30.0), 6)
        self.assertIsNone(features.stress_count(None, 50, 50, 50, 1.0, -5.0))          # a missing input is never counted as calm

    def test_risk_regime_boundaries(self):
        got = [features.risk_regime(n) for n in (None, 0, 1, 2, 3, 4, 6)]
        self.assertEqual(got, [None, "calm", "calm", "elevated", "elevated", "stressed", "stressed"])


class OneCoinByHand(unittest.TestCase):
    """The linear series (close 100 + i), day 249. Every expected value is plain arithmetic."""

    def check(self, column, expected, places=9):
        self.assertAlmostEqual(LAST[column], expected, places=places, msg=column)

    def test_the_kraken_values_come_through_unchanged(self):
        self.assertEqual((LAST["coin"], LAST["date_utc"]), ("BTC", agent.format_date(TODAY - DAY)))
        self.assertEqual((LAST["open"], LAST["high"], LAST["low"], LAST["close"], LAST["vwap"], LAST["volume"], LAST["trades"]),
                         (348.5, 350.0, 347.5, 349.0, 348.75, 14.0, 349))

    def test_the_unfinished_day_is_not_a_row(self):
        self.assertEqual(len(LINEAR), LINEAR_DAYS)
        self.assertEqual(LINEAR[0]["date_utc"], agent.format_date(TODAY - LINEAR_DAYS * DAY))

    def test_returns(self):
        self.check("ret_1d_pct", (349 / 348 - 1) * 100)
        self.check("log_ret_1d_pct", math.log(349 / 348) * 100)
        self.check("ret_7d_pct", (349 / 342 - 1) * 100)
        self.check("ret_30d_pct", (349 / 319 - 1) * 100)
        self.check("ret_60d_pct", (349 / 289 - 1) * 100)
        self.check("ret_90d_pct", (349 / 259 - 1) * 100)
        self.check("gap_pct", (348.5 / 348 - 1) * 100)
        self.check("body_pct", (349 / 348.5 - 1) * 100)

    def test_candle_anatomy(self):
        self.check("range_pct", 2.5 / 349 * 100)
        self.check("upper_wick_share", (350 - 349) / 2.5)
        self.check("lower_wick_share", (348.5 - 347.5) / 2.5)
        self.check("close_location", (349 - 347.5) / 2.5)
        self.check("vwap_dev_pct", (349 / 348.75 - 1) * 100)

    def test_volatility(self):
        self.check("true_range_pct", 2.5 / 349 * 100)          # max(2.5, |350-348| = 2, |347.5-348| = 0.5)
        self.check("atr14_pct", 2.5 / 349 * 100)               # every true range is 2.5, so is their average
        for name, days in (("vol_7d_pct", 7), ("vol_30d_pct", 30), ("vol_90d_pct", 90)):
            self.check(name, statistics.stdev([ret(i) for i in range(250 - days, 250)]) * 100)
        self.check("vol_30d_annualized_pct", LAST["vol_30d_pct"] * math.sqrt(365))
        self.check("vol_ratio_7_30", LAST["vol_7d_pct"] / LAST["vol_30d_pct"])
        window = range(220, 250)
        highs = {i: 100 + i + 1 for i in window}
        lows = {i: 100 + i - 1.5 for i in window}
        parkinson = math.sqrt(sum(math.log(highs[i] / lows[i]) ** 2 for i in window) / (4 * 30 * math.log(2)))
        self.check("parkinson_30d_pct", parkinson * 100)
        garman = sum(0.5 * math.log(highs[i] / lows[i]) ** 2 - (2 * math.log(2) - 1) * math.log((100 + i) / (100 + i - 0.5)) ** 2 for i in window) / 30
        self.check("garman_klass_30d_pct", math.sqrt(garman) * 100)

    def test_trend_and_momentum(self):
        self.check("dist_sma7_pct", (349 / 346 - 1) * 100)         # mean of 343..349
        self.check("dist_sma30_pct", (349 / 334.5 - 1) * 100)      # mean of 320..349
        self.check("dist_sma50_pct", (349 / 324.5 - 1) * 100)
        self.check("dist_sma200_pct", (349 / 249.5 - 1) * 100)
        self.check("rsi14", 100.0)                                 # every day was up
        closes = [100 + i for i in range(230, 250)]
        mean, sigma = statistics.fmean(closes), statistics.pstdev(closes)
        self.check("bb_pct_b", (349 - (mean - 2 * sigma)) / (4 * sigma))
        self.check("bb_bandwidth_pct", 4 * sigma / mean * 100)
        last30 = [100 + i for i in range(220, 250)]
        self.check("zscore_30d", (349 - statistics.fmean(last30)) / statistics.stdev(last30))

    def test_macd_is_a_difference_of_two_averages_as_a_share_of_the_close(self):
        closes = [100.0 + i for i in range(250)]

        def exp_average(n):
            value, alpha = statistics.fmean(closes[:n]), 2 / (n + 1)
            series = {n - 1: value}
            for i in range(n, 250):
                value = alpha * closes[i] + (1 - alpha) * value
                series[i] = value
            return series
        fast, slow = exp_average(12), exp_average(26)
        line = {i: fast[i] - slow[i] for i in range(25, 250)}
        signal = statistics.fmean([line[i] for i in range(25, 34)])
        for i in range(34, 250):
            signal = 0.2 * line[i] + 0.8 * signal
        self.check("macd_pct", line[249] / 349 * 100)
        self.check("macd_signal_pct", signal / 349 * 100)
        self.check("macd_hist_pct", (line[249] - signal) / 349 * 100)

    def test_drawdown_and_range(self):
        self.check("max_drawdown_30d_pct", 0.0)                    # it only went up
        self.check("max_drawdown_90d_pct", 0.0)
        self.check("drawdown_from_90d_high_pct", (349 / 350 - 1) * 100)
        self.check("above_90d_low_pct", (349 / 258.5 - 1) * 100)   # the 90-day low is day 160: 260 - 1.5
        self.check("range_position_90d", (349 - 258.5) / (350 - 258.5))
        self.assertEqual((LAST["days_since_90d_high"], LAST["days_since_90d_low"]), (0, 89))
        self.assertIsNone(LAST["drawdown_from_365d_high_pct"])     # only 250 days exist

    def test_tail_risk(self):
        newest = [ret(i) for i in range(245, 250)]                  # returns fall as the close rises: the five newest are the five worst
        self.check("var95_90d_pct", ret(245) * 100)
        self.check("cvar95_90d_pct", statistics.fmean(newest) * 100)
        self.check("worst_day_30d_pct", ret(249) * 100)
        self.check("best_day_30d_pct", ret(220) * 100)
        self.check("downside_dev_30d_pct", 0.0)
        self.check("down_day_share_30d", 0.0)
        window = [ret(i) for i in range(160, 250)]
        mean = statistics.fmean(window)
        m2 = statistics.fmean([(r - mean) ** 2 for r in window])
        m3 = statistics.fmean([(r - mean) ** 3 for r in window])
        m4 = statistics.fmean([(r - mean) ** 4 for r in window])
        self.check("skew_90d", m3 / m2 ** 1.5, places=6)
        self.check("excess_kurtosis_90d", m4 / m2 ** 2 - 3, places=6)

    def test_liquidity(self):
        quote = 348.75 * 14
        self.check("quote_volume_usd", quote)
        self.check("avg_trade_size_usd", quote / 349)
        self.check("signed_volume_balance_30d", 1.0)                # every day was up, so all the volume was on up days
        volumes = [10 + i % 7 for i in range(250)]
        quotes = [(100 + i - 0.25) * volumes[i] for i in range(250)]
        trades = [100 + i for i in range(250)]
        before = range(219, 249)
        self.check("volume_vs_30d_avg_x", 14 / statistics.fmean(volumes[i] for i in before))
        self.check("quote_volume_vs_30d_avg_x", quote / statistics.fmean(quotes[i] for i in before))
        self.check("trades_vs_30d_avg_x", 349 / statistics.fmean(trades[i] for i in before))
        earlier = [volumes[i] for i in before]
        self.check("volume_zscore_30d", (14 - statistics.fmean(earlier)) / statistics.stdev(earlier))
        amihud = statistics.fmean(abs(ret(i)) * 100 / (quotes[i] / 1e6) for i in range(220, 250))
        self.check("amihud_30d_pct_per_1m", amihud)

    def test_signed_volume_balance_counts_up_days_plus_down_days_minus_and_flat_days_zero(self):
        # day i closes +1 (i % 3 == 1), unchanged (i % 3 == 2) or -1 (i % 3 == 0) against the day before
        closes = [100]
        for i in range(1, 101):
            closes.append(closes[-1] + {1: 1, 2: 0, 0: -1}[i % 3])

        def pattern(i):
            c = closes[i]
            return c, c + 1, c - 1, c, c, 10 + i % 5, 50
        row = features.build({"BTC": rows_from(100, pattern)}, NOW)["BTC"][-1]
        sign = {1: 1, 2: 0, 0: -1}
        window = range(70, 100)
        expected = sum(sign[i % 3] * (10 + i % 5) for i in window) / sum(10 + i % 5 for i in window)
        self.assertAlmostEqual(row["signed_volume_balance_30d"], expected, places=12)
        self.assertAlmostEqual(row["down_day_share_30d"], sum(1 for i in window if sign[i % 3] < 0) / 30, places=12)

    def test_labels_on_the_linear_series(self):
        self.assertEqual((LAST["trend_30_60"], LAST["trend_50_200"], LAST["momentum_state"], LAST["band_state"]),
                         ("up", "up", "overbought", "inside"))
        self.assertEqual(LAST["trend_30_60"], agent.trend_label([100.0 + i for i in range(250)])[0])

    def test_nothing_is_invented_for_a_coin_alone(self):
        for name in ("corr_btc_30d", "beta_btc_30d", "rel_strength_30d_pct"):
            self.assertIsNone(LAST[name], name)                    # Bitcoin has no beta to itself; one coin has no group

    def test_the_agents_three_alert_metrics_are_the_agents_own_numbers(self):
        closes = [100.0 + i for i in range(250)]
        series, _ = agent.metric_series(closes)
        self.assertEqual(LAST["vol_30d_pct"], series["volatility"][-1] * 100)
        self.assertEqual(LAST["dist_sma30_pct"], series["distance"][-1] * 100)
        ranks = agent.ranks_at(series, 249)
        self.assertEqual((LAST["rank_vol_30d"], LAST["rank_dist_30d"], LAST["rank_drawdown_30d"]),
                         (ranks["volatility"], ranks["distance"], ranks["drawdown"]))
        self.assertEqual(LAST["at_alert_rank"], "TRUE" if max(ranks.values()) >= agent.ALERT_RANK else "FALSE")


class ShortAndGappyHistory(unittest.TestCase):
    def test_a_short_history_leaves_the_long_windows_blank_and_never_fills_them(self):
        short = features.build({"BTC": rows_from(95, linear)}, NOW)["BTC"][-1]
        for name in ("dist_sma200_pct", "trend_50_200", "drawdown_from_365d_high_pct", "rank_vol_30d",
                     "at_alert_rank", "vol_regime", "stress_signals", "risk_regime"):
            self.assertIsNone(short[name], name)
        for name in ("ret_90d_pct", "dist_sma50_pct", "rsi14", "drawdown_from_90d_high_pct", "var95_90d_pct", "vol_90d_pct"):
            self.assertIsNotNone(short[name], name)

    def test_the_first_days_are_blank_where_a_window_does_not_fit(self):
        first = LINEAR[0]
        self.assertEqual({name for name in features.NAMES if first[name] is None} & {"ret_1d_pct", "gap_pct", "true_range_pct", "rsi14"},
                         {"ret_1d_pct", "gap_pct", "true_range_pct", "rsi14"})
        self.assertIsNotNone(first["range_pct"])
        self.assertIsNotNone(first["close_location"])

    def test_a_flat_candle_has_no_wicks_and_no_location(self):
        def flat_last(i):
            o, h, l, c, w, v, t = linear(i)
            return (c, c, c, c, c, v, t) if i == 249 else (o, h, l, c, w, v, t)
        row = features.build({"BTC": rows_from(250, flat_last)}, NOW)["BTC"][-1]
        self.assertEqual(row["range_pct"], 0.0)
        for name in ("upper_wick_share", "lower_wick_share", "close_location"):
            self.assertIsNone(row[name], name)

    def test_a_day_with_no_trading_gives_a_blank_instead_of_a_division_by_zero(self):
        def quiet(i):
            o, h, l, c, w, v, t = linear(i)
            return (o, h, l, c, 0, 0, 0) if i == 249 else (o, h, l, c, w, v, t)
        row = features.build({"BTC": rows_from(250, quiet)}, NOW)["BTC"][-1]
        self.assertIsNone(row["vwap_dev_pct"])                     # vwap 0
        self.assertEqual(row["quote_volume_usd"], 0.0)
        self.assertIsNone(row["avg_trade_size_usd"])              # no trades: no average trade
        self.assertIsNone(row["amihud_30d_pct_per_1m"])            # a day with no money traded makes the 30-day average undefined

    def test_a_bad_candle_is_refused_with_the_agents_own_check(self):
        def broken(i):
            o, h, l, c, w, v, t = linear(i)
            return (o, l - 1, h, c, w, v, t) if i == 100 else (o, h, l, c, w, v, t)     # high below low
        with self.assertRaises(ValueError):
            features.build({"BTC": rows_from(250, broken)}, NOW)


class CrossAsset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        n = 120
        cls.btc_returns = [0.0] + [0.02 * math.sin(i * 1.3) + 0.01 * math.cos(i * 0.37) for i in range(1, n)]
        factors = {"BTC": 1.0, "ETH": 2.0, "XRP": -0.5}
        raw = {}
        for coin, factor in factors.items():
            closes, price = [], 100.0
            for r in cls.btc_returns:
                price *= 1 + factor * r
                closes.append(price)
            raw[coin] = rows_from(n - 1, lambda i, closes=closes: (closes[i], closes[i] * 1.001, closes[i] * 0.999, closes[i], closes[i], 5.0, 50))
        cls.out = features.build(raw, NOW)

    def test_a_coin_that_moves_twice_as_much_has_beta_2_and_correlation_1(self):
        last = self.out["ETH"][-1]
        self.assertAlmostEqual(last["beta_btc_30d"], 2.0, places=6)
        self.assertAlmostEqual(last["corr_btc_30d"], 1.0, places=6)

    def test_a_coin_that_moves_the_other_way_has_negative_beta_and_correlation(self):
        last = self.out["XRP"][-1]
        self.assertAlmostEqual(last["beta_btc_30d"], -0.5, places=6)
        self.assertAlmostEqual(last["corr_btc_30d"], -1.0, places=6)

    def test_bitcoin_has_no_beta_to_itself(self):
        self.assertIsNone(self.out["BTC"][-1]["beta_btc_30d"])
        self.assertIsNone(self.out["BTC"][-1]["corr_btc_30d"])

    def test_relative_strength_is_each_coins_30_day_change_minus_the_average_of_the_three(self):
        last = {coin: rows[-1] for coin, rows in self.out.items()}
        average = statistics.fmean(row["ret_30d_pct"] for row in last.values())
        for coin, row in last.items():
            self.assertAlmostEqual(row["rel_strength_30d_pct"], row["ret_30d_pct"] - average, places=9, msg=coin)
        self.assertAlmostEqual(sum(row["rel_strength_30d_pct"] for row in last.values()), 0.0, places=9)

    def test_the_cross_asset_columns_start_when_30_returns_exist(self):
        eth = self.out["ETH"]
        self.assertIsNone(eth[29]["beta_btc_30d"])
        self.assertIsNotNone(eth[30]["beta_btc_30d"])


class Columns(unittest.TestCase):
    def test_every_column_has_a_unique_name_a_known_group_a_format_and_a_meaning(self):
        names = [c[0] for c in features.COLUMNS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names, features.NAMES)
        groups = {"identity", "kraken", "returns", "candle", "volatility", "trend", "range", "tail risk", "liquidity",
                  "cross-asset", "own history", "labels"}
        for name, group, _unit, digits, meaning in features.COLUMNS:
            self.assertIn(group, groups, name)
            self.assertTrue(digits in ("text", "price") or (isinstance(digits, int) and 0 <= digits <= 6), name)
            self.assertTrue(meaning.strip(), name)

    def test_every_built_row_has_exactly_the_columns(self):
        for row in LINEAR[:3] + LINEAR[-3:]:
            self.assertEqual(list(row), features.NAMES)
        for cells in features.csv_rows(LINEAR[-2:]):
            self.assertEqual(len(cells), len(features.NAMES))

    def test_the_agent_uses_only_the_columns_it_says_it_uses(self):
        self.assertTrue(set(features.USED_BY_AGENT) <= set(features.NAMES))
        agent_columns = {n for n in features.NAMES if features.used_by_agent(n).startswith("yes")}
        self.assertEqual(agent_columns, set(features.USED_BY_AGENT))
        self.assertEqual(features.used_by_agent("rsi14"), "no (analysis only)")

    def test_comes_from_names_the_kraken_field_for_the_raw_columns(self):
        self.assertEqual(features.comes_from("high"), "Kraken OHLC field 2")
        self.assertEqual(features.comes_from("trades"), "Kraken OHLC field 7 (count)")
        self.assertEqual(features.comes_from("atr14_pct"), "calculated from the Kraken candles")

    def test_cells_are_blank_for_none_exact_for_kraken_values_and_rounded_otherwise(self):
        self.assertEqual(features.format_value("rsi14", None), "")
        self.assertEqual(features.format_value("close", 0.5123456789012345), "0.512345678901")     # 12 significant digits, like the candles file
        self.assertEqual(features.format_value("close", 94000.0), "94000")
        self.assertEqual(features.format_value("ret_1d_pct", 1.23456789), "1.2346")
        self.assertEqual(features.format_value("ret_1d_pct", -0.00001), "0.0000")                  # never "-0.0000"
        self.assertEqual(features.format_value("vol_30d_annualized_pct", 109.6849), "109.68")
        self.assertEqual(features.format_value("trades", 349), "349")
        self.assertEqual(features.format_value("risk_regime", "calm"), "calm")
        self.assertEqual(features.format_value("stress_signals", 2), "2")


if __name__ == "__main__":
    unittest.main()
