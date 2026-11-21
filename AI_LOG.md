# AI log

The prompts that mattered most, and what the AI got wrong or I changed by hand.

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

## 7. More tools, free sources only

**Prompt (in short):** add the extra tools you proposed (liquidity, derivatives, implied volatility, market-wide volume, sentiment, a price cross-check, alert statistics), but I will not use any paid API key.

**Result:** a new `sources.py` that reads only free sources (Kraken order book and Kraken Futures, Deribit DVOL, alternative.me, and CoinGecko through its free Demo key, which is optional), two new agent tools (`get_market_context`, `get_alert_history_stats`) instead of seven so the free Groq token cap is respected, seven playbook sections, four more scenarios, a `context_data` Sheet tab, a market context section in the brief with the sources' attribution, a manual `Data sources check` workflow that uses no AI tokens, and 57 more offline tests.

**What the AI got wrong or I changed:**
- Adding two tools raised the number of AI calls a loop can need to 9 or more, so the step limit of 8 would have failed a run that calls one tool at a time. The loop limit is now 12 and the plan limit 12.
- Seven tools would have made every prompt longer and burned the free daily token cap, so related data was merged into one tool.
- My first test run had three failures. Two were arithmetic slips of my own in the expected values (a DVOL rank that forgot an equal value counts half, and an ask-depth sum that was off by 228). The tests were corrected, not the code. The third was a real bug in the code: a price gap of exactly 1% was flagged as a disagreement because of floating-point noise. It is fixed and tested.
- The build environment cannot reach the data sources, so the sample replies were written from the documentation. The manual data check on GitHub settled what they could not: the funding rate and open interest units, the XRP futures symbol and CoinGecko's XRP id all matched. It also found a flaw the samples hid: Kraken's default 100 order-book levels did not reach 1% from the price for any coin, so "depth within 1%" covered less than it said and bid and ask were not comparable. The code now fetches up to 500 levels, uses one band for both sides and reports it.


## 8. Which scenarios to run within the free token cap

**Prompt (in short):** as a data analyst and market adviser, pick the scenarios worth running in a day within the token capacity, estimate them, run them live, and update the README with the new APIs and what each is for.

**Result:** the run log and the RESULT line now print the tokens Groq reports (`llm_tokens`), so planning uses measured numbers. Scenarios 17 to 20 and 9 were run with the real AI and passed. The README has a "New APIs in this version and what each one is for" table and a "Token budget for scenario runs" section with a ranked four-day plan.

**What the AI got wrong or I changed:**
- My estimate was about 13,000 to 15,000 tokens per scenario. The measured average is about 23,000 (13,592 for a calm scenario, 26,000 to 30,000 when the agent writes an alert). The plan was rebuilt on the measured numbers and now allows about six scenarios a day, not ten.
- The README said a full `--measure` costs about 1 million tokens. That was too low: 12 runs of 20 scenarios at about 23,000 tokens is more than 5 million. It now says so and says it needs the paid tier.
- Scenario 10 did not finish: Groq's free daily cap was reached (197,808 of 200,000 used, about 75,000 of it from the previous day's runs, which I had not counted). It is recorded as "did not finish" in the README and in the Sheet, not as a pass or a fail of the agent. I should have checked the remaining allowance before the second batch.

## 9. Showing the raw data and the columns

**Prompt (in short):** show the data fetched from Kraken, either as a Google Sheet tab or as a CSV saved by the code, and list which factors and columns are fetched and saved.

**Result:** `export_data.py` saves three CSV files: every Kraken candle with all 8 values, every factor the agent calculates per day with its rank, and a file that explains each column. A new README section lists the columns of the CSV files and of all five Sheet tabs.

**What the AI got wrong or I changed:**
- I had never given the raw data. The earlier request for "a tab that shows the API data" was answered with the `market_data` tab, which holds calculated numbers, and the agent keeps only 3 of the 8 values Kraken sends. The CSV export is the missing piece.
- The first version of the factors file rounded prices to 4 decimals, which turned XRP's 0.95904 into 0.9590. The live run showed it, so prices and volumes now keep up to 12 significant digits and a test covers it.
- A first check of the tests by breaking the code on purpose found one weak test: swapping two rank columns was not caught, because the sample data gave the same rank in both. The test now uses days where the three ranks differ, and checks that such days exist.
- The Agent tab shows only the coins named in an alert, which looked like "2 coins instead of 3". That is how it was built, not a missing coin. The `market_data` tab has all three coins on every run.

## 10. Checking that the Kraken data is correct

**Prompt (in short):** check that the data fetched from Kraken is correct and authentic, and say exactly what was checked.

**Result:** `validate_data.py` runs 16 checks per coin on the reply, the timestamps, the values, the warnings and an independent price comparison with CoinGecko. `tests/test_validate.py` injects the faults one at a time, now 43 of them. The live run found 47 PASS, 1 WARN and 0 FAIL. The WARN is XRP on 2025-03-02, a +34.3% day that is a real market event.

**What the AI got wrong or I changed:**
- Several of the first expectations of which faults the agent's own checks already refuse were wrong. The test now feeds every fault to the agent's own parser and records the answer, so the table in the README comes from a run and not from a guess.
- Breaking the validator on purpose (letting a candle dated in the future through) made 2 tests fail. That is the proof the tests can fail.
- The validator checks one coin at a time and had no check across coins. That gap is closed in entry 11.

## 11. Fetch-data scenarios and a stricter data gate

**Prompt (in short):** I shared 12 test scenarios for two coins (FD-01 to FD-12: negative trade count, high below low, close above high, wrong Kraken pair key, duplicate and out-of-order candles, missing date, valid BTC and ETH responses, future candle, BTC and ETH on different dates) and asked for all three coins to be covered with a strong traceability matrix whose coverage is measured.

**Result:** `tests/test_fetch_data.py` has 45 scenarios (the 12 with my IDs and wording, 33 more), each run for BTC, ETH and XRP through the real fetch code, the agent's gate and the validator. The traceability matrix in the README is generated from those runs, and a test fails when the README is out of date. Coverage is measured with Python's own `trace` module: every executable line of the gate and the audit functions runs.

**What the AI got wrong or I changed:**
- The scenarios showed that the agent's own gate accepted six of my twelve faults on the daily path (a negative trade count, high below low, close above high, a duplicate or out-of-order unfinished candle, a candle dated in the future). Only the validator caught them, and the validator does not run before the daily alert. The agent's gate is now stricter and refuses all six. This changes what the daily run accepts, so the pull request waits for a successful daily run.
- There was no rule that BTC, ETH and XRP end on the same day (FD-12). The agent now refuses it, and the validator has a matching check, `coins_same_dates`, reported once across the coins.
- The first measured coverage was below 100%. The trace named the lines that no scenario reached, and FD-36 to FD-45 were added for them (a close that is a word or `true`, the real CoinGecko reply with malformed points, a reply with no prices, the other coins unavailable).
- The coverage tool most people use (`coverage`) is not installed here, and I did not install it without asking, so the measurement uses the standard library.
- Six deliberate breaks of the gate were each caught, by the scenarios that trace to that rule. Re-running four of them with bytecode caching off gave the same answer.

## 12. All the useful columns from Kraken, and the live-analysis framing

**Prompt (in short):** the Kraken CSV should carry every important and potential column that helps market analysis, not only basic factors: think as a data scientist and a trader who wants every risk, alert and warning visible. A second message said not to stay inside the "education only" boundary and to treat the project as live market analysis.

**Result:** `features.py` builds 87 columns per coin per completed day from Kraken's 8 values, and `export_data.py` writes them to `market_features.csv`. They cover returns, candle shape, volatility (including the Parkinson and Garman-Klass estimators), trend and momentum, drawdown, tail risk, liquidity, the link to Bitcoin, ranks against the coin's own history and descriptive labels. Every column is explained in `columns_explained.csv`, which is generated from the same list, so the two cannot drift apart. The README now describes the project as live market analysis and decision support, with "not investment advice" kept.

**What the AI got wrong or I changed:**
- The agent still does not give buy or sell instructions, and the advice filter stays. The alert rank fires on about 8 to 14% of days and has no test of forward predictive power, so an instruction built on it would claim more than the evidence shows. What the agent gives instead is the risk picture, warnings, alerts and what would change the view. A separate, labelled "market view" field is possible if I ask for it.
- My first RSI check used a table of values from memory and differed from the formula by 0.03 to 0.07 on every day. I could not tell whether the table or the formula was wrong, so I compared the columns with pandas, numpy and scipy instead: 133 comparisons, largest relative difference 3e-13. Those libraries were already in the sandbox and are not project dependencies.
- The export test found a real mismatch: my own 30-day average distance had a value on day 30 where the agent's has none until day 31. The three alert metrics now come from the agent's own functions, so the file cannot disagree with the agent.
- One of my tests expected a blank rank where a value is correct (it needs 90 days of the range, not 120), so the test was corrected, not the code.
- Mutation testing found two formulas the tests did not pin (one term of the true range, and the sign of a flat day in the signed volume balance). Two tests were added, and all 14 breaks are now caught.
- My own mutation script left a stale compiled file (a break of the same size, written within the same second), which made a correct test look broken. I re-ran with bytecode caching off.


## 13. An interactive executive brief, a headline subject and an honest outlook

**Prompt (in short):** make the HTML report read like a news desk covering remarkable statistics, with charts that are dynamic, the top 3 headlines, and at the bottom a forward-looking analysis as a data scientist would write it. Email it once a day at the end of the run with an impactful subject. Show "Created by Rizwana Zahoor" and nothing else about the course. I asked to see a design preview first, approved it, and then asked for it to be built.

**Result:** `report.py` now draws interactive plotly.js charts (hover, zoom, legend toggle, range tabs) and has a hero, the top 3 headlines, stat tiles and a section called "What the history says". `insights.py` computes the event study, the three headlines and the email subject from the day's numbers. The email carries the headlines and the AI's analysis, with the interactive page attached.

**What the AI got wrong or I changed:**
- The outlook is not a forecast. On the real history (20 episodes of the alert rank) the rank persists (90.5% at the alert level the next day, 53.3% a week later) but the moves that follow are spread widely (30 days later: median +2.4%, middle half -7.1% to +14.7%, 17 episodes). The page says "Stress is sticky. Direction is not." only because those numbers support it, shows the sample size next to every statistic, prints four caveats and hides the statistics when there are fewer than 8 episodes. I did not add any buy or sell call.
- The rarity sentence in the headline is computed from each coin's own history, not typed in. I re-checked it by hand from the exported file: on 20 November BTC's distance from its 30-day average (-20.46%) was matched or exceeded on 3 of 690 days, today included. A test pins the exact headline text.
- A test caught a banned word ("long") in a chart heading, so it became "How many days the alert rank lasts". The same check runs on every email subject, with a plain fallback.
- The plotly range buttons were invisible in the first design, so the page builds its own tabs. A CSS class clash, a "-0%" display, overlapping threshold labels, clipped chart labels and sideways scroll on a phone were fixed after checking the page in a real browser at desktop and phone width.
- Not solved: on an alert day the Sheet web app also sends its own short alert email, so there are two emails that day. Switching it off needs a change in `Code.gs` and a redeploy, so I left that as a decision.

## 14. The Ask page: RAG over my playbook, running on my own computer

**Prompt (in short):** a premium, executive-level interface where I can ask questions about my playbook, shown as a preview first. Then: implement it, and run it locally.

**Result:** `rag.py` searches the 18 playbook sections with BM25, optionally has the AI write a short answer from the top three, and checks it in code (cited sections must exist and have been retrieved, no advice word, quotes copied word for word by code). `rag_server.py` and `rag_ui.html` serve it on 127.0.0.1 only. `python rag.py --check` runs 26 offline checks and the workflow runs it after each daily report.

**What the AI got wrong or I changed:**
- Retrieval missed a plain question: "When does the agent raise an alert?" returned the base-rate section, because that section says "never raises an alert". I added a small synonym rule for raise, trigger and fire that applies to the question only, so the playbook and the agent's own search are unchanged. Measured now: the right section is first for 19 of 21 questions and in the top three for all 21. These questions were written against this playbook, so this is a regression test and not an accuracy claim.
- Screenshots showed three things the tests did not: topic text cut in the middle of a word, no space under the header (a `.wrap` rule overrode the `main` padding), and the answer pushed far down the phone screen by the topic list. All fixed. The related-questions list also offered the question I had just asked.
- My own browser test failed because the page's Content-Security-Policy blocks string evaluation. That was the policy working, so I changed the test, not the policy.
- My workflow edit had a colon inside a step name that made the YAML invalid. Validating all three workflow files with a YAML parser caught it before the push.
- Eight deliberate breaks (advice check off, Host check off, Origin check off, rate limit off, accepting a section that was not retrieved, advice requests not declined, synonyms off, `innerHTML` added) were each caught by a test.
- Not done: the AI's wording on the real model has not been run, because it needs the key, and the page does not read live numbers.


## 15. The first live email: the brief as the email body, and a link that can be trusted

**Prompt (in short):** after the first live run, with a screenshot: the HTML appears only as an attachment and the email body looks different, so show the finalized report directly in the email if possible. Where is the Ask page link? The email shows a local link that does not open.

**Result:** `emailbody.py` draws the brief as the email body (scoreboard, top 3 headlines, stat tiles, rank heatmap, a 30-day strip of the highest rank per coin, the AI's analysis, what the history says with bars, trigger levels, context, market context, decision history), using tables and inline styles because an email program runs no scripts. The interactive page stays attached. The Ask panel now says plainly that the Ask page runs on the computer, and `start_ask_page.bat` starts it in one click. If an https address is set (`REPORT_URL`, `ASK_URL`), that panel becomes a real button.

**What the AI got wrong or I changed:**
- My first email carried only the headlines and three numbers. I chose that because charts cannot run in email, but I did not offer the table-drawn version, and the owner had to ask for it.
- The AI's analysis was missing from the email on a day with no alert. The code showed that block only when the message was not empty, and on a no-alert day the message is empty. The screenshot showed it. The block now always shows the reasoning, the rules cited and the guardrail result, and a test covers the no-alert case.
- The link was a real hyperlink to 127.0.0.1. It looked clickable and could not open from an email, and cannot open at all on a phone. It is now text that says so, plus a launcher. I did not invent a hosted address: a working link needs the owner to publish one.
- The web app refused an email body above 30,000 characters. The full body is about 70 to 80 KB, so `Code.gs` now allows 100,000, and the sender retries once with a shorter body, with a warning, if an older script is still deployed. Gmail clips near 102 KB, so the renderer stays under 90 KB and drops the least important sections first.
- My contrast test failed: white text on my amber was 3.6 to 1. The email uses a darker amber (5.0 to 1). The page's heatmap still uses the lighter amber with white numbers, so that one is open.
- My advice-word test flagged "recommends" in an intro sentence of mine, and "recommendation" in the disclaimer that says there is none. I reworded the sentence and excluded only the disclaimer from the scan.
- Not done: the email has not been seen in Gmail, Outlook or a phone app. I rendered it in Chrome at 700 px and 375 px from demo data and from the real Kraken candles. Outlook for Windows and Gmail's dark mode can change colours and spacing.

