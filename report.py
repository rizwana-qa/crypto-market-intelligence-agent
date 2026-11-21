"""Executive intelligence brief for the Crypto Watch Agent.

One self-contained HTML page (charts use plotly.js from a CDN) and a short HTML email that carries the same
headlines. It reuses the agent's own functions, so every number is the number the agent saw.

Who writes what, stated on the page itself:
  - Computed by code: prices, the three metrics, the ranks, the headlines and the monitoring triggers.
  - Written by AI: the analysis text, which is the agent's latest decision read back from the Sheet.
  - Checked by code: the guardrails the decision passed (valid playbook sources, no advice wording, market data).

There is no price forecast and no recommendation in this report, on purpose: the project is for education only.

Usage:
  python report.py                      # live Kraken data + the Sheet history (needs SHEET_URL for the AI analysis)
  python report.py --email              # also email it through the Apps Script web app (needs the updated Code.gs)
  python report.py --demo               # synthetic data, clearly labelled, to preview the layout offline
"""
import argparse
import html
import json
import math
import sys
from datetime import datetime, timezone

import agent
import sources

PLOTLY_URL = "https://cdn.plot.ly/plotly-2.35.2.min.js"
LABELS = {"volatility": "30-day volatility", "distance": "Distance from 30-day average", "drawdown": "30-day max drawdown"}
COLORS = {"BTC": "#f59e0b", "ETH": "#6366f1", "XRP": "#14b8a6"}
CHART_DAYS = 90      # length of the price chart
RANK_DAYS = 60       # length of the rank history chart
NEAR_RANK = 90.0     # same band as playbook.md, "Near threshold"
DECISION_ROWS = 10
MONITORING_QUESTION = (
    "Across BTC/USD, ETH/USD and XRP/USD, what has changed in the latest completed daily market data, which movements or relationships "
    "are unusual compared with each asset's historical behaviour, what do trends, volatility, volume, relative strength and cross-asset "
    "relationships indicate, and what evidence-based forward scenarios, uncertainty levels, risk flags and invalidation conditions should be monitored?"
)


# ------------------------------------------------------------------------------------------------
# 1. Data
# ------------------------------------------------------------------------------------------------
def value_at_rank(values, rank):
    """The smallest value in `values` whose own percentile rank is at least `rank` (None for an empty list)."""
    for value in sorted(set(values)):
        if agent.percentile_rank(values, value) >= rank:
            return value
    return None


def _volatility_level(window_returns, target, direction, last_close):
    """The next-day close that pushes the 30-day volatility to `target`, found by bisection (None if even a 50% move cannot)."""
    def spread(r):
        return agent.statistics.stdev(window_returns + [r])
    start = agent.statistics.fmean(window_returns)
    if spread(start) >= target:
        return "already"
    edge = 0.5 * direction
    if spread(edge) < target:
        return None
    low, high = start, edge
    for _ in range(60):
        middle = (low + high) / 2
        low, high = (middle, high) if spread(middle) < target else (low, middle)
    return last_close * (1 + high)


def trigger_levels(closes):
    """The next-day closes at which each metric would reach the alert rank, from the coin's own history.
    These are conditions to monitor. They are not forecasts and not price targets."""
    series, returns = agent.metric_series(closes)
    last = closes[-1]
    k = agent.WINDOW - 1
    levels = {"history_days": len(closes), "distance": None, "drawdown": None, "volatility": None}
    # A flat history has no top 5 percent, so there is no level to give (value_at_rank returns None).
    d = value_at_rank([abs(v) for v in series["distance"] if v is not None], agent.ALERT_RANK)
    if d is not None:
        total = sum(closes[-k:])
        levels["distance"] = {"value_pct": d * 100, "up": (1 + d) * total / (k - d), "down": (1 - d) * total / (k + d)}
    depth = value_at_rank([v for v in series["drawdown"] if v is not None], agent.ALERT_RANK)
    if depth is not None:
        window = closes[-agent.WINDOW:]
        already = -agent.max_drawdown(window) >= depth
        levels["drawdown"] = {"value_pct": depth * 100, "down": "already" if already else max(window) * (1 - depth)}
    vol = value_at_rank([v for v in series["volatility"] if v is not None], agent.ALERT_RANK)
    if vol is not None:
        recent = returns[-k:]
        levels["volatility"] = {"value_pct": vol * 100, "up": _volatility_level(recent, vol, 1, last),
                                "down": _volatility_level(recent, vol, -1, last)}
    return levels


# The base rate is computed in agent.py now (the get_alert_history_stats tool uses it); the brief shows the same numbers.
episodes = agent.episodes
base_rate = agent.base_rate


def analyse(coin, stamps, closes, volumes=None):
    """The agent's own summary plus the series the charts need."""
    summary = agent.summarize(coin, stamps, closes)
    if volumes is not None:
        agent.add_context(summary, closes, volumes)
    series, _ = agent.metric_series(closes)
    n = len(closes)
    start = max(0, n - CHART_DAYS)
    base = closes[start]
    rank_dates, rank_top = [], []
    for i in range(max(agent.WINDOW, n - RANK_DAYS), n):
        ranks = agent.ranks_at(series, i)
        rank_dates.append(agent.format_date(stamps[i]))
        rank_top.append(round(max(ranks.values()), 1))
    return {
        "coin": coin,
        "summary": summary,
        "base_rate": base_rate(stamps, closes),
        "dates": [agent.format_date(t) for t in stamps[start:]],
        "indexed": [round(c / base * 100, 2) for c in closes[start:]],
        "rank_dates": rank_dates,
        "rank_top": rank_top,
        "levels": trigger_levels(closes),
    }


def cross_from(series):
    """Relationships between the coins that have data, or an error note."""
    if len(series) < 2:
        return {"error": "at least two coins with data are needed for relative strength and correlation"}
    try:
        return agent.cross_asset_summary(series)
    except (ValueError, agent.statistics.StatisticsError) as error:
        return {"error": str(error)}


def gather(now):
    coins, series = [], {}
    for coin in agent.WATCHLIST:
        try:
            stamps, closes, volumes = agent.load_coin(coin, now)
            coins.append(analyse(coin, stamps, closes, volumes))
            series[coin] = (stamps, closes)
        except (RuntimeError, ValueError, agent.statistics.StatisticsError) as error:
            coins.append({"coin": coin, "error": str(error)})
    return coins, cross_from(series)


def gather_context():
    """The same market context the agent reads (free sources, fail-soft). Never raises."""
    return agent.tool_market_context()


def fetch_decisions():
    try:
        body = agent.sheet_post({"action": "history", "limit": DECISION_ROWS, "detail": True})
    except agent.AgentError as error:
        return [], f"The decision history could not be read from the Sheet: {error}"
    return body.get("decisions", []), ""


def demo_series():
    """Synthetic closes and volumes, only to preview the layout. The page says so in its banner."""
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    today = int(now.timestamp()) // agent.SECONDS_PER_DAY * agent.SECONDS_PER_DAY
    out = {}
    for index, (coin, start_price) in enumerate((("BTC", 90000.0), ("ETH", 3100.0), ("XRP", 2.4))):
        n = 400
        stamps = [today - (n - i) * agent.SECONDS_PER_DAY for i in range(n)]
        closes, volumes = [], []
        for i in range(n):
            wave = math.sin(i / (9 + 3 * index)) * 0.012 + math.sin(i / 3.1 + index) * 0.006
            drop = -0.0035 * max(0, i - (n - 14)) if coin in ("BTC", "XRP") else 0.0
            closes.append(start_price * (1 + 0.0004 * i + wave + drop))
            volumes.append(1000.0 * (1 + 0.3 * math.sin(i / 5.0 + index)) * (2.2 if i >= n - 2 and coin == "BTC" else 1.0))
        out[coin] = (stamps, closes, volumes)
    return out


def demo_coins():
    return [analyse(coin, stamps, closes, volumes) for coin, (stamps, closes, volumes) in demo_series().items()]


def demo_cross():
    return cross_from({coin: (stamps, closes) for coin, (stamps, closes, _) in demo_series().items()})


def demo_context(coins=None):
    """Sample context for the layout preview. Prices follow the synthetic closes so the page does not contradict itself."""
    import scenarios                                   # only the preview needs the fake builder
    closes = {c["coin"]: c["summary"]["last_close"] for c in (coins or demo_coins()) if "error" not in c}
    return scenarios.fake_context(btc_mid=closes.get("BTC", 67210.5), mids=closes, iv_rank=96.5, btc_price_gap=0.3)


def demo_decisions():
    return [{
        "time": "2026-11-20T09:17:41Z", "version": "Agent loop (demo)", "alert": True, "coins": ["BTC", "XRP"],
        "message": "Demo text: BTC and XRP are above the alert rank for drawdown and distance from the 30-day average.",
        "sources": ["Alert rules", "Drawdown rule"], "blocked_reason": "",
        "reasoning": "Demo text: the ranks in the market summary are at or above 95 for these coins.",
    }], ""


# ------------------------------------------------------------------------------------------------
# 2. Facts the report states (all computed by code)
# ------------------------------------------------------------------------------------------------
def good(data):
    return [c for c in data["coins"] if "error" not in c]


def breaching(data):
    return [c for c in good(data) if c["summary"]["breaches"]]


def near_threshold(data):
    found = []
    for c in good(data):
        for metric in agent.METRICS:
            rank = c["summary"]["ranks"][metric]
            if NEAR_RANK <= rank < agent.ALERT_RANK:
                found.append((c["coin"], metric, rank))
    return found


def metric_value(summary, metric):
    if metric == "volatility":
        return f"{summary['volatility_30d_daily_pct']:.2f}% daily"
    if metric == "distance":
        return f"{summary['dist_from_30d_avg_pct']:+.2f}%"
    return f"{summary['max_drawdown_30d_pct']:.2f}%"


def status_of(data):
    if not good(data):
        return "NO DATA", "grey", "Market data unavailable for every coin"
    alerted = [c["coin"] for c in breaching(data)]
    if alerted:
        return "ALERT", "red", "At the alert rank: " + ", ".join(alerted)
    return "CALM", "green", "No coin at the alert rank"


def headlines(data):
    lines = []
    for c in breaching(data):
        s = c["summary"]
        parts = [f"{LABELS[m].lower()} {metric_value(s, m)} (rank {s['ranks'][m]})" for m in s["breaches"]]
        lines.append(f"{c['coin']} is unusual compared with its own history: " + "; ".join(parts) + ".")
    if not breaching(data) and good(data):
        lines.append(f"No coin reached the alert rank of {agent.ALERT_RANK:g} on volatility, distance from the 30-day average or drawdown.")
    for coin, metric, rank in near_threshold(data):
        lines.append(f"{coin} is near the threshold on {LABELS[metric].lower()} (rank {rank}, alert rank {agent.ALERT_RANK:g}).")
    for c in good(data):
        ctx = c["summary"].get("context", {})
        if "volume" in c["summary"].get("flags", []):
            lines.append(f"{c['coin']} volume is unusual: {ctx['volume_ratio_30d']:.2f} times its 30-day average (rank {ctx['volume_rank']}). "
                         "Volume is context and never raises an alert on its own.")
    cross = data.get("cross") or {}
    if cross.get("flags"):
        lines.append("Unusual relationships, context only: " + "; ".join(cross["flags"]) + " (each at an extreme of its own history, see the relationships table).")
    context = data.get("context") or {}
    if context.get("flags"):
        lines.append("Market context, context only and never an alert on its own: " + "; ".join(context["flags"]) + " (see the market context table).")
    broken = [c for c in data["coins"] if "error" in c]
    if broken:
        lines.append("Data unavailable, so no statement is made for: " + ", ".join(c["coin"] for c in broken) + ".")
    else:
        lines.append(f"Data is complete and current for all {len(data['coins'])} coins.")
    return lines


def triggers(data):
    lines = []
    breached = breaching(data)
    if breached:
        who = "; ".join(f"{c['coin']} ({', '.join(LABELS[m].lower() for m in c['summary']['breaches'])})" for c in breached)
        lines.append(f"{who}: a spike stays inside the 30-day window for 30 days, so these ranks can stay high for days. "
                     f"The agent does not repeat an alert for a coin that is in its last {agent.HISTORY_LIMIT} decisions.")
    for coin, metric, rank in near_threshold(data):
        lines.append(f"{coin}: {LABELS[metric].lower()} is {agent.ALERT_RANK - rank:.1f} rank points below the alert rank of {agent.ALERT_RANK:g}.")
    broken = [c["coin"] for c in data["coins"] if "error" in c]
    if broken:
        lines.append("Unavailable data excludes a coin from alerts until Kraken returns complete, current candles: " + ", ".join(broken) + ".")
    lines.append("Next scheduled agent run: every day at 05:17 UTC (09:17 in Dubai).")
    return lines


def latest_decision(data):
    return data["decisions"][-1] if data["decisions"] else None


def agreement(data):
    decision = latest_decision(data)
    if decision is None or not good(data):
        return None
    ai = set(decision.get("coins") or []) if decision.get("alert") else set()
    code = {c["coin"] for c in breaching(data)}
    if ai == code:
        return True, "The AI's latest decision and the code's own rank check name the same coins."
    return False, (f"The AI's latest decision names {', '.join(sorted(ai)) or 'no coin'}; today's rank check names "
                   f"{', '.join(sorted(code)) or 'no coin'}. They can differ when the decision was made on an earlier day.")


# ------------------------------------------------------------------------------------------------
# 3. Charts (plotly.js specs written as plain data)
# ------------------------------------------------------------------------------------------------
BASE_LAYOUT = {
    "margin": {"l": 52, "r": 16, "t": 12, "b": 40}, "height": 330,
    "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "Inter, Segoe UI, Roboto, Arial, sans-serif", "size": 12, "color": "#334155"},
    "legend": {"orientation": "h", "y": -0.18}, "hovermode": "x unified",
    "xaxis": {"gridcolor": "#eef2f7"}, "yaxis": {"gridcolor": "#e8edf4", "zeroline": False},
}


def layout(**changes):
    merged = json.loads(json.dumps(BASE_LAYOUT))
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def threshold_lines(y_value, color, label=None, dash="dash"):
    shape = {"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": y_value, "y1": y_value,
             "line": {"color": color, "width": 1.5, "dash": dash}}
    note = None
    if label:
        note = {"xref": "paper", "x": 1, "y": y_value, "xanchor": "right", "yanchor": "bottom", "showarrow": False,
                "text": label, "font": {"size": 11, "color": color}}
    return shape, note


HEAT_COLUMNS = [("Volatility", "volatility"), ("Vs 30-day<br>average", "distance"), ("Max<br>drawdown", "drawdown"),
                ("Volume<br>(context)", "volume"), ("Relative<br>strength<br>(context)", "strength")]


def rank_heatmap(data, ok):
    """Coins by measures. The colour shows how extreme the rank is: grey is ordinary, amber is 90 up to 95, red is 95 or higher.
    Relative strength can be unusual at either end, so its colour uses the distance from the middle and its text is the rank."""
    strength = {x["coin"]: x["rank"] for x in (data.get("cross") or {}).get("relative_strength", [])}
    z, text = [], []
    for c in ok:
        s, ctx = c["summary"], c["summary"].get("context", {})
        values = [s["ranks"]["volatility"], s["ranks"]["distance"], s["ranks"]["drawdown"], ctx.get("volume_rank"), strength.get(c["coin"])]
        text.append(["n/a" if v is None else f"{v:.1f}" for v in values])
        z.append([None if v is None else (max(v, 100.0 - v) if i == 4 else v) for i, v in enumerate(values)])
    scale = [[0, "#f1f5f9"], [0.89, "#e2e8f0"], [0.9, "#fde68a"], [0.949, "#fcd34d"], [0.95, "#f87171"], [1, "#dc2626"]]
    spec = {"type": "heatmap", "x": [name for name, _ in HEAT_COLUMNS], "y": [c["coin"] for c in ok], "z": z, "text": text,
            "texttemplate": "%{text}", "textfont": {"size": 15}, "zmin": 50, "zmax": 100, "colorscale": scale, "showscale": False, "xgap": 4, "ygap": 4,
            "hovertemplate": "%{y}, %{x}: rank %{text}<extra></extra>"}
    line = {"type": "line", "xref": "x", "yref": "paper", "x0": 2.5, "x1": 2.5, "y0": 0, "y1": 1, "line": {"color": "#0f172a", "width": 1.5, "dash": "dot"}}
    notes = [{"x": 1, "y": 1.12, "xref": "x", "yref": "paper", "text": "Can raise an alert", "showarrow": False, "font": {"size": 12, "color": "#991b1b"}},
             {"x": 3.5, "y": 1.12, "xref": "x", "yref": "paper", "text": "Context only", "showarrow": False, "font": {"size": 12, "color": "#475569"}}]
    return {"data": [spec], "layout": layout(hovermode="closest", yaxis={"autorange": "reversed"}, xaxis={"side": "bottom", "tickfont": {"size": 11}, "tickangle": 0},
                                             shapes=[line], annotations=notes, margin={"l": 50, "r": 10, "t": 40, "b": 70})}


def charts(data):
    ok = good(data)
    if not ok:
        return {}
    price = [{"type": "scatter", "mode": "lines", "name": c["coin"], "x": c["dates"], "y": c["indexed"],
              "line": {"color": COLORS.get(c["coin"], "#64748b"), "width": 2.5}} for c in ok]
    ranks = rank_heatmap(data, ok)
    alert_shape, alert_note = threshold_lines(agent.ALERT_RANK, "#dc2626", f"Alert rank {agent.ALERT_RANK:g}")
    history = [{"type": "scatter", "mode": "lines", "name": c["coin"], "x": c["rank_dates"], "y": c["rank_top"],
                "line": {"color": COLORS.get(c["coin"], "#64748b"), "width": 2.5}} for c in ok]
    return {
        "price": {"data": price, "layout": layout(yaxis={"title": {"text": "Index, 90 days ago = 100"}})},
        "ranks": ranks,
        "history": {"data": history, "layout": layout(
            yaxis={"range": [0, 105], "title": {"text": "Highest of the 3 ranks"}},
            shapes=[alert_shape], annotations=[alert_note])},
    }


# ------------------------------------------------------------------------------------------------
# 4. The page
# ------------------------------------------------------------------------------------------------
CSS = """
:root{--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--bg:#f1f5f9;--card:#ffffff;--navy:#0b1f3a;--teal:#0f766e;
--red:#dc2626;--amber:#d97706;--green:#16a34a;--violet:#6d28d9}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Segoe UI",Roboto,Arial,sans-serif;line-height:1.5}
.banner{position:relative;overflow:hidden;background:linear-gradient(120deg,#0b1f3a 0%,#0f3d5e 55%,#0f766e 100%);color:#fff;padding:38px 24px 44px}
.banner svg{position:absolute;right:0;bottom:0;width:60%;height:100%;opacity:.35}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px;position:relative}
.banner .wrap{padding:0}
.eyebrow{letter-spacing:.14em;font-size:12px;font-weight:700;color:#99f6e4}
h1{margin:10px 0 8px;font-size:30px;line-height:1.2;max-width:760px}
.meta{font-size:13px;color:#cbd5e1}
.demo{display:inline-block;margin-top:12px;background:#fde68a;color:#78350f;font-weight:700;padding:5px 12px;border-radius:8px;font-size:13px}
.pill{display:inline-block;margin-top:16px;padding:7px 16px;border-radius:999px;font-weight:700;font-size:14px}
.pill.red{background:#fee2e2;color:#991b1b}.pill.green{background:#dcfce7;color:#166534}.pill.grey{background:#e2e8f0;color:#334155}
main.wrap{margin-top:-24px;padding-bottom:40px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:18px}
.kpi,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 1px 2px rgba(15,23,42,.05)}
.kpi{padding:16px 18px}.kpi .k{font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em}
.kpi .v{font-size:26px;font-weight:800;margin-top:4px}.kpi .s{font-size:12px;color:var(--muted);margin-top:2px}
.card{padding:20px 22px;margin-bottom:18px}
.card h2{margin:0 0 4px;font-size:18px}.card .sub{color:var(--muted);font-size:13px;margin:0 0 14px}
.chip{display:inline-block;font-size:11px;font-weight:700;border-radius:6px;padding:2px 8px;margin-left:8px;vertical-align:middle;letter-spacing:.03em}
.chip.code{background:#dbeafe;color:#1e40af}.chip.ai{background:#ede9fe;color:#5b21b6}.chip.check{background:#dcfce7;color:#166534}.chip.watch{background:#fef3c7;color:#92400e}
ul.clean{margin:0;padding-left:20px}ul.clean li{margin:7px 0}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding:8px 10px;border-bottom:2px solid var(--line)}
td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.rank{display:inline-block;min-width:44px;text-align:center;border-radius:6px;padding:1px 6px;font-weight:700;font-size:13px}
.rank.hi{background:#fee2e2;color:#991b1b}.rank.mid{background:#fef3c7;color:#92400e}.rank.lo{background:#f1f5f9;color:#475569}
.badge{font-weight:700;font-size:12px;border-radius:6px;padding:2px 8px}
.badge.ALERT{background:#fee2e2;color:#991b1b}.badge.NEAR{background:#fef3c7;color:#92400e}.badge.CALM{background:#dcfce7;color:#166534}.badge.NODATA{background:#e2e8f0;color:#334155}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:18px}
.chart{min-height:330px;color:var(--muted);font-size:13px}
.ai{border-left:6px solid var(--violet);background:linear-gradient(90deg,#faf5ff,#ffffff 40%)}
.ai blockquote{margin:10px 0;padding:12px 16px;background:#f5f3ff;border-radius:10px;font-size:16px}
.facts{display:flex;flex-wrap:wrap;gap:8px 22px;font-size:13px;color:var(--muted);margin:8px 0}
.q{margin:6px 0 12px;padding:14px 18px;border-left:6px solid var(--teal);background:#f0fdfa;border-radius:10px;font-size:17px;font-weight:600}
.muted{color:var(--muted);font-size:12px}
.legend{display:flex;flex-wrap:wrap;gap:10px 20px;font-size:13px;color:var(--muted)}
footer{font-size:12px;color:var(--muted);padding:6px 4px 30px}
@media(max-width:640px){h1{font-size:24px}.grid2{grid-template-columns:1fr}}
@media print{body{background:#fff}.banner{-webkit-print-color-adjust:exact;print-color-adjust:exact}.card,.kpi{box-shadow:none;break-inside:avoid}}
"""

BANNER_ART = ('<svg viewBox="0 0 600 220" preserveAspectRatio="none" aria-hidden="true"><g fill="none" stroke="#99f6e4" stroke-width="2">'
              '<polyline points="0,170 60,150 110,160 170,110 230,125 290,80 350,95 410,50 470,70 540,25 600,40"/>'
              '<polyline stroke="#fbbf24" stroke-opacity=".7" points="0,190 70,175 130,182 190,150 250,160 310,125 370,135 430,100 490,112 550,80 600,88"/>'
              '</g><g fill="#99f6e4" fill-opacity=".5"><rect x="90" y="120" width="9" height="46"/><rect x="210" y="96" width="9" height="40"/>'
              '<rect x="330" y="70" width="9" height="44"/><rect x="450" y="45" width="9" height="42"/></g></svg>')


def esc(value):
    return html.escape(str(value), quote=True)


def rank_cell(rank):
    cls = "hi" if rank >= agent.ALERT_RANK else "mid" if rank >= NEAR_RANK else "lo"
    return f'<span class="rank {cls}">{rank:.1f}</span>'


def coin_status(summary):
    if summary["breaches"]:
        return "ALERT"
    if any(NEAR_RANK <= r < agent.ALERT_RANK for r in summary["ranks"].values()):
        return "NEAR"
    return "CALM"


def snapshot_table(data):
    rows = []
    for c in data["coins"]:
        if "error" in c:
            rows.append(f'<tr><td><b>{esc(c["coin"])}</b></td><td colspan="5">Data unavailable: {esc(c["error"])}. No statement is made for this coin.</td>'
                        f'<td><span class="badge NODATA">NO DATA</span></td></tr>')
            continue
        s = c["summary"]
        status = coin_status(s)
        rows.append(
            f'<tr><td><b>{esc(c["coin"])}</b><br><span style="color:#64748b;font-size:12px">{esc(s["pair"])}</span></td>'
            f'<td class="num">${s["last_close"]:,.4f}</td><td class="num">{s["daily_return_pct"]:+.2f}%</td>'
            f'<td class="num">{s["volatility_30d_daily_pct"]:.2f}% {rank_cell(s["ranks"]["volatility"])}</td>'
            f'<td class="num">{s["dist_from_30d_avg_pct"]:+.2f}% {rank_cell(s["ranks"]["distance"])}</td>'
            f'<td class="num">{s["max_drawdown_30d_pct"]:.2f}% {rank_cell(s["ranks"]["drawdown"])}</td>'
            f'<td><span class="badge {status}">{status}</span></td></tr>')
    head = ('<tr><th>Coin</th><th class="num">Last close</th><th class="num">Day</th><th class="num">Volatility (rank)</th>'
            '<th class="num">Vs 30-day average (rank)</th><th class="num">Max drawdown (rank)</th><th>Status</th></tr>')
    return f"<table>{head}{''.join(rows)}</table>"


def question_section():
    coins = " &middot; ".join(esc(c) for c in agent.WATCHLIST)
    return (
        '<section class="card"><h2>The question this agent answers</h2>'
        f'<blockquote class="q">{esc(MONITORING_QUESTION)}</blockquote>'
        '<p class="sub"><b>What the agent answers each day today:</b> ' + esc(agent.QUESTION) + ' '
        'Trend, volume, relative strength and the correlation between coins are context: the agent describes them and they never raise an alert on their own. '
        'Forward scenarios are shown below as conditions to monitor, not predictions. The agent makes no forecast, by design.</p>'
        f'<div class="facts"><span>Watchlist: {coins} (USD pairs)</span><span>Data: Kraken daily candles, latest completed UTC day</span>'
        f'<span>Cadence: once a day, 05:17 UTC</span><span>Output: one Sheet row, and an email when there is an alert</span></div></section>')


def scenario_rows():
    """What the agent does in each situation. Each row has a scenario test in scenarios.py, so the rules are checked, not only described."""
    return [
        ("Calm market", f"Every rank is below {NEAR_RANK:g}.", "No alert."),
        ("Unusual move", f"Any rank of volatility, distance from the 30-day average or drawdown is {agent.ALERT_RANK:g} or higher.",
         f"One brief alert (at most {agent.MAX_MESSAGE_CHARS} characters) that names every coin involved and cites the playbook rule."),
        ("Near the threshold", f"A rank from {NEAR_RANK:g} up to, but not including, {agent.ALERT_RANK:g}.", "No alert. It may be mentioned as near threshold."),
        ("Already alerted", f"The coin is in the agent's last {agent.HISTORY_LIMIT} saved decisions.", "No repeat alert for that coin."),
        ("Data problem", f"Kraken is unreachable, a candle is stale or missing, or fewer than {agent.MIN_HISTORY_DAYS} days exist.",
         "No alert for that coin. The agent says the data was unavailable and never estimates a number."),
        ("Instructions in the data", "Text in the data, such as forum chatter, pushes the agent to give trading instructions.", "Blocked by code. Alerts only describe numbers and never tell anyone what to do."),
        ("One coin down", "One coin has a data problem while the others are calm.", "No alert. The failure is reported, and the other coins are still assessed."),
        ("Context without a price move", "Volume, relative strength or a correlation is unusual, but no price rank is at the alert rank.",
         "No alert. The context is described in the brief and never raises an alert on its own."),
        ("Several coins at once", "Two or more coins reach the alert rank on the same day.", "One brief alert that names every coin involved. A coin that is not at the alert rank is never named."),
    ]


def scenario_section():
    rows = "".join(f"<tr><td><b>{esc(a)}</b></td><td>{esc(b)}</td><td>{esc(c)}</td></tr>" for a, b, c in scenario_rows())
    return ('<section class="card"><h2>What the agent does in each situation<span class="chip check">EACH ROW IS A SCENARIO TEST</span></h2>'
            '<p class="sub">The AI recommends and the code decides. These rules are in the playbook, and every row has a test that runs on each code change.</p>'
            f'<table><tr><th>Situation</th><th>Trigger</th><th>What the agent does</th></tr>{rows}</table></section>')


def ai_section(data):
    decision = latest_decision(data)
    chips = '<span class="chip ai">WRITTEN BY AI</span>'
    if decision is None:
        note = data.get("decisions_note") or "No decision has been saved to the Sheet yet."
        return (f'<section class="card ai"><h2>AI analysis{chips}</h2><p class="sub">{esc(note)}</p></section>')
    blocked = str(decision.get("blocked_reason") or "")
    verdict = (f'<span class="chip check">PASSED THE CODE GUARDRAILS</span>' if not blocked
               else f'<span class="chip watch">BLOCKED BY A GUARDRAIL</span>')
    message = decision.get("message") or "(no message: the agent saw nothing to alert on, or a guardrail removed it)"
    reasoning = decision.get("reasoning") or "Not available. Update apps_script/Code.gs and redeploy it to include the AI's reasoning here."
    sources = ", ".join(decision.get("sources") or []) or "none"
    cross = agreement(data)
    cross_html = ""
    if cross is not None:
        mark = "Consistent" if cross[0] else "Different"
        cross_html = f'<p><b>Cross-check by code: {mark}.</b> {esc(cross[1])}</p>'
    return (
        f'<section class="card ai"><h2>AI analysis{chips}{verdict}</h2>'
        f'<p class="sub">The agent is an AI model that recommends; the code decides what is allowed through. This is its latest saved decision.</p>'
        f'<blockquote>{esc(message)}</blockquote>'
        f'<p><b>Reasoning given by the AI:</b> {esc(reasoning)}</p>'
        f'<p><b>Playbook rules cited:</b> {esc(sources)}</p>'
        f'{cross_html}'
        + (f'<p><b>Guardrail result:</b> {esc(blocked)}</p>' if blocked else '')
        + f'<div class="facts"><span>Model: {esc(agent.MODEL)} on Groq</span><span>Version: {esc(decision.get("version") or "unknown")}</span>'
          f'<span>Decided: {esc(decision.get("time") or "unknown")}</span><span>Alert: {"yes" if decision.get("alert") else "no"}</span></div></section>')


def decision_table(data):
    rows = []
    for d in reversed(data["decisions"]):
        coins = ", ".join(d.get("coins") or []) or "none"
        message = str(d.get("message") or "")
        short = message if len(message) <= 150 else message[:147] + "..."
        rows.append(f'<tr><td>{esc(d.get("time", ""))}</td><td>{"<b>ALERT</b>" if d.get("alert") else "no alert"}</td>'
                    f'<td>{esc(coins)}</td><td>{esc(short)}</td><td>{esc(", ".join(d.get("sources") or []))}</td></tr>')
    if not rows:
        return '<p class="sub">No saved decisions yet.</p>'
    return ('<table><tr><th>Time (UTC)</th><th>Result</th><th>Coins</th><th>Message</th><th>Sources</th></tr>' + "".join(rows) + "</table>")


def rank_cell_two_sided(rank):
    cls = "hi" if rank >= agent.ALERT_RANK or rank <= agent.UNUSUAL_LOW else "mid" if rank >= NEAR_RANK or rank <= 100 - NEAR_RANK else "lo"
    return f'<span class="rank {cls}">{rank:.1f}</span>'


def context_section(data):
    cross = data.get("cross") or {}
    strength = {x["coin"]: x for x in cross.get("relative_strength", [])}
    rows = []
    for c in good(data):
        ctx = c["summary"].get("context")
        if not ctx:
            continue
        volume = (f'{ctx["volume_ratio_30d"]:.2f}x {rank_cell(ctx["volume_rank"])}' if "volume_rank" in ctx else "n/a")
        rs = strength.get(c["coin"])
        rs_text = (f'{rs["vs_group_pct"]:+.2f} pts {rank_cell_two_sided(rs["rank"])}' if rs else "n/a")
        rows.append(f'<tr><td><b>{esc(c["coin"])}</b></td><td>{esc(ctx["trend"])}</td>'
                    f'<td class="num">{ctx["ret_30d_pct"]:+.2f}% / {ctx["ret_60d_pct"]:+.2f}%</td>'
                    f'<td class="num">{volume}</td><td class="num">{rs_text}</td></tr>')
    if not rows:
        return ""
    table = ('<table><tr><th>Coin</th><th>Trend</th><th class="num">Change 30d / 60d</th><th class="num">Volume vs 30-day average (rank)</th>'
             '<th class="num">Relative strength vs group (rank)</th></tr>' + "".join(rows) + "</table>")
    pairs = ""
    if cross.get("correlations"):
        flagged = set(cross.get("flags", []))
        body = "".join(f'<tr><td><b>{esc(x["pair"])}</b></td><td class="num">{x["corr_30d"]:+.2f}</td><td class="num">{rank_cell_two_sided(x["rank"])}</td>'
                       f'<td>{"unusual" if "correlation " + x["pair"] in flagged else "within its normal range"}</td></tr>' for x in cross["correlations"])
        pairs = ('<p class="sub" style="margin-top:14px"><b>How the coins move together</b> (correlation of the last 30 daily returns, ranked against the pair\'s own history; '
                 'near 100 is unusually tight, near 0 unusually loose).</p>'
                 f'<table><tr><th>Pair</th><th class="num">Correlation</th><th class="num">Rank</th><th>Reading</th></tr>{body}</table>')
    elif cross.get("error"):
        pairs = f'<p class="sub">Relationships unavailable: {esc(cross["error"])}.</p>'
    return ('<section class="card"><h2>Context: trend, volume and relationships<span class="chip code">COMPUTED BY CODE</span></h2>'
            '<p class="sub">These describe the past and never raise an alert on their own. Volume is the coin\'s traded volume on Kraken, not market-wide volume. '
            f'Relative strength is a coin\'s 30-day return minus the group average.</p>{table}{pairs}</section>')


def _usd(value):
    """$14.2M, $6.8B, $950K: the dollar figures of the order book and open interest are read at a glance."""
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            return f"${value / limit:,.1f}{suffix}"
    return f"${value:,.0f}"


def _missing(piece, fallback="unavailable"):
    reason = piece.get("unavailable", fallback) if isinstance(piece, dict) else fallback
    return f'<span class="muted">unavailable: {esc(reason)}</span>'


def market_context_section(data):
    """What the other free sources say about each coin today. Description only: none of it raises an alert."""
    context = data.get("context") or {}
    if "error" in context or not context.get("coins"):
        if context.get("error"):
            return f'<section class="card"><h2>Market context<span class="chip code">COMPUTED BY CODE</span></h2><p class="sub">Unavailable: {esc(context["error"])}.</p></section>'
        return ""
    rows = []
    for coin, piece in context["coins"].items():
        liq, der, iv, wide, check = (piece.get(k) for k in ("liquidity", "derivatives", "implied_volatility", "market_wide", "price_check"))
        if sources.is_data(liq):
            liq_html = (f'spread {liq["spread_pct"]:.4f}%<br><span class="muted">bid {_usd(liq["bid_depth_usd"])} / '
                        f'ask {_usd(liq["ask_depth_usd"])} within {liq["depth_band_pct"]:g}% of the price</span>')
        else:
            liq_html = _missing(liq)
        if sources.is_data(der):
            oi = _usd(der["open_interest_usd_approx"]) if der.get("open_interest_usd_approx") else f'{der["open_interest"]:,.0f} contracts'
            der_html = (f'{der["funding_rate_pct_per_hour"]:+.4f}% per hour<br><span class="muted">{esc(der["funding_pays"])}, open interest {oi}</span>')
        else:
            der_html = _missing(der)
        if sources.is_data(iv):
            rank = rank_cell(iv["rank_1y"]) if iv.get("rank_1y") is not None else f'<span class="muted">no rank yet ({iv["history_days"]} days)</span>'
            iv_html = f'{iv["implied_vol_pct"]:.1f}% annualised<br>{rank} <span class="muted">vs past year</span>'
        else:
            iv_html = _missing(iv)
        if sources.is_data(wide) and "market_volume_rank" in wide:
            wide_html = (f'{_usd(wide["volume_24h_usd"])} in 24h<br>{wide["market_volume_ratio_30d"]:.2f}x its 30-day average '
                         f'{rank_cell(wide["market_volume_rank"])}')
        elif sources.is_data(wide):
            wide_html = f'{_usd(wide["volume_24h_usd"])} in 24h<br>{_missing({"unavailable": wide.get("volume_history", "history unavailable")})}'
        else:
            wide_html = _missing(wide)
        if sources.is_data(check):
            tone = "lo" if check["agree"] else "hi"
            check_html = (f'<span class="rank {tone}">{check["difference_pct"]:+.2f}%</span> '
                          f'<span class="muted">{"agree" if check["agree"] else "differ"}</span><br>'
                          f'<span class="muted">Kraken {_price(check["kraken_mid_usd"])} vs CoinGecko {_price(check["coingecko_price_usd"])}</span>')
        else:
            check_html = _missing(check)
        rows.append(f'<tr><td><b>{esc(coin)}</b></td><td>{liq_html}</td><td>{der_html}</td><td>{iv_html}</td><td>{wide_html}</td><td>{check_html}</td></tr>')
    table = ('<table><tr><th>Coin</th><th>Liquidity (Kraken order book)</th><th>Funding and open interest (Kraken Futures)</th>'
             '<th>Implied volatility (Deribit DVOL)</th><th>Market-wide volume (CoinGecko)</th><th>Price check</th></tr>' + "".join(rows) + "</table>")
    fng = context.get("sentiment")
    if sources.is_data(fng):
        mood = (f'<p class="sub" style="margin-top:14px"><b>Fear and greed index (whole market):</b> {fng["value"]} of 100, {esc(fng["label"])}; '
                f'{fng["change_vs_yesterday"]:+d} since yesterday, 30-day mean {fng["mean_prior_30d"]:.0f}.</p>')
    else:
        mood = f'<p class="sub" style="margin-top:14px"><b>Fear and greed index:</b> {_missing(fng)}</p>'
    flags = context.get("flags") or []
    flag_html = (f'<p class="sub"><b>Unusual today:</b> {esc("; ".join(flags))}.</p>' if flags
                 else '<p class="sub"><b>Unusual today:</b> nothing among the sources that responded.</p>')
    return ('<section class="card"><h2>Market context: liquidity, derivatives, implied volatility, sentiment<span class="chip code">COMPUTED BY CODE</span></h2>'
            '<p class="sub">Extra free sources that describe how the market is positioned. They are context only: they never raise an alert, and a field that cannot be read says so '
            'instead of being estimated. A positive funding rate means longs pay shorts. Implied volatility is what option prices imply, not a forecast.</p>'
            f'{table}{mood}{flag_html}</section>')


def _price(value):
    return f"${value:,.2f}" if value >= 100 else f"${value:,.4f}"


def _level_cell(level, last, side="above"):
    if level == "already":
        return "already at the alert rank"
    if level is None:
        return "not reachable"
    text = f"{_price(level)} ({(level / last - 1) * 100:+.1f}%)"
    if (side == "above" and last >= level) or (side == "below" and last <= level):
        text += ", already beyond"
    return text


def base_rate_section(data):
    ok = [c for c in good(data) if c.get("base_rate") and c["base_rate"]["ranked_days"]]
    if not ok:
        return ""
    rows, union = [], {}
    for c in ok:
        b = c["base_rate"]
        for day, flag in zip(b["days"], b["flags"]):
            union[day] = union.get(day, False) or flag
        last = next((d for d, f in zip(reversed(b["days"]), reversed(b["flags"])) if f), None)
        rows.append(f'<tr><td><b>{esc(c["coin"])}</b></td><td class="num">{b["ranked_days"]}</td><td class="num">{b["alert_days"]}</td>'
                    f'<td class="num">{100.0 * b["alert_days"] / b["ranked_days"]:.1f}%</td><td class="num">{b["episodes"]}</td><td>{esc(last or "never")}</td></tr>')
    flags = [union[d] for d in sorted(union)]
    rows.append(f'<tr><td><b>Any coin</b></td><td class="num">{len(flags)}</td><td class="num">{sum(flags)}</td>'
                f'<td class="num">{100.0 * sum(flags) / len(flags):.1f}%</td><td class="num">{episodes(flags)}</td><td></td></tr>')
    return ('<section class="card"><h2>How often the alert rank is reached<span class="chip code">COMPUTED BY CODE</span></h2>'
            f'<p class="sub">The same rule run over each coin\'s own history: a day counts when volatility, distance from the 30-day average or drawdown ranks at {agent.ALERT_RANK:g} or higher. '
            'Each day is ranked only against the days before it, so no future data leaks in. Three days in a row are one episode.</p>'
            '<table><tr><th>Coin</th><th class="num">Days ranked</th><th class="num">Days at the alert rank</th><th class="num">Share of days</th>'
            '<th class="num">Separate episodes</th><th>Latest day at the alert rank</th></tr>' + "".join(rows) + '</table>'
            '<p class="sub" style="margin-top:12px"><b>How to read it.</b> A top-5% rule reaches the alert rank on about 5% of days for each metric by construction. '
            'The three metrics move together and a spike stays in the 30-day window, so days cluster into episodes. Because three coins are watched, an alert for some coin is more common than an alert for one coin. '
            'These are in-sample counts on past data and say nothing about what comes next.</p></section>')


def watch_section(data):
    rows = []
    columns = [("distance", "up", "above"), ("distance", "down", "below"), ("volatility", "up", "above"), ("volatility", "down", "below"), ("drawdown", "down", "below")]
    for c in good(data):
        lv = c.get("levels")
        if not lv:
            continue
        last = c["summary"]["last_close"]
        cells = []
        for metric, key, side in columns:
            level = lv[metric][key] if lv.get(metric) and key in lv[metric] else "none"
            cells.append("n/a" if level == "none" else _level_cell(level, last, side))
        rows.append(f'<tr><td><b>{esc(c["coin"])}</b><br><span class="muted">last {_price(last)}</span></td>'
                    + "".join(f'<td class="num">{esc(x)}</td>' for x in cells) + "</tr>")
    if not rows:
        return ""
    levels = ('<table><tr><th>Coin</th><th class="num">Distance: close above</th><th class="num">Distance: close below</th>'
              '<th class="num">Volatility: close above</th><th class="num">Volatility: close below</th><th class="num">Drawdown: close below</th></tr>'
              + "".join(rows) + "</table>")
    days = max((c["levels"]["history_days"] for c in good(data) if c.get("levels")), default=0)
    scenarios_rows = [
        ("Stress persists", f"A metric that is at rank {agent.ALERT_RANK:g} or higher stays there after the next completed day.",
         f"The agent stays quiet for that coin: it is in the last {agent.HISTORY_LIMIT} decisions.", f"The rank falls below {NEAR_RANK:g}."),
        ("Back to normal", f"Every rank of a coin is below {NEAR_RANK:g}.", "The coin reads as calm. No alert.", f"Any rank returns to {agent.ALERT_RANK:g} or higher."),
        ("A new breach", "A coin that has not breached closes beyond one of the levels above.", "The next daily run sends one brief alert for that coin and cites the playbook rule.",
         "The close stays between the levels, or the candle or data is not valid."),
    ]
    body = "".join(f"<tr><td><b>{esc(a)}</b></td><td>{esc(b)}</td><td>{esc(c)}</td><td>{esc(d)}</td></tr>" for a, b, c, d in scenarios_rows)
    return ('<section class="card"><h2>Conditional watch scenarios<span class="chip watch">CONDITIONS TO MONITOR, NOT PREDICTIONS</span></h2>'
            '<p class="sub">No probability is given and no price is forecast. Each scenario is a condition that can be observed, what the agent does when it happens, '
            'and what would show the scenario no longer applies.</p>'
            f'<table><tr><th>Scenario</th><th>Condition to observe</th><th>What the agent does</th><th>Invalidation</th></tr>{body}</table>'
            '<p class="sub" style="margin-top:14px"><b>Next-day closes that would put a metric at the alert rank.</b> Worked out from each coin\'s own history by code. A close between the two levels of a metric leaves it below the alert rank.</p>'
            f'{levels}'
            f'<p class="sub"><b>Uncertainty.</b> The levels use up to {days} days of each coin\'s own history and assume the 30-day window moves forward by one day. '
            'A new day also shifts the history a little, so read each level as approximate. A rank within five points of the alert rank can cross it in a single day. '
            'A level is no longer valid once the next candle completes, or if Kraken returns incomplete or stale data.</p></section>')


def chart_script(specs):
    payload = json.dumps(specs).replace("</", "<\\/")
    return ('<script src="%s"></script><script>(function(){var specs=%s;var cfg={displayModeBar:false,responsive:true};'
            'document.querySelectorAll(".chart").forEach(function(el){var spec=specs[el.dataset.chart];'
            'if(!spec){return;}if(!window.Plotly){el.textContent="The charts need an internet connection to load plotly.js.";return;}'
            'el.textContent="";Plotly.newPlot(el,spec.data,spec.layout,cfg);});})();</script>') % (PLOTLY_URL, payload)


def build_html(data):
    label, tone, summary_line = status_of(data)
    ok = good(data)
    breached = len(breaching(data))
    as_of = max((c["summary"]["as_of"] for c in ok), default="unavailable")
    demo = '<div class="demo">DEMO DATA: synthetic prices, not real market data</div>' if data.get("demo") else ""
    kpis = [
        ("Status", label, summary_line),
        ("Coins at the alert rank", f"{breached} of {len(data['coins'])}", f"Alert rank {agent.ALERT_RANK:g}, near threshold {NEAR_RANK:g}"),
        ("Data reliability", f"{len(ok)} of {len(data['coins'])} coins", "Complete, current daily candles from Kraken"),
        ("Window", "30 days", f"Ranked against up to ~700 days of each coin's own history"),
    ]
    kpi_html = "".join(f'<div class="kpi"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div><div class="s">{esc(s)}</div></div>' for k, v, s in kpis)
    head = "".join(f"<li>{esc(line)}</li>" for line in headlines(data))
    watch = "".join(f"<li>{esc(line)}</li>" for line in triggers(data))
    charts_html = ""
    if ok:
        charts_html = (
            '<section class="card"><h2>Charts<span class="chip code">COMPUTED BY CODE</span></h2>'
            '<p class="sub">Ranks compare today with the coin\'s own past, so a rank of 95 means higher than about 95% of its own days.</p>'
            '<div class="grid2"><div><b>Price, indexed to 100 (last 90 days)</b><div class="chart" data-chart="price"></div></div>'
            '<div><b>Today against each coin\'s own history (rank, 0 to 100)</b><div class="chart" data-chart="ranks"></div></div></div>'
            '<div style="margin-top:12px"><b>How unusual each coin has been: highest of the three ranks, last 60 days</b>'
            '<div class="chart" data-chart="history"></div></div></section>')
    generated = data["generated"]
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crypto Watch: Executive Intelligence Brief</title><style>{CSS}</style></head><body>
<header class="banner">{BANNER_ART}<div class="wrap">
<div class="eyebrow">CRYPTO WATCH AGENT &middot; EXECUTIVE INTELLIGENCE BRIEF</div>
<h1>BTC, ETH and XRP: how unusual is today against their own history?</h1>
<div class="meta">Data as of {esc(as_of)} (latest completed UTC day) &middot; Generated {esc(generated)} UTC &middot; Source: Kraken daily candles</div>
<span class="pill {tone}">{esc(label)}: {esc(summary_line)}</span>{demo}</div></header>
<main class="wrap">
<div class="kpis">{kpi_html}</div>
{question_section()}
<section class="card"><h2>Headlines<span class="chip code">COMPUTED BY CODE</span></h2><ul class="clean">{head}</ul></section>
<section class="card"><h2>Market snapshot<span class="chip code">COMPUTED BY CODE</span></h2>
<p class="sub">Each value is followed by its rank (0 to 100) against that coin's own history. Red is at or above {agent.ALERT_RANK:g}, amber is {NEAR_RANK:g} to below {agent.ALERT_RANK:g}.</p>{snapshot_table(data)}</section>
{charts_html}
{context_section(data)}
{market_context_section(data)}
{base_rate_section(data)}
{ai_section(data)}
<section class="card"><h2>What to watch at the next review<span class="chip watch">MONITORING TRIGGERS, NOT FORECASTS</span></h2><ul class="clean">{watch}</ul></section>
{watch_section(data)}
<section class="card"><h2>Decision history<span class="chip code">FROM THE SHEET</span></h2>{decision_table(data)}</section>
{scenario_section()}
<section class="card"><h2>How to read this report</h2><div class="legend">
<span><span class="chip code">COMPUTED BY CODE</span> numbers, ranks, headlines, triggers</span>
<span><span class="chip ai">WRITTEN BY AI</span> the analysis text</span>
<span><span class="chip check">CHECKED BY CODE</span> guardrails on the AI's decision</span></div></section>
<footer><b>Education only. Not financial advice.</b> This report contains no price forecast and makes no recommendation. Thresholds are descriptive conventions
(rank {agent.ALERT_RANK:g}, 30-day window, daily candles), documented with reasons in playbook.md. Price data: Kraken public OHLC API.
Market context, where available: Kraken order book and Kraken Futures, Deribit volatility index (DVOL), market-wide volume and prices from
<a href="https://www.coingecko.com/en/api">CoinGecko API</a>, and the Fear and Greed Index from <a href="https://alternative.me/crypto/fear-and-greed-index/">alternative.me</a>.
Generated by the Crypto Watch Agent from its own tools; see the repository README for the method and guardrails.</footer>
</main>{chart_script(charts(data))}</body></html>"""


# ------------------------------------------------------------------------------------------------
# 5. Email (inline styles only: email programs ignore scripts and most CSS)
# ------------------------------------------------------------------------------------------------
def build_email(data):
    label, tone, summary_line = status_of(data)
    colors = {"red": ("#fee2e2", "#991b1b"), "green": ("#dcfce7", "#166534"), "grey": ("#e2e8f0", "#334155")}[tone]
    items = "".join(f'<li style="margin:6px 0">{esc(line)}</li>' for line in headlines(data))
    decision = latest_decision(data)
    ai = ""
    if decision and decision.get("message"):
        ai = (f'<tr><td style="padding:0 24px 18px"><div style="border-left:5px solid #6d28d9;background:#f5f3ff;padding:12px 16px;border-radius:8px">'
              f'<div style="font-size:11px;font-weight:700;color:#5b21b6;letter-spacing:.05em">AI ANALYSIS (WRITTEN BY AI, CHECKED BY CODE)</div>'
              f'<div style="margin-top:6px;font-size:15px;color:#0f172a">{esc(decision["message"])}</div></div></td></tr>')
    demo = '<div style="color:#92400e;font-weight:700">DEMO DATA</div>' if data.get("demo") else ""
    return (
        '<div style="background:#f1f5f9;padding:18px;font-family:Segoe UI,Arial,sans-serif;color:#0f172a">'
        '<table role="presentation" width="640" align="center" style="max-width:640px;width:100%;background:#ffffff;border-radius:12px;border-collapse:collapse;overflow:hidden">'
        '<tr><td style="background:#0b3b52;background-image:linear-gradient(120deg,#0b1f3a,#0f766e);padding:26px 24px;color:#ffffff">'
        '<div style="font-size:11px;letter-spacing:.14em;font-weight:700;color:#99f6e4">CRYPTO WATCH AGENT &middot; EXECUTIVE BRIEF</div>'
        '<div style="font-size:22px;font-weight:700;margin-top:8px">BTC, ETH and XRP against their own history</div>'
        f'<div style="margin-top:12px;display:inline-block;background:{colors[0]};color:{colors[1]};padding:6px 14px;border-radius:999px;font-weight:700;font-size:13px">'
        f'{esc(label)}: {esc(summary_line)}</div>{demo}</td></tr>'
        f'<tr><td style="padding:16px 24px 0;font-size:13px;color:#475569"><i>{esc(agent.QUESTION)}</i></td></tr>'
        '<tr><td style="padding:14px 24px 8px"><div style="font-size:12px;font-weight:700;color:#1e40af;letter-spacing:.05em">HEADLINES (COMPUTED BY CODE)</div>'
        f'<ul style="padding-left:18px;margin:8px 0;font-size:14px;line-height:1.5">{items}</ul></td></tr>{ai}'
        '<tr><td style="padding:6px 24px 22px;font-size:13px;color:#475569">The full brief with charts is attached as an HTML file. Open it in a browser. '
        'Education only, not financial advice. No forecast and no recommendation.</td></tr></table></div>')


def send_email(data, page):
    label, _, summary_line = status_of(data)
    subject = f"Crypto watch executive brief: {label} ({summary_line})"[:200]
    try:
        agent.sheet_post({"action": "report", "subject": subject, "summary_html": build_email(data), "html": page})
    except agent.AgentError as error:
        text = str(error)
        hint = (" The deployed web app is the older version. Update apps_script/Code.gs from the repository and redeploy it (Manage deployments > pencil > New version)."
                if ("INVALID_ACTION" in text or "INVALID_REQUEST" in text) else "")
        print(f"::warning::Report email not sent: {text}.{hint}", flush=True)
        return False
    print("[report] email sent", flush=True)
    return True


# ------------------------------------------------------------------------------------------------
# 6. Command line
# ------------------------------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the executive intelligence brief.")
    parser.add_argument("--out", default="executive-report.html")
    parser.add_argument("--email", action="store_true", help="email the report through the Apps Script web app")
    parser.add_argument("--demo", action="store_true", help="use synthetic data to preview the layout")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    if args.demo:
        coins, cross = demo_coins(), demo_cross()
        context = demo_context(coins)
        decisions, note = demo_decisions()
    else:
        coins, cross = gather(now)
        context = gather_context()
        decisions, note = fetch_decisions()
    data = {"coins": coins, "cross": cross, "context": context, "decisions": decisions, "decisions_note": note, "demo": args.demo,
            "generated": now.strftime("%Y-%m-%d %H:%M")}
    page = build_html(data)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(page)
    print(f"[report] wrote {args.out} ({len(page) // 1024} KB): {status_of(data)[0]}, {len(good(data))} of {len(coins)} coins with data", flush=True)
    for line in headlines(data):
        print(f"[report]   {line}", flush=True)
    if args.email and not args.demo:
        send_email(data, page)
    return 0 if good(data) or args.demo else 1


if __name__ == "__main__":
    sys.exit(main())
