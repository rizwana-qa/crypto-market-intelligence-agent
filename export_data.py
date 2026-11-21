"""Saves what the agent reads from Kraken, and every factor it calculates from it, as CSV files.

    python export_data.py              writes the files to ./data_export
    python export_data.py --out DIR    writes them to DIR

Three files, all plain CSV (open them in Excel, Google Sheets or any editor):

    kraken_candles.csv    every daily candle Kraken returns, all 8 values, for BTC, ETH and XRP
    daily_factors.csv     the numbers the agent calculates from the closes, for every completed day
    columns_explained.csv one line per column of the two files above: where it comes from, what it means, whether the agent uses it

No AI and no tokens are used. The only network call is Kraken's public OHLC endpoint (no key), the same call the agent makes,
through the same code (agent.fetch_rows and the same checks). Nothing is written when any coin fails.
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import agent

CANDLE_COLUMNS = ["coin", "kraken_pair", "date_utc", "time_unix", "open", "high", "low", "close", "vwap", "volume", "trades", "status"]
FACTOR_COLUMNS = ["coin", "date_utc", "close", "volume", "daily_return_pct", "volume_vs_30d_avg_x", "volatility_30d_daily_pct",
                  "distance_from_30d_avg_pct", "max_drawdown_30d_pct", "rank_volatility", "rank_distance", "rank_drawdown",
                  "at_alert_rank"]
DICTIONARY_COLUMNS = ["file", "column", "comes_from", "meaning", "used_by_agent"]

COMPLETED = "completed"
UNFINISHED = "unfinished (today, dropped by the agent)"

# One line per column of the two data files. A test checks that this list and the real headers always match.
DICTIONARY = [
    ("kraken_candles.csv", "coin", "agent.WATCHLIST", "BTC, ETH or XRP", "yes"),
    ("kraken_candles.csv", "kraken_pair", "agent.WATCHLIST", "The pair asked for: XBTUSD, ETHUSD or XRPUSD (Kraken calls Bitcoin XBT)", "yes"),
    ("kraken_candles.csv", "date_utc", "Kraken field 0 (time), as a date", "The UTC day the candle covers; each candle starts at 00:00 UTC", "yes"),
    ("kraken_candles.csv", "time_unix", "Kraken field 0 (time)", "Start of the candle, in seconds since 1970-01-01 UTC", "yes"),
    ("kraken_candles.csv", "open", "Kraken field 1", "First trade price of the day, in USD", "no"),
    ("kraken_candles.csv", "high", "Kraken field 2", "Highest trade price of the day, in USD", "no"),
    ("kraken_candles.csv", "low", "Kraken field 3", "Lowest trade price of the day, in USD", "no"),
    ("kraken_candles.csv", "close", "Kraken field 4", "Last trade price of the day, in USD. Every price metric is built from the close", "yes"),
    ("kraken_candles.csv", "vwap", "Kraken field 5", "Volume-weighted average price of the day, in USD", "no"),
    ("kraken_candles.csv", "volume", "Kraken field 6", "Amount traded on Kraken during the day, in coins (not USD)", "yes"),
    ("kraken_candles.csv", "trades", "Kraken field 7 (count)", "Number of trades during the day", "no"),
    ("kraken_candles.csv", "status", "calculated", "completed, or unfinished for the current UTC day, which the agent drops so every number is from a full day", "yes"),
    ("daily_factors.csv", "coin", "agent.WATCHLIST", "BTC, ETH or XRP", "yes"),
    ("daily_factors.csv", "date_utc", "kraken_candles.csv", "The completed UTC day", "yes"),
    ("daily_factors.csv", "close", "kraken_candles.csv", "Closing price in USD", "yes"),
    ("daily_factors.csv", "volume", "kraken_candles.csv", "Volume in coins", "yes"),
    ("daily_factors.csv", "daily_return_pct", "calculated", "Close divided by the previous close, minus 1, in percent", "yes (shown to the AI)"),
    ("daily_factors.csv", "volume_vs_30d_avg_x", "calculated", "The day's volume divided by the average volume of the 30 days before it. Context only, never an alert", "yes (context)"),
    ("daily_factors.csv", "volatility_30d_daily_pct", "calculated", "Sample standard deviation of the last 30 daily returns, in percent. Alert metric 1", "yes (alert metric)"),
    ("daily_factors.csv", "distance_from_30d_avg_pct", "calculated", "Close compared with its 30-day average, in percent (the rank uses the absolute value). Alert metric 2", "yes (alert metric)"),
    ("daily_factors.csv", "max_drawdown_30d_pct", "calculated", "Largest fall from a running peak inside the last 31 closes, in percent (0 or negative). Alert metric 3", "yes (alert metric)"),
    ("daily_factors.csv", "rank_volatility", "calculated", "0 to 100: the share of the coin's own earlier days with a lower volatility (a tie counts half). Blank until the history is long enough to rank", "yes"),
    ("daily_factors.csv", "rank_distance", "calculated", "The same rank for the distance from the 30-day average", "yes"),
    ("daily_factors.csv", "rank_drawdown", "calculated", "The same rank for the 30-day drawdown", "yes"),
    ("daily_factors.csv", "at_alert_rank", "calculated", f"TRUE when any of the three ranks is {agent.ALERT_RANK:g} or higher. Only these days can start an alert", "yes"),
]


def _number(value, digits):
    return "" if value is None else f"{agent.round_clean(value, digits):.{digits}f}"


def candle_rows(coin, raw_rows, now):
    """Every raw Kraken row as CSV values, with the unfinished day marked."""
    today = int(now.timestamp()) // agent.SECONDS_PER_DAY * agent.SECONDS_PER_DAY
    rows = []
    for stamp, open_, high, low, close, vwap, volume, trades in raw_rows:
        status = COMPLETED if stamp < today else UNFINISHED
        rows.append([coin, agent.WATCHLIST[coin], agent.format_date(stamp), stamp, open_, high, low, close, vwap, volume, trades, status])
    return rows


def factor_rows(coin, raw_rows, now):
    """The agent's own numbers for every completed day. Same functions as the agent, not a copy of the maths."""
    candles = agent.parse_candles(raw_rows, agent.WATCHLIST[coin])
    stamps, closes = agent.completed_closes(candles, now)
    by_time = {c["timestamp"]: c for c in candles}
    volumes = [by_time[t]["volume"] for t in stamps]
    series, returns = agent.metric_series(closes)
    ratios = agent.volume_ratio_series(volumes)
    first_ranked = agent.WINDOW + agent.MIN_HISTORY_DAYS - 1   # the same first day as the base-rate count in the brief
    rows = []
    for i in range(len(closes)):
        ranks = agent.ranks_at(series, i) if i >= first_ranked else None
        drawdown = None if series["drawdown"][i] is None else -series["drawdown"][i] * 100
        rows.append([
            coin, agent.format_date(stamps[i]), _number(closes[i], 4), _number(volumes[i], 4),
            _number(returns[i - 1] * 100, 4) if i > 0 else "",
            _number(ratios[i], 4),
            _number(None if series["volatility"][i] is None else series["volatility"][i] * 100, 4),
            _number(None if series["distance"][i] is None else series["distance"][i] * 100, 4),
            _number(drawdown, 4),
            _number(ranks["volatility"], 1) if ranks else "", _number(ranks["distance"], 1) if ranks else "",
            _number(ranks["drawdown"], 1) if ranks else "",
            ("TRUE" if max(ranks.values()) >= agent.ALERT_RANK else "FALSE") if ranks else "",
        ])
    return rows


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def export(out_dir, now=None):
    """Fetches all coins first, then writes the three files. Raises (and writes nothing) when any coin fails."""
    now = now or datetime.now(timezone.utc)
    raw = {coin: agent.fetch_rows(pair) for coin, pair in agent.WATCHLIST.items()}
    candles, factors = [], []
    for coin, rows in raw.items():
        agent.parse_candles(rows, agent.WATCHLIST[coin])      # the agent's own row checks
        candles.extend(candle_rows(coin, rows, now))
        factors.extend(factor_rows(coin, rows, now))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "kraken_candles.csv", CANDLE_COLUMNS, candles)
    write_csv(out / "daily_factors.csv", FACTOR_COLUMNS, factors)
    write_csv(out / "columns_explained.csv", DICTIONARY_COLUMNS, DICTIONARY)
    return {"candles": len(candles), "factors": len(factors), "dir": str(out)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Save the Kraken data and the agent's factors as CSV files.")
    parser.add_argument("--out", default="data_export", help="folder for the three CSV files (default: data_export)")
    args = parser.parse_args(argv)
    try:
        result = export(args.out)
    except (RuntimeError, ValueError) as error:
        print(f"export failed, nothing written: {error}", file=sys.stderr)
        return 1
    print(f"wrote {result['candles']} candle rows and {result['factors']} daily factor rows to {result['dir']}/ "
          f"(kraken_candles.csv, daily_factors.csv, columns_explained.csv)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
