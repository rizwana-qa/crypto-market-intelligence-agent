# Crypto Watch Playbook

Own rules for the daily crypto watch agent. Education only, not financial advice. Watchlist: BTC, ETH and XRP (USD pairs, Kraken daily candles). "Rank" means the percentile rank (0 to 100) of today's value inside that coin's own history of completed daily candles, about 700 days. A rank of 95 means today is higher than about 95 percent of that coin's own days.

## Data quality
Keywords: data, reliable, stale, missing, error, unavailable, Kraken, history, candles, gap, insufficient.

Rules:
- Use only completed UTC daily candles. The unfinished candle of today is excluded.
- If a tool returns an error, the latest completed candle is not yesterday, or the daily data has a gap, there is no reliable evidence for that coin. Do not alert on it. Say the data was unavailable. Never estimate a number.
- Fewer than 90 completed candles is too little history to rank against.

Reasons and sources:
- Unfinished candle: Kraken's OHLC endpoint returns the current, not-yet-committed candle as its last row (Kraken REST API documentation, OHLC; also recorded in the Assignment 1 README, Data source).
- 90 days: the alert rank looks at the top 5 percent of days. With 90 days that is 4 or 5 days, the least that makes a rank meaningful. Own design, simple arithmetic (5 percent of 90 is 4.5).

## Volatility rule
Keywords: volatility, swings, unusual moves, standard deviation, daily returns, spike, calm, rank.

Definition: 30-day daily volatility is the sample standard deviation (divide by n minus 1) of the last 30 daily simple returns, which need 31 closing prices. This is the same formula as Assignment 1 (analysis.py, daily_volatility).

Rule: a volatility rank of 95 or higher is unusual.

Reason: crypto prices move in bursts, so one fixed percentage would fire all the time for XRP and almost never for BTC. Ranking each coin against its own history makes the rule fair to each coin and needs no assumption about the shape of returns. Source: own design; the alert frequency is backtested by calibrate.py and reported in the README.

## Trend against the 30-day average
Keywords: trend, average, distance, above, below, mean, moving average, stretched, deviation.

Definition: distance is the last close divided by the mean of the last 30 closes, minus 1, in percent. A negative value means below the average, a positive value means above.

Rule: the rank of the absolute distance at 95 or higher is unusual. The sign says whether the coin is above or below its average.

Reason: the same as for volatility, the rank compares each coin with its own normal. The 30-day window matches the Assignment 1 window. Source: own design, Assignment 1 window; frequency backtested by calibrate.py.

## Drawdown rule
Keywords: drawdown, fall, drop, decline, peak, trough, loss, down.

Definition: the largest fall from a running peak inside the last 31 closes, in percent (a negative number). Same method as Assignment 1 (analysis.py, max_drawdown).

Rule: a drawdown depth rank of 95 or higher is unusual.

Reason: a drawdown shows damage that a single day's return hides. Ranking the depth against the coin's own 30-day drawdowns keeps the rule comparable across coins. Source: own design, Assignment 1 method; frequency backtested by calibrate.py.

## Alert rules
Keywords: alert, threshold, rank, near threshold, calm, normal, decision, when, breaches, unusual.

Rules:
1. Alert on a coin when at least one of its ranks (volatility, trend against the average, drawdown) is 95 or higher. The "breaches" field of the market summary lists them. Cite the section of every rule used.
2. Calm market: every rank is below 90. No alert.
3. Near threshold: a rank from 90 up to, but not including, 95. No alert. It may be mentioned in the reasoning as "near threshold".
4. One alert per day: one short message that covers every coin that breached.
5. A coin with a data error is never part of an alert (see Data quality).

Reasons and sources:
- 95: by construction a rank of 95 or higher is the top 5 percent of that coin's own days, about one day in 20 for each metric. A bar of 99 would almost never fire. A bar of 90 would fire about one day in 10 for each metric, and readers learn to ignore frequent alerts. The real alert frequency of the three metrics together is measured by calibrate.py (README). This is a design choice, not a market standard and not a trading signal. Source: own design, checked by calibrate.py.
- 90: a band of 5 rank points below the alert rank is called near threshold so a day just under the bar is not treated as a breach. Own convention.

## Repeat alerts
Keywords: repeat, already alerted, history, recent decisions, duplicate, again, yesterday, memory.

Rule: do not alert on a coin again if get_recent_decisions shows an alert for that coin within the last 7 decisions. A different coin that has not been alerted may still alert.

Reason: a spike stays inside the 30-day window for 30 days, so its rank stays high for days after the event and would repeat the same news every morning. Seven decisions (one per day, so one week) is long enough that the event is no longer news and short enough that a new event in the same coin is not hidden for long. Source: window arithmetic from the 30-day window of Assignment 1; the value 7 is own design.

## Wording rules
Keywords: wording, message, alert text, advice, language, describe, format, length, short, words.

Rules:
- Describe what the numbers show: name the coin, the metric, the value and the rank.
- At most 280 characters. Plain words.
- No advice and no prediction. Never tell anyone to act. Do not use the words buy, sell, long, short, hold, entry, exit, accumulate, should or recommend.
- Use only numbers returned by the tools. If data was unavailable, say so and give no number for that coin.

Reasons and sources: this project is for education only and must never tell anyone to buy or sell (Assignment 2 and 3 brief, rule 4 and Step 7). 280 characters keeps the email readable at a glance (own convention).
