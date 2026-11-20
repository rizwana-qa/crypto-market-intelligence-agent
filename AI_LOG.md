# AI log

The five prompts that mattered most, and what the AI got wrong or I changed by hand.

TODO(you): this log must be true to what you did. Entries 1 to 3 below record real events from the first build session. Edit them into your own words, and replace entries 4 and 5 with your own prompts as you work (the reviewer and the planner, the first live run, the red run). Write down every time you changed the AI's output by hand.

## 1. Plan the shortest route

**Prompt:** "Act as python expert engineer and tell me the best and easiest way to finish this assignment and start doing and sharing steps in easy ways. We have only 1 hour to complete."

**Result:** a time-boxed plan: one `agent.py` with modes, thresholds taken from percentile ranks of each coin's own history, constants at the top for live changes, and the Sheet key carried inside `SHEET_URL` so only two secrets exist.

**What the AI got wrong or I changed:** the first plan was written before the Assignment 1 code was read. It assumed the Kraken fetch from Assignment 1 would work for three coins. It does not: Assignment 1 refuses any result key it has not verified, and only knew `XBTUSD` and `ETHUSD`. The XRP key (`XXRPZUSD`) had to be checked against Kraken's `AssetPairs` endpoint first.

## 2. Build Part A and Part B

**Prompt:** TODO(you): the prompt you gave for the tools, loop, guardrails, reviewer and planner.

**What the AI got wrong or I changed:** the first test run had two failing tests. Both were mistakes in the tests, not in the code. One "allowed" sentence contained the word "advice", which the filter blocks on purpose. In the other, the scripted AI never called a tool, so the "no valid market data" guardrail correctly blocked the alert. The tests were corrected, not the guardrails.

## 3. Prove that the tests can fail

**Prompt:** TODO(you): your prompt asking to check that the tests catch broken guardrails.

**What the AI got wrong or I changed:** a first mutation check reported "Ran 51 tests" without showing the verdict line, so it proved nothing. It was repeated with the verdict visible. Five deliberate breaks were all caught: an emptied advice list, a disabled source check, a removed no-market-data guard, a broken step limit and a removed watchlist check.

## 4. TODO(you)

**Prompt:**

**What the AI got wrong or I changed:**

## 5. TODO(you)

**Prompt:**

**What the AI got wrong or I changed:**
