# AI log

The five prompts that mattered most, and what the AI got wrong or I changed by hand.

This log records real events from the build sessions. Read it once and change anything that is not how it happened.

## 1. Plan the shortest route

**Prompt:** "Act as python expert engineer and tell me the best and easiest way to finish this assignment and start doing and sharing steps in easy ways. We have only 1 hour to complete."

**Result:** a time-boxed plan: one `agent.py` with modes, thresholds taken from percentile ranks of each coin's own history, constants at the top for live changes, and the Sheet key carried inside `SHEET_URL` so only two secrets exist.

**What the AI got wrong or I changed:** the first plan was written before the Assignment 1 code was read. It assumed the Kraken fetch from Assignment 1 would work for three coins. It does not: Assignment 1 refuses any result key it has not verified, and only knew `XBTUSD` and `ETHUSD`. The XRP key (`XXRPZUSD`) had to be checked against Kraken's `AssetPairs` endpoint first.

## 2. Build Part A and Part B

**Prompt (in short):** build the agent code in my new repository, with tools, an agent loop, guardrails, the reviewer and the planner, and tell me the manual steps.

**What the AI got wrong or I changed:** the first test run had two failing tests. Both were mistakes in the tests, not in the code. One "allowed" sentence contained the word "advice", which the filter blocks on purpose. In the other, the scripted AI never called a tool, so the "no valid market data" guardrail correctly blocked the alert. The tests were corrected, not the guardrails.

## 3. Prove that the tests can fail

**Prompt (in short):** check that the tests can really fail, not only pass.

**What the AI got wrong or I changed:** a first mutation check reported "Ran 51 tests" without showing the verdict line, so it proved nothing. It was repeated with the verdict visible. Five deliberate breaks were all caught: an emptied advice list, a disabled source check, a removed no-market-data guard, a broken step limit and a removed watchlist check.

## 4. First live run: the model had been shut down

**Prompt (in short):** I added the Groq key and the Sheet URL as GitHub secrets, now run it.

**What the AI got wrong or I changed:** the code defaulted to `llama-3.3-70b-versatile`, which Groq shut down on 16 August 2026, so the first live run failed with `model_not_found` (HTTP 404). The AI checked Groq's deprecation page and switched the default to `openai/gpt-oss-120b`, the replacement Groq lists. The next run hit the free tier limit of 8,000 tokens per minute, so the retry was changed from 4 to 8 attempts with a longer wait. None of this could be found with the offline tests, because the scripted AI never calls Groq.

## 5. First live save: three Sheet errors in a row

**Prompt (in short):** the Sheet has no column names, and later the Daily run fails.

**What the AI got wrong or I changed:** the Sheet answered `UNAUTHORIZED` and then `404`. The log showed the cause each time without printing the key: first `SHEET_URL` had no `?key=` part, then the URL ended in `/exe` instead of `/exec`, then the `KEY` Script Property had 17 characters and the key in the secret had 16. The agent was changed to print a safe hint (the length of the key, the shape of the URL with the id hidden) and to log a tool error instead of hiding it. I also found that a failed history call was passed to the AI as a quiet error, so the agent carried on without its memory; it is now logged.

## 6. Extend the agent to cover the whole monitoring question

**Prompt (in short):** the README lists volume, relative strength, cross-asset relationships and forward scenarios as not implemented, "I am planning to implement it". Also: add the Planner and Planner + reviewer loop from my diagrams, and add a Google Sheet tab that shows the API data.

**Result:** a new tool `get_cross_asset_summary`, volume and trend added to `get_market_summary`, four new playbook sections, a code guardrail that context never alerts alone, three new Sheet tabs, nine more scenarios, 65 more offline tests, and a richer brief (context, base rate, conditional watch scenarios).

**What the AI got wrong or I changed:**
- The diagram said "max 4 steps" for the plan. The project needs at least 6 (three coins, cross-asset, memory, one playbook search), so the limit is 10 and the diagram labels in the README say so.
- "Forward scenarios" was first read as price forecasts. It was changed to conditional watch scenarios: observable triggers, what the agent does, and an invalidation, with no probability and no price forecast, because the project is education only.
- My first tests had arithmetic mistakes of my own (a relative-strength fixture that returned 10.34% instead of 10%, a "mixed" trend fixture whose 30-day change was positive, and a percentile expectation of 95 where the rank formula gives 96). The tests were corrected, not the code.
- The 16-scenario run costs about 190,000 tokens, which is the free daily limit, so `--only` and `--publish` were added instead of running everything at once.
- The Planner + reviewer live run was cancelled by Groq's rate limit and gave no result. It is recorded as no result, not as a pass.
- The brief was reviewed as a rendered screenshot. The rank bar chart had clipped labels and overlapping threshold text, so it became a heatmap. The 15-row trigger table became a 3-row matrix.

