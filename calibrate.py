"""Backtest of the alert rank: how often would the playbook rules have raised an alert in the past?

    python calibrate.py

Needs internet (live Kraken data, no key). It replays the last ~700 completed daily candles of each coin:
for every day, each metric is ranked against that coin's history up to that day only, exactly as the
agent's market tool does. It then counts the days on which at least one rank reached ALERT_RANK and the
separate episodes (runs of consecutive breach days). Paste the table into the README ("Playbook thresholds").
"""

from datetime import datetime, timezone

import agent

START_DAY = 120  # first day that is ranked: enough history before it for a meaningful percentile


def backtest(closes):
    series, _ = agent.metric_series(closes)
    breach_days = []
    for i in range(START_DAY, len(closes)):
        ranks = agent.ranks_at(series, i)
        breach_days.append(any(rank >= agent.ALERT_RANK for rank in ranks.values()))
    episodes = sum(1 for today, before in zip(breach_days, [False] + breach_days[:-1]) if today and not before)
    return len(breach_days), sum(breach_days), episodes


def main():
    now = datetime.now(timezone.utc)
    print(f"Alert rank {agent.ALERT_RANK}, window {agent.WINDOW} days, data up to the last completed day before {now:%Y-%m-%d} UTC\n")
    print("| Coin | Days tested | Days with an alert | Share of days | Separate episodes |")
    print("|---|---|---|---|---|")
    for coin, pair in agent.WATCHLIST.items():
        try:
            stamps, closes = agent.completed_closes(agent.fetch_candles(pair), now)
        except (RuntimeError, ValueError) as error:
            print(f"| {coin} | error: {error} | | | |")
            continue
        days, breaches, episodes = backtest(closes)
        print(f"| {coin} | {days} | {breaches} | {100 * breaches / days:.1f}% | {episodes} |")


if __name__ == "__main__":
    main()
