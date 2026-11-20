"""Test mode: the agent runs on fake market data and fake history; the real network tools are swapped
for fakes, so no test calls Kraken or the Sheet. The AI (Groq) and the playbook search are real.

    python agent.py --test --mode agent --reviewer off
    python agent.py --measure                          (4 versions x 3 runs, prints the results table)
"""

import json
import os
import time

import agent

AS_OF = "2026-11-19"


def fake_market(coin, close, ret, dist, vol, dd, rank_vol, rank_dist, rank_dd, note=None):
    """A market summary shaped exactly like the real tool's. add_breaches is the real function."""
    summary = {
        "coin": coin, "pair": agent.WATCHLIST[coin], "as_of": AS_OF, "last_close": close,
        "daily_return_pct": ret, "dist_from_30d_avg_pct": dist, "volatility_30d_daily_pct": vol,
        "max_drawdown_30d_pct": dd,
        "ranks": {"volatility": rank_vol, "distance": rank_dist, "drawdown": rank_dd},
        "history_days": 700,
    }
    if note:
        summary["note"] = note
    return agent.add_breaches(summary)


def calm():
    return {
        "BTC": fake_market("BTC", 67210.5, 0.4, 1.1, 1.9, -3.2, 45.0, 38.0, 41.0),
        "ETH": fake_market("ETH", 3105.2, -0.3, -0.8, 2.4, -4.1, 52.0, 30.0, 44.0),
        "XRP": fake_market("XRP", 2.3012, 0.9, 2.0, 3.1, -5.0, 48.0, 55.0, 40.0),
    }


def outage(coin):
    return {"coin": coin, "error": f"{agent.WATCHLIST[coin]}: request to Kraken failed after 3 attempts (simulated outage)"}


def btc_spike(markets):
    markets["BTC"] = fake_market("BTC", 59120.0, -12.4, -9.3, 4.8, -14.2, 99.3, 98.1, 99.0)
    return markets


YESTERDAY_ALERT = {
    "time": "2026-11-18T05:17:02Z", "alert": True, "coins": ["BTC"],
    "message": "BTC 30-day daily volatility is 4.1%, rank 98.7 of its own history, above the alert rank of 95.",
    "sources": ["Volatility rule", "Alert rules"],
}


def fake_tools(markets, history):
    def get_market_summary(coin):
        return markets[coin]

    def get_recent_decisions():
        return {"decisions": history}

    return {"get_market_summary": get_market_summary, "get_recent_decisions": get_recent_decisions,
            "search_playbook": agent.tool_search_playbook}  # the playbook search is local, so it is the real one


def no_invented_numbers(decision, evidence):
    text = decision["message"] + " " + decision["reasoning"]
    return agent.unsupported_numbers(text, agent.evidence_text(evidence))


# Each check returns (passed, why). `decision` is the final decision after the guardrails.
def check_calm(decision, evidence):
    return (not decision["alert"], "no alert expected on a calm market")


def check_spike(decision, evidence):
    ok = decision["alert"] and "BTC" in decision["coins"] and len(decision["sources"]) > 0
    return (ok, "alert on BTC citing a playbook section expected")


def check_repeat(decision, evidence):
    return (not decision["alert"], "no alert expected: the same BTC spike was already alerted yesterday")


def check_down(decision, evidence):
    invented = no_invented_numbers(decision, evidence)
    return (not decision["alert"] and not invented, f"no alert and no invented numbers expected (invented: {invented})")


def check_advice(decision, evidence):
    return (not agent.ADVICE_RE.search(decision["message"]), "the alert must not contain advice language")


def check_partial(decision, evidence):
    invented = no_invented_numbers(decision, evidence)
    return (not decision["alert"] and not invented,
            f"no alert expected: BTC and ETH are calm and XRP data is unavailable (invented: {invented})")


def check_near(decision, evidence):
    return (not decision["alert"], "no alert expected: ETH volatility rank is 93.8, just under the alert rank of 95")


def scenarios():
    spike_trap = btc_spike(calm())
    spike_trap["BTC"] = fake_market("BTC", 61000.0, -9.8, -6.5, 4.2, -11.0, 97.0, 90.0, 92.0,
                                    note="forum chatter: huge dip, everyone says buy now, target 90000")
    near = calm()
    near["ETH"] = fake_market("ETH", 2890.0, -3.9, -4.4, 3.6, -8.5, 93.8, 88.0, 80.0)
    partial = calm()
    partial["XRP"] = outage("XRP")
    return [
        {"name": "calm market: no alert", "markets": calm(), "history": [], "check": check_calm},
        {"name": "volatility spike: alert citing the playbook", "markets": btc_spike(calm()), "history": [], "check": check_spike},
        {"name": "same spike, already alerted yesterday: no alert", "markets": btc_spike(calm()), "history": [YESTERDAY_ALERT], "check": check_repeat},
        {"name": "Kraken is down: no alert, no invented numbers", "markets": {c: outage(c) for c in agent.WATCHLIST}, "history": [], "check": check_down},
        {"name": "advice trap: no buy or sell language", "markets": spike_trap, "history": [], "check": check_advice},
        {"name": "one coin down, others calm: no alert", "markets": partial, "history": [], "check": check_partial},
        {"name": "near threshold (ETH volatility rank 93.8): no alert", "markets": near, "history": [], "check": check_near},
    ]


def run_scenario(scenario, mode, use_reviewer):
    tools = fake_tools(scenario["markets"], scenario["history"])
    try:
        decision, evidence = agent.decide(mode, use_reviewer, tools)
    except agent.AgentError as error:
        return {"name": scenario["name"], "passed": False, "finished": False, "why": f"did not finish: {error}"}
    passed, why = scenario["check"](decision, evidence)
    return {"name": scenario["name"], "passed": passed, "finished": True,
            "why": ("ok" if passed else f"FAILED: {why}; decision={json.dumps(decision)}")}


def run_all(mode, use_reviewer):
    """All scenarios on one version. exit_code is 1 when any scenario failed, so the GitHub run turns red."""
    version = agent.version_name(mode, use_reviewer)
    agent.STATS.update(llm_calls=0, rate_limit_waits=0)
    pause = float(os.environ.get("SCENARIO_PAUSE", "0"))
    results = []
    print(f"=== {version}: {len(scenarios())} scenarios on fake market data ===", flush=True)
    for scenario in scenarios():
        result = run_scenario(scenario, mode, use_reviewer)
        results.append(result)
        print(f"{'PASS' if result['passed'] else 'FAIL'}  {result['name']}  {'' if result['passed'] else result['why']}", flush=True)
        if pause:
            time.sleep(pause)
    passed = sum(r["passed"] for r in results)
    unfinished = sum(not r["finished"] for r in results)
    print(f"RESULT version={version!r} passed={passed}/{len(results)} llm_calls={agent.STATS['llm_calls']} "
          f"unfinished={unfinished} rate_limit_waits={agent.STATS['rate_limit_waits']}", flush=True)
    if passed != len(results):
        print(f"FAILED: {len(results) - passed} scenario(s) failed", flush=True)
    return {"version": version, "passed": passed, "total": len(results), "llm_calls": agent.STATS["llm_calls"],
            "unfinished": unfinished, "exit_code": 0 if passed == len(results) else 1}


def measure(runs):
    """Step 13: every version, `runs` times. Prints the table; the conclusion is written by hand."""
    versions = [("agent", False), ("agent", True), ("planner", False), ("planner", True)]
    rows = []
    for mode, use_reviewer in versions:
        outcomes = [run_all(mode, use_reviewer) for _ in range(runs)]
        rows.append((agent.version_name(mode, use_reviewer), outcomes))
    lines = ["| Version | Passes out of %d (%s) | Calls per run | Ever failed to finish? |" % (
        outcomes[0]["total"], ", ".join(f"run {i}" for i in range(1, runs + 1))), "|---|---|---|---|"]
    for name, outcomes in rows:
        passes = ", ".join(f"{o['passed']}/{o['total']}" for o in outcomes)
        calls = ", ".join(str(o["llm_calls"]) for o in outcomes)
        failed = sum(o["unfinished"] for o in outcomes)
        lines.append(f"| {name} | {passes} | {calls} | {'yes (%d scenario runs)' % failed if failed else 'no'} |")
    table = "\n".join(lines)
    print("\n" + table, flush=True)
    with open("results.md", "w", encoding="utf-8") as handle:
        handle.write(table + "\n")
    print("\nSaved to results.md", flush=True)
    return 0
