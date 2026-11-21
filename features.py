"""Market features: every number worth knowing about a coin, calculated from the 8 values Kraken sends for each daily candle.

    market_features = features.build(raw_rows_by_coin, now)      # {coin: [one dict per completed day]}

Pure Python (standard library only), no network and no AI. export_data.py writes the result to market_features.csv.

Why it exists. The agent raises its alerts from three numbers (30-day volatility, distance from the 30-day average, 30-day
drawdown) and shows the AI a few context values. An analyst, or a later version of the agent, needs the whole picture of a
day: how far price moved and how it moved inside the day, how volatile it is by several estimators, trend and momentum,
how far it sits from its highs, how bad its worst days are, how much money traded and how thin the market is, how it moves
with Bitcoin, and where today sits in the coin's own history. Every column is defined in COLUMNS, which is the single source
for the CSV header and for columns_explained.csv.

Honest limits:
- These numbers describe the past. None of them is tested as a forecast, and none of them raises an alert on its own.
- The labels (vol_regime, trend_50_200, momentum_state, band_state, risk_regime) are descriptive conventions chosen here,
  not trading signals. The agent's alert rule is unchanged: a rank of 95 or more on one of its three alert metrics.
- Anything that needs more days than exist is left blank, never filled in. Kraken's daily history is about 720 days.
- Volume is Kraken's own, not the whole market. The USD volume is vwap times coins (close times coins when the vwap is 0).
"""

import math
import statistics

import agent

DAYS_PER_YEAR = 365                 # crypto trades every day, so annualise with 365, not 252
VAR_LEVEL = 0.05                    # 95% historical value at risk: the 5% worst days
VAR_WINDOW = 90
TAIL_WINDOW = 90
LONG_HIGH_DAYS = 365
RANGE_DAYS = 90
SMA_DAYS = (7, 30, 50, 200)
RETURN_DAYS = (7, 30, 60, 90)
RSI_DAYS = 14
ATR_DAYS = 14
BOLLINGER_DAYS = 20
BOLLINGER_WIDTH = 2.0
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
VOL_SHORT_DAYS = 7
OVERBOUGHT, OVERSOLD = 70.0, 30.0
STRESS_RANK = 90.0                  # the agent's "near the alert rank" level
STRESS_VOLUME_X = 2.0               # a day with at least twice its 30-day average volume
STRESS_DRAWDOWN_PCT = -20.0         # 20% or more below the 90-day high
RISK_ELEVATED, RISK_STRESSED = 2, 4  # number of stress signals

# name, group, unit, digits (0 = whole number, "price" = exact Kraken digits, "text" = label), meaning
# digits is how many decimals the CSV shows. The meaning is what columns_explained.csv says.
COLUMNS = [
    # --- Kraken's 8 values, unchanged
    ("coin", "identity", "", "text", "BTC, ETH or XRP"),
    ("date_utc", "identity", "", "text", "The completed UTC day (each candle starts at 00:00 UTC)"),
    ("open", "kraken", "USD", "price", "First trade price of the day"),
    ("high", "kraken", "USD", "price", "Highest trade price of the day"),
    ("low", "kraken", "USD", "price", "Lowest trade price of the day"),
    ("close", "kraken", "USD", "price", "Last trade price of the day. Every price metric below is built from the close unless it says high, low or open"),
    ("vwap", "kraken", "USD", "price", "Volume-weighted average price of the day"),
    ("volume", "kraken", "coins", "price", "Amount traded on Kraken during the day, in coins"),
    ("trades", "kraken", "count", 0, "Number of trades during the day"),
    # --- returns
    ("ret_1d_pct", "returns", "%", 4, "Close divided by the previous close, minus 1"),
    ("log_ret_1d_pct", "returns", "%", 4, "Natural log of close divided by the previous close. Adds up over days, which the simple return does not"),
    ("ret_7d_pct", "returns", "%", 4, "Close compared with the close 7 days earlier"),
    ("ret_30d_pct", "returns", "%", 4, "Close compared with the close 30 days earlier"),
    ("ret_60d_pct", "returns", "%", 4, "Close compared with the close 60 days earlier"),
    ("ret_90d_pct", "returns", "%", 4, "Close compared with the close 90 days earlier"),
    ("gap_pct", "returns", "%", 4, "Today's open compared with yesterday's close. Large only when the market moved around the midnight UTC cut"),
    ("body_pct", "returns", "%", 4, "Close compared with the open of the same day"),
    # --- candle anatomy
    ("range_pct", "candle", "%", 4, "(High minus low) divided by the close: how far price travelled inside the day"),
    ("upper_wick_share", "candle", "0 to 1", 4, "Share of the day's range above the larger of open and close: a high rejected by sellers. Blank when high equals low"),
    ("lower_wick_share", "candle", "0 to 1", 4, "Share of the day's range below the smaller of open and close: a low rejected by buyers. Blank when high equals low"),
    ("close_location", "candle", "0 to 1", 4, "Where the close sits in the day's range: 0 is the low, 1 is the high. Blank when high equals low"),
    ("vwap_dev_pct", "candle", "%", 4, "Close compared with the day's vwap: positive means the day ended above its average traded price"),
    # --- volatility
    ("true_range_pct", "volatility", "%", 4, "Wilder's true range (the largest of high-low, high-prev close, prev close-low) divided by the close"),
    ("atr14_pct", "volatility", "%", 4, "Average true range over 14 days (Wilder smoothing) divided by the close"),
    ("vol_7d_pct", "volatility", "%", 4, "Sample standard deviation of the last 7 daily returns"),
    ("vol_30d_pct", "volatility", "%", 4, "Sample standard deviation of the last 30 daily returns. The agent's alert metric 1"),
    ("vol_90d_pct", "volatility", "%", 4, "Sample standard deviation of the last 90 daily returns"),
    ("vol_30d_annualized_pct", "volatility", "%", 2, "vol_30d_pct times the square root of 365"),
    ("vol_ratio_7_30", "volatility", "x", 4, "vol_7d_pct divided by vol_30d_pct: above 1 means volatility is rising, below 1 means it is calming"),
    ("parkinson_30d_pct", "volatility", "%", 4, "Parkinson estimator over 30 days: volatility from the daily high and low only (blind to open and close)"),
    ("garman_klass_30d_pct", "volatility", "%", 4, "Garman-Klass estimator over 30 days: volatility from open, high, low and close"),
    # --- trend and momentum
    ("dist_sma7_pct", "trend", "%", 4, "Close compared with its 7-day simple average"),
    ("dist_sma30_pct", "trend", "%", 4, "Close compared with its 30-day simple average. The agent's alert metric 2 (it ranks the absolute value)"),
    ("dist_sma50_pct", "trend", "%", 4, "Close compared with its 50-day simple average"),
    ("dist_sma200_pct", "trend", "%", 4, "Close compared with its 200-day simple average"),
    ("macd_pct", "trend", "% of close", 4, "12-day exponential average minus 26-day exponential average, divided by the close"),
    ("macd_signal_pct", "trend", "% of close", 4, "9-day exponential average of the MACD line, divided by the close"),
    ("macd_hist_pct", "trend", "% of close", 4, "MACD line minus its signal line, divided by the close: the momentum of the momentum"),
    ("rsi14", "trend", "0 to 100", 2, "Relative strength index, 14 days, Wilder smoothing. 100 means only up days, 0 only down days"),
    ("bb_pct_b", "trend", "ratio", 4, "Bollinger %B: where the close sits in the 20-day, 2-standard-deviation band. 0 is the lower band, 1 the upper, outside 0 to 1 is outside the band"),
    ("bb_bandwidth_pct", "trend", "%", 4, "Width of the Bollinger band (upper minus lower) divided by the 20-day average: a squeeze is a low value"),
    ("zscore_30d", "trend", "std devs", 4, "Close minus its 30-day average, divided by the sample standard deviation of the last 30 closes"),
    # --- drawdown and range
    ("max_drawdown_30d_pct", "range", "%", 4, "Largest fall from a running peak inside the last 31 closes (0 or negative). The agent's alert metric 3"),
    ("max_drawdown_90d_pct", "range", "%", 4, "Largest fall from a running peak inside the last 91 closes (0 or negative)"),
    ("drawdown_from_90d_high_pct", "range", "%", 4, "Close compared with the highest daily high of the last 90 days (0 or negative)"),
    ("drawdown_from_365d_high_pct", "range", "%", 4, "Close compared with the highest daily high of the last 365 days (0 or negative)"),
    ("above_90d_low_pct", "range", "%", 4, "Close compared with the lowest daily low of the last 90 days (0 or positive)"),
    ("range_position_90d", "range", "0 to 1", 4, "Where the close sits between the 90-day low (0) and the 90-day high (1)"),
    ("days_since_90d_high", "range", "days", 0, "Days since the 90-day high was set (0 means today)"),
    ("days_since_90d_low", "range", "days", 0, "Days since the 90-day low was set (0 means today)"),
    # --- tail risk
    ("var95_90d_pct", "tail risk", "%", 4, "Historical value at risk: the 5th worst daily return of the last 90 days (a loss, so negative). 5 days in 100 were worse"),
    ("cvar95_90d_pct", "tail risk", "%", 4, "Expected shortfall: the average of the 5 worst daily returns of the last 90 days"),
    ("worst_day_30d_pct", "tail risk", "%", 4, "Lowest daily return of the last 30 days"),
    ("best_day_30d_pct", "tail risk", "%", 4, "Highest daily return of the last 30 days"),
    ("skew_90d", "tail risk", "ratio", 4, "Skewness of the last 90 daily returns: negative means the large moves were mostly down"),
    ("excess_kurtosis_90d", "tail risk", "ratio", 4, "Excess kurtosis of the last 90 daily returns: above 0 means fatter tails than a normal distribution"),
    ("downside_dev_30d_pct", "tail risk", "%", 4, "Root mean square of the down days in the last 30 (up days count as 0): volatility that only counts losses"),
    ("down_day_share_30d", "tail risk", "0 to 1", 4, "Share of the last 30 days that closed lower than the day before"),
    # --- volume and liquidity
    ("quote_volume_usd", "liquidity", "USD", 0, "Money traded on Kraken that day: vwap times volume (close times volume when the vwap is 0)"),
    ("volume_vs_30d_avg_x", "liquidity", "x", 4, "The day's volume divided by the average volume of the 30 days before it. Context only, never an alert"),
    ("quote_volume_vs_30d_avg_x", "liquidity", "x", 4, "The day's USD volume divided by the average USD volume of the 30 days before it"),
    ("volume_zscore_30d", "liquidity", "std devs", 4, "The day's volume minus the average of the 30 days before it, divided by their sample standard deviation"),
    ("trades_vs_30d_avg_x", "liquidity", "x", 4, "The day's trade count divided by the average count of the 30 days before it"),
    ("avg_trade_size_usd", "liquidity", "USD", 2, "USD volume divided by the number of trades: a rise means bigger orders (a thin or institutional day)"),
    ("signed_volume_balance_30d", "liquidity", "-1 to 1", 4, "Over 30 days: volume on up days minus volume on down days, divided by total volume. A proxy for who dominated, not true buy or sell flow"),
    ("amihud_30d_pct_per_1m", "liquidity", "% per 1M USD", 4, "30-day average of the absolute daily return divided by USD volume in millions: how far price moves per USD 1M traded. Higher means thinner"),
    # --- cross-asset
    ("corr_btc_30d", "cross-asset", "-1 to 1", 4, "Correlation of the last 30 daily returns with Bitcoin's. Blank for BTC"),
    ("beta_btc_30d", "cross-asset", "x", 4, "Beta to Bitcoin over 30 days: the covariance with Bitcoin's returns divided by Bitcoin's variance. 2 means it moved twice as much. Blank for BTC"),
    ("rel_strength_30d_pct", "cross-asset", "points", 4, "ret_30d_pct minus the average ret_30d_pct of the three coins: positive means ahead of the group"),
    # --- the coin's own history (percent of its earlier days that were lower, a tie counts half, needs 90 values)
    ("rank_vol_30d", "own history", "0 to 100", 1, "Rank of vol_30d_pct in the coin's own history. The agent's alert rank for volatility"),
    ("rank_dist_30d", "own history", "0 to 100", 1, "Rank of the absolute dist_sma30_pct. The agent's alert rank for distance"),
    ("rank_drawdown_30d", "own history", "0 to 100", 1, "Rank of the 30-day drawdown depth. The agent's alert rank for drawdown"),
    ("at_alert_rank", "own history", "TRUE or FALSE", "text", f"TRUE when any of the three agent ranks is {agent.ALERT_RANK:g} or higher. Only these days can start an alert"),
    ("rank_range_pct", "own history", "0 to 100", 1, "Rank of range_pct: how wide the day was compared with the coin's other days"),
    ("rank_atr14", "own history", "0 to 100", 1, "Rank of atr14_pct"),
    ("rank_vol_7d", "own history", "0 to 100", 1, "Rank of vol_7d_pct: a short-term volatility reading"),
    ("rank_parkinson_30d", "own history", "0 to 100", 1, "Rank of parkinson_30d_pct"),
    ("rank_quote_volume", "own history", "0 to 100", 1, "Rank of quote_volume_usd: how busy the day was"),
    ("rank_trades", "own history", "0 to 100", 1, "Rank of the trade count"),
    ("rank_amihud", "own history", "0 to 100", 1, "Rank of amihud_30d_pct_per_1m: how thin the market is compared with its own past"),
    # --- descriptive labels (conventions chosen here, not signals)
    ("vol_regime", "labels", "label", "text", "From rank_vol_30d: extreme (95 or more), high (75 or more), normal (25 or more), low"),
    ("trend_30_60", "labels", "label", "text", "The agent's trend label: up, down or mixed (30 and 60-day changes and the 30-day average agree, or not)"),
    ("trend_50_200", "labels", "label", "text", "up when the 50-day average is above the 200-day average and the close is above the 200-day average, down for the mirror image, otherwise mixed"),
    ("momentum_state", "labels", "label", "text", "From rsi14: overbought (70 or more), oversold (30 or less), otherwise neutral"),
    ("band_state", "labels", "label", "text", "above_upper or below_lower when the close is outside the Bollinger band, otherwise inside"),
    ("stress_signals", "labels", "count 0 to 6", 0, f"How many of these hold: rank_vol_30d, rank_dist_30d, rank_drawdown_30d or rank_range_pct at {STRESS_RANK:g} or more, volume at {STRESS_VOLUME_X:g}x its 30-day average or more, the close {abs(STRESS_DRAWDOWN_PCT):g}% or more below the 90-day high"),
    ("risk_regime", "labels", "label", "text", f"calm (0 or 1 stress signals), elevated ({RISK_ELEVATED} or 3), stressed ({RISK_STRESSED} or more). A summary of the data, not an alert and not advice"),
]

NAMES = [column[0] for column in COLUMNS]
_DIGITS = {column[0]: column[3] for column in COLUMNS}

# Columns the agent itself reads or shows to the AI. Everything else is for analysis only.
USED_BY_AGENT = {
    "coin": "yes", "date_utc": "yes", "close": "yes", "volume": "yes",
    "ret_1d_pct": "yes (shown to the AI)", "ret_30d_pct": "yes (context)", "ret_60d_pct": "yes (context)",
    "volume_vs_30d_avg_x": "yes (context)", "trend_30_60": "yes (context)",
    "vol_30d_pct": "yes (alert metric)", "dist_sma30_pct": "yes (alert metric)", "max_drawdown_30d_pct": "yes (alert metric)",
    "rank_vol_30d": "yes", "rank_dist_30d": "yes", "rank_drawdown_30d": "yes", "at_alert_rank": "yes",
}


def used_by_agent(name):
    return USED_BY_AGENT.get(name, "no (analysis only)")


def comes_from(name):
    group = next(column[1] for column in COLUMNS if column[0] == name)
    if group == "identity":
        return "agent.WATCHLIST / Kraken field 0 (time)"
    if group == "kraken":
        return "Kraken OHLC field " + {"open": "1", "high": "2", "low": "3", "close": "4", "vwap": "5", "volume": "6", "trades": "7 (count)"}[name]
    return "calculated from the Kraken candles"


# ---- small building blocks. Each returns a list as long as its input, None where there is not enough history.

def pct(value):
    return None if value is None else value * 100


def ratio(a, b):
    return None if a is None or b is None or b == 0 else a / b


def mean_of(values):
    return statistics.fmean(values)


def sample_stdev(values):
    return statistics.stdev(values) if len(values) >= 2 else None


def rolling(values, n, function):
    """function(window) for every day that has n values, else None. The window ends on the day itself."""
    return [function(values[i - n + 1:i + 1]) if i >= n - 1 else None for i in range(len(values))]


def sma(values, n):
    return rolling(values, n, mean_of)


def ema(values, n):
    """Exponential average, alpha = 2/(n+1), seeded with the plain average of the first n values (the usual convention).
    Leading None values are skipped, so it can smooth the MACD line."""
    out = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), None)
    if start is None or len(values) - start < n:
        return out
    seed = start + n - 1
    out[seed] = mean_of(values[start:seed + 1])
    alpha = 2.0 / (n + 1)
    for i in range(seed + 1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def wilder(values, n, first):
    """Wilder smoothing: the first value is the plain average of n values starting at index first, then
    previous * (n-1)/n + value / n."""
    out = [None] * len(values)
    seed = first + n - 1
    if seed >= len(values):
        return out
    out[seed] = mean_of(values[first:seed + 1])
    for i in range(seed + 1, len(values)):
        out[i] = (out[i - 1] * (n - 1) + values[i]) / n
    return out


def true_range(high, low, close):
    """Wilder's true range; day 0 has no previous close, so None."""
    out = [None]
    for i in range(1, len(close)):
        out.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return out


def rsi(closes, n=RSI_DAYS):
    changes = [None] + [b - a for a, b in zip(closes, closes[1:])]
    gains = [None if c is None else max(c, 0.0) for c in changes]
    losses = [None if c is None else max(-c, 0.0) for c in changes]
    avg_gain, avg_loss = wilder(gains, n, 1), wilder(losses, n, 1)
    out = []
    for gain, loss in zip(avg_gain, avg_loss):
        if gain is None:
            out.append(None)
        elif loss == 0:
            out.append(50.0 if gain == 0 else 100.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + gain / loss))
    return out


def moment(values, k):
    mean = mean_of(values)
    return mean_of([(v - mean) ** k for v in values])


def skewness(values):
    m2 = moment(values, 2)
    return None if m2 == 0 else moment(values, 3) / m2 ** 1.5


def excess_kurtosis(values):
    m2 = moment(values, 2)
    return None if m2 == 0 else moment(values, 4) / m2 ** 2 - 3.0


def worst_k(returns, level=VAR_LEVEL):
    """The k worst returns, k = ceil(level * n) (5 of 90 at 5%), sorted from the worst."""
    k = math.ceil(level * len(returns))
    return sorted(returns)[:k]


def correlation_and_beta(x, y):
    """Correlation of x with y, and the beta of x on y (cov / var of y). None when y or x does not move."""
    mx, my = mean_of(x), mean_of(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) - 1)
    vx, vy = sample_stdev(x) ** 2, sample_stdev(y) ** 2
    if vy == 0:
        return None, None
    beta = cov / vy
    corr = None if vx == 0 else cov / math.sqrt(vx * vy)
    return corr, beta


def rank_series(values):
    """The agent's own rank (agent.percentile_rank) of each day inside the history up to that day.
    None until the coin has agent.MIN_HISTORY_DAYS values of that metric."""
    out, seen = [], []
    for value in values:
        if value is None:
            out.append(None)
            continue
        seen.append(value)
        out.append(agent.percentile_rank(seen, value) if len(seen) >= agent.MIN_HISTORY_DAYS else None)
    return out


# ---- the descriptive labels. Conventions chosen here, not signals: each is a plain rule on numbers already in the file.

def vol_regime(rank):
    if rank is None:
        return None
    return "extreme" if rank >= agent.ALERT_RANK else "high" if rank >= 75 else "normal" if rank >= 25 else "low"


def trend_50_200(close, average50, average200):
    if average50 > average200 and close > average200:
        return "up"
    if average50 < average200 and close < average200:
        return "down"
    return "mixed"


def momentum_state(rsi_value):
    if rsi_value is None:
        return None
    return "overbought" if rsi_value >= OVERBOUGHT else "oversold" if rsi_value <= OVERSOLD else "neutral"


def band_state(pct_b):
    if pct_b is None:
        return None
    return "above_upper" if pct_b > 1 else "below_lower" if pct_b < 0 else "inside"


def stress_count(rank_vol, rank_dist, rank_drawdown, rank_range, volume_x, drawdown_from_high_pct):
    """How many of the six stress conditions hold. None when any input is missing (the ranks need 90 days of history)."""
    inputs = (rank_vol, rank_dist, rank_drawdown, rank_range, volume_x, drawdown_from_high_pct)
    if None in inputs:
        return None
    return (sum(1 for rank in inputs[:4] if rank >= STRESS_RANK) + (volume_x >= STRESS_VOLUME_X)
            + (drawdown_from_high_pct <= STRESS_DRAWDOWN_PCT))


def risk_regime(count):
    if count is None:
        return None
    return "stressed" if count >= RISK_STRESSED else "elevated" if count >= RISK_ELEVATED else "calm"


# ---- one coin

def read_days(raw_rows, pair, now):
    """The completed days of one coin as parallel lists. The agent's own checks run first, so a bad candle is refused
    here exactly as it is in the agent."""
    candles = agent.parse_candles(raw_rows, pair)
    stamps, _ = agent.completed_closes(candles, now)
    wanted = set(stamps)
    days = {key: [] for key in ("stamp", "open", "high", "low", "close", "vwap", "volume", "trades")}
    for row in raw_rows:
        if row[0] not in wanted:
            continue
        days["stamp"].append(row[0])
        for key, index in (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("vwap", 5), ("volume", 6)):
            days[key].append(agent.as_number(row[index]))
        days["trades"].append(row[7])
    return days


def usd_volume(vwap, close, volume):
    price = vwap if vwap is not None and vwap > 0 else close
    return price * volume


def one_coin(days):
    """Every column that needs only this coin's own days. Returns {column: list}, plus the returns."""
    o, h, l, c, vwap = days["open"], days["high"], days["low"], days["close"], days["vwap"]
    vol, trades = days["volume"], days["trades"]
    n = len(c)
    f = {name: [None] * n for name in NAMES}
    ret = [None] + [c[i] / c[i - 1] - 1 for i in range(1, n)]            # ret[i] belongs to day i

    for i in range(n):
        f["ret_1d_pct"][i] = pct(ret[i])
        f["log_ret_1d_pct"][i] = pct(math.log(c[i] / c[i - 1])) if i else None
        for days_back in RETURN_DAYS:
            if i >= days_back:
                f[f"ret_{days_back}d_pct"][i] = pct(c[i] / c[i - days_back] - 1)
        f["gap_pct"][i] = pct(o[i] / c[i - 1] - 1) if i else None
        f["body_pct"][i] = pct(c[i] / o[i] - 1)
        span = h[i] - l[i]
        f["range_pct"][i] = pct(span / c[i])
        if span > 0:
            f["upper_wick_share"][i] = (h[i] - max(o[i], c[i])) / span
            f["lower_wick_share"][i] = (min(o[i], c[i]) - l[i]) / span
            f["close_location"][i] = (c[i] - l[i]) / span
        f["vwap_dev_pct"][i] = pct(c[i] / vwap[i] - 1) if vwap[i] and vwap[i] > 0 else None

    # volatility
    tr = true_range(h, l, c)
    atr = wilder(tr, ATR_DAYS, 1)
    for i in range(n):
        f["true_range_pct"][i] = pct(ratio(tr[i], c[i]))
        f["atr14_pct"][i] = pct(ratio(atr[i], c[i]))
    series, _ = agent.metric_series(c)                                 # the agent's three alert metrics, from the agent's own code
    for i in range(n):
        f["vol_30d_pct"][i] = pct(series["volatility"][i])
        f["dist_sma30_pct"][i] = pct(series["distance"][i])
        f["max_drawdown_30d_pct"][i] = None if series["drawdown"][i] is None else -series["drawdown"][i] * 100
    for name, width in (("vol_7d_pct", VOL_SHORT_DAYS), ("vol_90d_pct", 90)):
        for i in range(width, n):
            f[name][i] = pct(statistics.stdev(ret[i - width + 1:i + 1]))
    for i in range(n):
        v30 = f["vol_30d_pct"][i]
        f["vol_30d_annualized_pct"][i] = None if v30 is None else v30 * math.sqrt(DAYS_PER_YEAR)
        f["vol_ratio_7_30"][i] = ratio(f["vol_7d_pct"][i], v30)
        if i >= agent.WINDOW - 1:
            w = range(i - agent.WINDOW + 1, i + 1)
            f["parkinson_30d_pct"][i] = pct(math.sqrt(sum(math.log(h[k] / l[k]) ** 2 for k in w) / (4 * len(w) * math.log(2))))
            gk = mean_of([0.5 * math.log(h[k] / l[k]) ** 2 - (2 * math.log(2) - 1) * math.log(c[k] / o[k]) ** 2 for k in w])
            f["garman_klass_30d_pct"][i] = pct(math.sqrt(max(gk, 0.0)))

    # trend and momentum
    for width in (w for w in SMA_DAYS if w != agent.WINDOW):         # the 30-day distance is the agent's own number (above)
        average = sma(c, width)
        for i in range(n):
            f[f"dist_sma{width}_pct"][i] = None if average[i] is None else pct(c[i] / average[i] - 1)
    fast, slow = ema(c, MACD_FAST), ema(c, MACD_SLOW)
    macd = [None if a is None or b is None else a - b for a, b in zip(fast, slow)]
    signal = ema(macd, MACD_SIGNAL)
    strength = rsi(c)
    mean20 = sma(c, BOLLINGER_DAYS)
    mean30 = sma(c, agent.WINDOW)
    for i in range(n):
        f["macd_pct"][i] = pct(ratio(macd[i], c[i]))
        f["macd_signal_pct"][i] = pct(ratio(signal[i], c[i]))
        if macd[i] is not None and signal[i] is not None:
            f["macd_hist_pct"][i] = pct((macd[i] - signal[i]) / c[i])
        f["rsi14"][i] = strength[i]
        if mean20[i] is not None:
            sigma = statistics.pstdev(c[i - BOLLINGER_DAYS + 1:i + 1])      # the Bollinger convention: population st. dev.
            if sigma > 0:
                f["bb_pct_b"][i] = (c[i] - (mean20[i] - BOLLINGER_WIDTH * sigma)) / (2 * BOLLINGER_WIDTH * sigma)
            f["bb_bandwidth_pct"][i] = pct(2 * BOLLINGER_WIDTH * sigma / mean20[i])
        if mean30[i] is not None:
            sigma30 = statistics.stdev(c[i - agent.WINDOW + 1:i + 1])
            f["zscore_30d"][i] = ratio(c[i] - mean30[i], sigma30)

    # drawdown and range
    for i in range(n):
        if i >= 90:
            f["max_drawdown_90d_pct"][i] = pct(agent.max_drawdown(c[i - 90:i + 1]))
        if i >= RANGE_DAYS - 1:
            top = max(h[i - RANGE_DAYS + 1:i + 1])
            bottom = min(l[i - RANGE_DAYS + 1:i + 1])
            f["drawdown_from_90d_high_pct"][i] = pct(c[i] / top - 1)
            f["above_90d_low_pct"][i] = pct(c[i] / bottom - 1)
            f["range_position_90d"][i] = ratio(c[i] - bottom, top - bottom)
            window_high = h[i - RANGE_DAYS + 1:i + 1]
            window_low = l[i - RANGE_DAYS + 1:i + 1]
            f["days_since_90d_high"][i] = RANGE_DAYS - 1 - max(k for k, v in enumerate(window_high) if v == top)
            f["days_since_90d_low"][i] = RANGE_DAYS - 1 - max(k for k, v in enumerate(window_low) if v == bottom)
        if i >= LONG_HIGH_DAYS - 1:
            f["drawdown_from_365d_high_pct"][i] = pct(c[i] / max(h[i - LONG_HIGH_DAYS + 1:i + 1]) - 1)

    # tail risk
    for i in range(n):
        if i >= VAR_WINDOW:
            window = ret[i - VAR_WINDOW + 1:i + 1]
            worst = worst_k(window)
            f["var95_90d_pct"][i] = pct(worst[-1])
            f["cvar95_90d_pct"][i] = pct(mean_of(worst))
            f["skew_90d"][i] = skewness(window)
            f["excess_kurtosis_90d"][i] = excess_kurtosis(window)
        if i >= agent.WINDOW:
            window = ret[i - agent.WINDOW + 1:i + 1]
            f["worst_day_30d_pct"][i] = pct(min(window))
            f["best_day_30d_pct"][i] = pct(max(window))
            f["downside_dev_30d_pct"][i] = pct(math.sqrt(mean_of([min(r, 0.0) ** 2 for r in window])))
            f["down_day_share_30d"][i] = sum(1 for r in window if r < 0) / len(window)

    # volume and liquidity
    quote = [usd_volume(vwap[i], c[i], vol[i]) for i in range(n)]
    ratios = agent.volume_ratio_series(vol)
    for i in range(n):
        f["quote_volume_usd"][i] = quote[i]
        f["volume_vs_30d_avg_x"][i] = ratios[i]
        f["avg_trade_size_usd"][i] = ratio(quote[i], trades[i])
        if i >= agent.WINDOW:
            before_volume = vol[i - agent.WINDOW:i]
            before_quote = quote[i - agent.WINDOW:i]
            before_trades = trades[i - agent.WINDOW:i]
            f["quote_volume_vs_30d_avg_x"][i] = ratio(quote[i], mean_of(before_quote))
            f["volume_zscore_30d"][i] = ratio(vol[i] - mean_of(before_volume), sample_stdev(before_volume))
            f["trades_vs_30d_avg_x"][i] = ratio(trades[i], mean_of(before_trades))
            window = range(i - agent.WINDOW + 1, i + 1)
            total = sum(vol[k] for k in window)
            if total > 0:
                f["signed_volume_balance_30d"][i] = sum((vol[k] if ret[k] > 0 else -vol[k] if ret[k] < 0 else 0.0) for k in window) / total
            amihud = [abs(ret[k]) * 100 / (quote[k] / 1e6) for k in window if quote[k] > 0]
            if len(amihud) == len(window):
                f["amihud_30d_pct_per_1m"][i] = mean_of(amihud)

    # the coin's own history
    first_ranked = agent.WINDOW + agent.MIN_HISTORY_DAYS - 1               # the same first day as the agent's base rate
    for i in range(first_ranked, n):
        ranks = agent.ranks_at(series, i)
        f["rank_vol_30d"][i] = ranks["volatility"]
        f["rank_dist_30d"][i] = ranks["distance"]
        f["rank_drawdown_30d"][i] = ranks["drawdown"]
        f["at_alert_rank"][i] = "TRUE" if max(ranks.values()) >= agent.ALERT_RANK else "FALSE"
    for name, source in (("rank_range_pct", f["range_pct"]), ("rank_atr14", f["atr14_pct"]), ("rank_vol_7d", f["vol_7d_pct"]),
                         ("rank_parkinson_30d", f["parkinson_30d_pct"]), ("rank_quote_volume", quote),
                         ("rank_trades", trades), ("rank_amihud", f["amihud_30d_pct_per_1m"])):
        f[name] = rank_series(source)

    # labels
    for i in range(n):
        f["vol_regime"][i] = vol_regime(f["rank_vol_30d"][i])
        if i >= agent.TREND_LONG_DAYS:
            f["trend_30_60"][i] = agent.trend_label(c[:i + 1])[0]
        if i >= 199:
            f["trend_50_200"][i] = trend_50_200(c[i], mean_of(c[i - 49:i + 1]), mean_of(c[i - 199:i + 1]))
        f["momentum_state"][i] = momentum_state(strength[i])
        f["band_state"][i] = band_state(f["bb_pct_b"][i])
        count = stress_count(f["rank_vol_30d"][i], f["rank_dist_30d"][i], f["rank_drawdown_30d"][i], f["rank_range_pct"][i],
                             f["volume_vs_30d_avg_x"][i], f["drawdown_from_90d_high_pct"][i])
        f["stress_signals"][i] = count
        f["risk_regime"][i] = risk_regime(count)
    return f, ret


# ---- all coins

def build(raw_by_coin, now):
    """{coin: [one dict per completed day]} for every coin in raw_by_coin ({coin: raw Kraken rows}). The cross-asset
    columns use Bitcoin's days, matched by date, so a coin with a missing day never borrows another day's return."""
    pairs = agent.WATCHLIST
    days = {coin: read_days(rows, pairs[coin], now) for coin, rows in raw_by_coin.items()}
    own = {coin: one_coin(d) for coin, d in days.items()}
    by_date = {coin: {days[coin]["stamp"][i]: own[coin][1][i] for i in range(len(days[coin]["stamp"]))} for coin in days}
    ret30 = {coin: dict(zip(days[coin]["stamp"], own[coin][0]["ret_30d_pct"])) for coin in days}
    out = {}
    for coin, (f, ret) in own.items():
        stamps = days[coin]["stamp"]
        for i, stamp in enumerate(stamps):
            if coin != "BTC" and "BTC" in by_date and i >= agent.WINDOW:
                window = stamps[i - agent.WINDOW + 1:i + 1]
                btc = [by_date["BTC"].get(s) for s in window]
                mine = [ret[k] for k in range(i - agent.WINDOW + 1, i + 1)]
                if None not in btc and None not in mine:
                    f["corr_btc_30d"][i], f["beta_btc_30d"][i] = correlation_and_beta(mine, btc)
            group = [ret30[other].get(stamp) for other in days]
            if None not in group and len(group) == len(pairs) and f["ret_30d_pct"][i] is not None:
                f["rel_strength_30d_pct"][i] = f["ret_30d_pct"][i] - mean_of(group)
        rows = []
        for i, stamp in enumerate(stamps):
            row = {name: f[name][i] for name in NAMES}
            row["coin"] = coin
            row["date_utc"] = agent.format_date(stamp)
            for key in ("open", "high", "low", "close", "vwap", "volume", "trades"):
                row[key] = days[coin][key][i]
            rows.append(row)
        out[coin] = rows
    return out


def format_value(name, value):
    """A CSV cell. Blank for None; Kraken's own numbers keep their digits; calculated numbers are rounded as COLUMNS says."""
    if value is None:
        return ""
    digits = _DIGITS[name]
    if digits == "text":
        return str(value)
    if digits == "price":
        return f"{value:.12g}"
    return f"{agent.round_clean(value, digits):.{digits}f}"


def csv_rows(rows):
    return [[format_value(name, row[name]) for name in NAMES] for row in rows]
