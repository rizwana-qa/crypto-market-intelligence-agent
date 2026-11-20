# Crypto Watch Agent

Densight Labs AI Masterclass, Assignments 2 and 3: Build a Crypto Watch Agent.

**This project is for education only and is not financial advice.**

## Question and watchlist

> Is BTC, ETH or XRP showing unusual volatility, distance from its 30-day average or drawdown in its latest completed daily candle compared with its own history, and is the data reliable enough to say so?

| Coin | Kraken pair | Kraken result key |
|---|---|---|
| Bitcoin (BTC) | `XBTUSD` | `XXBTZUSD` |
| Ethereum (ETH) | `ETHUSD` | `XETHZUSD` |
| XRP | `XRPUSD` | `XXRPZUSD` |

Data: Kraken public OHLC endpoint, daily candles (`interval=1440`), no key. BTC and ETH were verified in Assignment 1. The XRP result key was checked against Kraken's `AssetPairs` endpoint (`XRPUSD` is stored under `XXRPZUSD`).

## How it works

Each day the agent looks at three coins and decides whether anything deserves an alert.

**What the AI does.** It chooses which tools to call, reads their results, searches `playbook.md` for the rule that applies, and writes a draft decision as JSON: `alert`, `coins`, `message`, `sources`, `reasoning`. It only recommends.

**What the code does.** It fetches and validates the Kraken data, calculates every number, ranks each number against the coin's own history, runs every tool, checks the AI's answer, blocks anything that breaks a rule, writes the Sheet row and triggers the email. The AI never fetches data and never runs a tool.

```
Kraken -> market tool (code) ----\
Sheet  -> memory tool (code) ------> AI (Groq) -> draft JSON -> [reviewer] -> GUARDRAILS (code) -> Sheet row + email
playbook.md -> search tool (code) -/
```

**Model.** The AI is `openai/gpt-oss-120b` on Groq (set by `MODEL` in `agent.py`, or the `GROQ_MODEL` variable). The first choice, `llama-3.3-70b-versatile`, was shut down by Groq on 16 August 2026, so the first live run failed with `model_not_found`. The free Groq tier allows 8,000 tokens per minute and 200,000 per day, so the agent waits and retries when it is rate limited.

Four versions can be run: **Agent loop** (the AI asks for tools one round at a time), **Agent loop + reviewer**, **Planner** (the AI writes a plan, the code validates it and runs every step, then the AI decides) and **Planner + reviewer**.

## Run it from a fresh clone

1. Python 3.12 or newer (the workflows use 3.12; the offline tests were run on 3.13). `git clone <this repo>` and `cd` into it. `pip install -r requirements.txt`
2. **Google Sheet and Apps Script.**
   1. Create a Google Sheet. Copy its id (the part of the URL between `/d/` and `/edit`).
   2. In the Sheet: Extensions > Apps Script. Paste `apps_script/Code.gs`.
   3. Project Settings > Script properties: add `KEY` (a random string of at least 16 characters) and `SPREADSHEET_ID`. Optionally `EMAIL_TO`.
   4. Run `authorizeOnce` once in the editor and accept the permissions. It creates the `Agent` tab.
   5. Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone. Copy the web app URL (it ends in `/exec`).
   6. After every change to the script: Deploy > Manage deployments > edit > Version: New version > Deploy.
3. **Keys.** Never put them in a file.
   - Terminal: `export GROQ_API_KEY=...` and `export SHEET_URL="https://script.google.com/macros/s/.../exec?key=<KEY>"`
   - GitHub: Settings > Secrets and variables > Actions > add `GROQ_API_KEY` and `SHEET_URL`. Paste them without spaces.
4. Check the pairs and calibrate the thresholds: `python calibrate.py`
5. Run the agent: `python agent.py` (add `--dry-run` to skip the Sheet; `--mode planner --reviewer on` for another version).
6. Run the tests: `python tests/test_guardrails.py -v` (offline) and `python agent.py --test` (fake tools, real AI).
7. Measure the four versions: `python agent.py --measure` (12 full runs, about 1 million tokens; this is more than the free Groq limit of 200,000 tokens per day, so it needs the paid tier or several days).

## Playbook thresholds, with reason and source

The full rules are in [`playbook.md`](playbook.md). Every number the agent uses:

| Threshold | Value | Reason | Source |
|---|---|---|---|
| Window | 30 days (31 closes, 30 returns) | Same window as the Assignment 1 analysis | Assignment 1 README, Method |
| Alert rank | 95 | Top 5% of the coin's own days, about 1 day in 20 per metric. 99 almost never fires, 90 fires about 1 day in 10 | Own design, backtested by `calibrate.py` |
| Near threshold | 90 to below 95 | A day just under the bar is not a breach | Own convention |
| Minimum history | 90 completed candles | The top 5% of 90 days is 4 or 5 days, the least that makes a rank meaningful | Own design, arithmetic |
| Repeat lookback | last 7 decisions | A spike stays in the 30-day window for 30 days, so it would repeat daily. A week is long enough to stop that | Window arithmetic, own design for 7 |
| Message length | 280 characters | Readable at a glance in an email | Own convention |
| Step limit | 8 AI calls | A run that needs more is stuck, so it stops with an error | Own design |
| Plan limit | 8 steps | Five steps are needed (three coins, memory, playbook), 8 leaves room | Own design |

These are descriptive conventions, not market standards and not trading signals.

### Backtest of the alert rank

Not run yet. TODO(you): run `python calibrate.py` on your own computer (it needs access to Kraken) and paste its table here. It counts how many days in the last ~700 would have raised an alert, and how many separate episodes that is.

### Hand check

TODO(you): pick one number from the market tool (for example `volatility_30d_daily_pct` for BTC). Download the same 31 closes from Kraken, recalculate it in Google Sheets with `=STDEV.S(...)` on the 30 daily returns, and write what you found here.

## Guardrails (all in code, applied after the AI answers)

| Guardrail | Function | Proved by (tests/test_guardrails.py) |
|---|---|---|
| The answer must be valid JSON with the right keys, or the AI is asked again (twice at most, then the run stops) | `parse_decision`, loop in `run_agent_loop` | `AnswerShape`, `test_loop_asks_again_after_an_invalid_answer_but_only_twice` |
| Only allowed tools, and only watchlist coins; anything else is an error, never a guess | `check_call`, `run_tool` | `ToolRules`, `test_loop_refuses_a_coin_that_is_not_on_the_watchlist` |
| **No valid source, no alert.** Every source must be a real playbook section title | `apply_guardrails` | `test_no_valid_source_no_alert`, `test_an_ai_that_invents_a_source_cannot_get_an_alert_through` |
| A short alert only (280 characters), and no alert without a message | `parse_decision` | `AnswerShape.test_bad_answers_are_refused` |
| **Advice filter** | `ADVICE_RE`, `apply_guardrails` | `AdviceFilter`, `test_an_ai_that_gives_advice_cannot_get_an_alert_through` |
| No alert without valid market data in this run | `apply_guardrails` | `test_alert_without_valid_market_data_is_blocked` |
| Step limit: the run stops with an error | `MAX_STEPS` | `test_loop_stops_with_an_error_at_the_step_limit` |
| A plan is validated before anything runs; one retry, then the run stops | `validate_plan`, `make_plan` | `test_every_kind_of_bad_plan_is_refused`, `test_planner_stops_after_a_second_bad_plan` |
| A broken reviewer reply never blocks the job, and the code guardrails still apply after it | `review_draft` | `test_a_broken_reviewer_reply_never_blocks_the_job`, `test_code_guardrails_still_apply_after_a_reviewer_that_approves_everything` |
| The Sheet key never appears in an error message or log | `redact` | `Delivery.test_the_key_never_appears_in_an_error` |

**The advice filter.** An alert whose message contains any of these words, as a whole word in any letter case, is blocked: buy, buying, bought, sell, selling, sold, long, short, hold, holding, entry, exit, accumulate, should, recommend, recommends, recommended, recommendation, advise, advice, take profit, stop loss, stop-loss. The list covers actions on a position (buy, sell, long, short, hold, entry, exit, accumulate, take profit, stop loss) and advisory verbs (should, recommend, advise). It matches whole words, so "longer", "shortfall" and "holdings" pass. The list is the constant `BANNED_WORDS` at the top of `agent.py`. A blocked alert is stored in the Sheet with its reason and an empty message.

### Two kinds of tests

- `tests/test_guardrails.py`: no AI, no network. A scripted AI gives the same answers every time, so these are exact. They also check the maths against a hand calculation (59 closes of 100 followed by 90).
- `python agent.py --test`: the scenarios in `scenarios.py` on fake market data and a fake history, with the real AI and the real playbook search. Seven scenarios: calm market; volatility spike; same spike already alerted yesterday; Kraken down; advice trap (the fake data contains forum chatter telling the AI to buy); one coin down while the others are calm; a day just under the threshold. A failed scenario exits with code 1, so the GitHub run turns red.

### Red, then green

I broke a guardrail on purpose: the message length limit `MAX_MESSAGE_CHARS` was raised from 280 to 2800 (commit `3258757`).

- **Red run** ([run 44737225410](https://github.com/rizwana-qa/crypto-watch-agent/actions/runs/44737225410)): the step "Guardrail tests (offline)" failed with `FAILED (failures=2)`. The two failing tests were `AnswerShape.test_bad_answers_are_refused` (an over-long message was accepted) and `Playbook.test_every_threshold_in_the_code_appears_in_the_playbook` (the code and the playbook no longer agreed on 280). The scenario step was skipped because the first step failed.
- **Green run** ([run 44737359535](https://github.com/rizwana-qa/crypto-watch-agent/actions/runs/44737359535), commit `c09991e`, restoring 280): the offline tests pass again and the seven scenarios then run with the real AI.

TODO(you): screenshots of both runs, with the log open (a run with an empty log means nothing ran).

## Sample output

Live run on 2026-11-20 (Actions > Daily crypto watch, version Agent loop). The agent fetched BTC, ETH and XRP from Kraken, read its history from the Sheet, searched the playbook and decided `alert = true` for BTC and XRP with the sources "Alert rules" and "Trend against the 30-day average". The Sheet web app saved it as row 2 and sent the email (`email sent: True`). The reasoning in the row quotes the ranks it was given: BTC distance rank 97.0, BTC drawdown rank 96.6, XRP drawdown rank 96.1, all at or above the alert rank of 95.

TODO(you): add a screenshot of the Sheet row (with its sources) and of the email.

## Results and conclusion (Step 13)

Each version is to be run three times on the same seven scenarios with `python agent.py --measure` (also saved in `results.md`).

**Measured so far:** the Agent loop version passed all 7 scenarios once (46 AI calls, 0 scenarios unfinished, 15 rate-limit waits) in the GitHub run on 2026-11-20 ([run 44736173365](https://github.com/rizwana-qa/crypto-watch-agent/actions/runs/44736173365)). The other three versions have not been run with the real AI yet. The full measurement is about 1 million tokens, more than the 200,000 tokens per day of the free Groq tier.

TODO(you): paste the full table once `--measure` has been run.

| Version | Passes out of 7 (run 1, run 2, run 3) | Calls per run | Ever failed to finish? |
|---|---|---|---|
| Agent loop | 7/7 (one run only) | 46 | no |
| Agent loop + reviewer | not run | | |
| Planner | not run | | |
| Planner + reviewer | not run | | |

Conclusion: TODO(you). Which version runs daily, why, and what it costs in calls and waiting. An honest result such as "the reviewer did not help" is a good answer. Then set that version in `.github/workflows/daily.yml` and in `tests.yml`.

## Files

| File | What it is |
|---|---|
| `agent.py` | The agent: tools, loop, planner, reviewer, guardrails, delivery, command line |
| `scenarios.py` | Test mode: fake data and the seven scenarios, plus the `--measure` table |
| `playbook.md` | The agent's own rules, with a reason and a source for every threshold |
| `calibrate.py` | Backtest of the alert rank on live Kraken history |
| `apps_script/Code.gs` | The Google Apps Script web app (history, save, email). No key in it |
| `tests/test_guardrails.py` | Offline tests |
| `.github/workflows/daily.yml`, `tests.yml` | The daily run and the tests on every code or playbook change |
| `AI_LOG.md` | The five most important prompts, and what the AI got wrong or I changed |

Parts of the Kraken fetch and the 30-day calculations are adapted from my Assignment 1 repository (`src/fetch_data.py`, `src/analysis.py`). That repository was not changed.
