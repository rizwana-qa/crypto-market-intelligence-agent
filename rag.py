"""Ask the playbook: retrieval and guarded answers over playbook.md. Runs on your own computer.

How it works, in four steps (standard library plus the agent's own Groq call, nothing else):
  1. Retrieval: every "## " section of playbook.md is a document. BM25 ranks them against the question, with the section title
     counting three times, and keeps the best three.
  2. Question guard: a request for advice or a price forecast is declined by code, without any AI call.
  3. Answer: the AI gets only the retrieved sections and must reply as JSON, a bottom line plus "why" points that each name
     a playbook section. Without GROQ_API_KEY the page shows the matching playbook text instead (no AI).
  4. Answer guard (code, not the AI): a point that cites a section that does not exist, or one that was not retrieved, is dropped;
     advice wording is rejected; a number that is not in the cited section, or a point whose key words mostly are not in it, makes
     the answer be withheld; an answer with no valid citation left is replaced by the retrieved text. The quote shown as
     evidence is picked by code from the section and is word for word.

Usage:
  python rag.py "Can an extreme Fear and Greed reading raise an alert?"
  python rag.py --check          # builds the index and runs the retrieval test set (no AI call, no network)
  python rag_server.py           # the Ask page in your browser
"""
import argparse
import json
import math
import re
import sys

import agent

TOP_K = 3                 # sections given to the AI and shown as evidence
MIN_SHARE = 0.25          # a section scoring under this share of the best score is not used
K1, B = 1.5, 0.75         # BM25 settings (the usual defaults)
TITLE_REPEAT = 3          # a match in the section title counts this many times
MAX_QUESTION = 300        # characters
MAX_BOTTOM_LINE = 240
MAX_WHY = 4
MIN_WORD_SUPPORT = 0.4    # share of a point's key words that must occur in the section it cites
NOT_IN_PLAYBOOK = "That is not in the playbook."
EXTRA_STOPWORDS = {"does", "can", "which", "would", "there", "their", "into", "than", "then", "also", "about", "used", "use", "using",
                   "say", "says", "tell", "explain", "mean", "means", "meaning", "have", "has", "was", "were", "been", "will", "your", "you",
                   "who", "why", "where", "give", "gives", "show", "shows", "much", "many", "each", "per"}
STOPWORDS = agent.STOPWORDS | EXTRA_STOPWORDS

# A request for advice or a forecast. The tool explains the playbook, so these are declined by code and never reach the AI.
ADVICE_REQUEST_RE = re.compile(
    r"\b(?:should\s+(?:i|we)|shall\s+(?:i|we)|can\s+i\s+(?:buy|sell|trade|invest)|good\s+time\s+to|worth\s+(?:buying|selling)|"
    r"will\s+(?:it|btc|eth|xrp|bitcoin|ether|the\s+price|prices?)\s+(?:go|rise|fall|drop|moon|pump|dump|crash|hit|reach)|"
    r"going\s+to\s+(?:rise|fall|drop|moon|pump|dump|crash)|price\s+(?:target|prediction)|predict|forecast|"
    r"(?:buy|sell)\s+(?:now|today|signal)|financial\s+advice|investment\s+advice)\b", re.IGNORECASE)
DECLINED = ("This page explains the playbook. It does not give advice to buy or sell, and it does not forecast prices.",
            "The agent only describes numbers and never tells anyone to act.")

RELATED = {   # follow-up questions offered after an answer, by the section that was cited first
    "Sentiment index": ["What does near threshold mean?", "Which words are banned in an alert message?"],
    "Alert rules": ["What does near threshold mean?", "When does the agent not repeat an alert?"],
    "Wording rules": ["How long can an alert message be?", "What happens when data is unavailable?"],
}
DEFAULT_RELATED = ["When does the agent raise an alert?", "Which words are banned in an alert message?", "What does near threshold mean?"]


# ------------------------------------------------------------------------------------------------
# 1. Retrieval
# ------------------------------------------------------------------------------------------------
def stem(word):
    """A light stemmer so rule/rules, rank/ranking/ranks and word/words/wording meet. It only has to be consistent."""
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zes")):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


def terms(text):
    found = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in STOPWORDS or (len(word) < 3 and not (word.isdigit() and len(word) >= 2)):
            continue
        found.append(stem(word))
    return found


# Words people use for "an alert fires" that the playbook words differently ("alert on a coin when ... breaches"). Applied to the
# question only, never to the playbook text, so the agent's own search and the daily run are not touched.
QUERY_SYNONYMS = {stem(word): ("breach", "threshold") for word in ("raise", "raised", "raising", "trigger", "triggered", "fire", "fires")}


class Index:
    """BM25 over the playbook sections."""

    def __init__(self, sections=None):
        self.sections = sections if sections is not None else agent.load_playbook()
        self.docs = [terms(s["title"]) * TITLE_REPEAT + terms(s["text"]) for s in self.sections]
        self.size = len(self.docs)
        self.average = sum(len(d) for d in self.docs) / self.size
        self.frequency = {}
        for doc in self.docs:
            for term in set(doc):
                self.frequency[term] = self.frequency.get(term, 0) + 1

    def idf(self, term):
        df = self.frequency.get(term, 0)
        return math.log(1 + (self.size - df + 0.5) / (df + 0.5))

    def scores(self, question):
        wanted = set(terms(question))
        for word in list(wanted):
            wanted.update(QUERY_SYNONYMS.get(word, ()))
        out = []
        for order, doc in enumerate(self.docs):
            length, score = len(doc), 0.0
            for term in wanted:
                tf = doc.count(term)
                if tf:
                    score += self.idf(term) * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self.average))
            out.append((score, order))
        return out

    def search(self, question, k=TOP_K):
        ranked = sorted(((s, o) for s, o in self.scores(question) if s > 0), key=lambda item: (-item[0], item[1]))
        if not ranked:
            return []
        floor = ranked[0][0] * MIN_SHARE
        return [{**self.sections[o], "score": round(s, 3), "order": o} for s, o in ranked[:k] if s >= floor]


def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -\t") for p in parts if len(p.strip(" -\t")) > 25 and not p.strip().lower().startswith("keywords:")]


def quote_for(section, question):
    """A sentence of the section, word for word, with the most words in common with the question. Picked by code."""
    wanted = set(terms(question))
    best, best_score = None, -1
    for sentence in sentences(section["text"]):
        score = len(wanted & set(terms(sentence)))
        if score > best_score:
            best, best_score = sentence, score
    return (best or section["text"][:200]).strip()


def clip(text, limit=120):
    """text cut at a word boundary, with an ellipsis, never in the middle of a word."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:(") + "\u2026"


def summary_line(section):
    """One short line for the topic list: the rule of the section when it has one."""
    for line in section["text"].splitlines():
        line = line.strip(" -")
        if line.lower().startswith("rule:"):
            line = line[5:].strip()
            return clip(line.split(". ")[0])
    found = sentences(section["text"])
    return clip(found[0] if found else section["title"])


# ------------------------------------------------------------------------------------------------
# 2. Answering
# ------------------------------------------------------------------------------------------------
SYSTEM = (
    "You answer questions about the Crypto Watch playbook. Use ONLY the playbook sections given below; never use outside knowledge. "
    "Reply with one JSON object and nothing else: "
    '{"bottom_line": "one or two plain sentences, at most 200 characters", '
    '"why": [{"text": "one sentence taken from the sections", "section": "exact section title"}], '
    '"related": ["up to 3 short follow-up questions the sections can answer"]}. '
    "Give 2 to 4 items in why. Every why item must name the exact title of one of the given sections. "
    f'If the sections do not answer the question, set bottom_line to "{NOT_IN_PLAYBOOK}" and why to an empty list. '
    "Describe the rules. Never give advice, trading instructions, forecasts or recommendations."
)


def clean_question(question):
    return re.sub(r"\s+", " ", str(question or "")).strip()[:MAX_QUESTION]


def evidence(hits, question, cited=None):
    cited = cited if cited is not None else [h["title"] for h in hits]
    return [{"n": i + 1, "title": h["title"], "quote": quote_for(h, question)} for i, h in enumerate(hits) if h["title"] in cited]


def guardrails(cites_exist=True, no_advice=True, from_retrieved=True, supported=True):
    checks = [("Cited sections exist", cites_exist), ("No advice wording", no_advice), ("Only the retrieved sections are used", from_retrieved),
              ("Numbers and key words match the cited sections", supported)]
    return {"checks": [{"name": n, "passed": p} for n, p in checks], "passed": sum(p for _, p in checks), "total": len(checks)}


def base_answer(question, kind, bottom_line, why, hits, **extra):
    related = RELATED.get(why[0]["section"] if why else (hits[0]["title"] if hits else ""), DEFAULT_RELATED)
    related = [q for q in related if q.lower().rstrip("?") != question.lower().rstrip("?")]
    out = {"question": question, "kind": kind, "bottom_line": bottom_line, "why": why, "related": related,
           "evidence": evidence(hits, question, [w["section"] for w in why] or None) if hits else [], "used_ai": False,
           "guardrails": guardrails(), "note": ""}
    out.update(extra)
    return out


def retrieval_only(question, hits, note):
    """What the code found, with no AI text: the best section's own wording, each point tied to its section."""
    why = [{"text": quote_for(h, question), "section": h["title"]} for h in hits[:3]]
    return base_answer(question, "retrieval", f"The closest playbook section is {hits[0]['title']}.", why, hits, note=note)


NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
                "ninety": 90, "hundred": 100, "thousand": 1000}


def number_forms(text, section=False):
    """Every number in the text as a plain string, so 1,000 and 1000.0 and 95% and 95 percent match. Spelled-out numbers count too.
    In a section the list numbers (1. 2. 3.) are not facts and are left out. In an answer a lone "one" is just a word and is left out."""
    if section:
        text = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
    found = set()
    for match in NUMBER_RE.findall(text):
        whole, _, fraction = match.replace(",", "").partition(".")
        fraction = fraction.rstrip("0")
        found.add((whole.lstrip("0") or "0") + ("." + fraction if fraction else ""))
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in NUMBER_WORDS and (section or word != "one"):
            found.add(str(NUMBER_WORDS[word]))
    return found


def word_support(text, section):
    """The share of the key words of text (the retrieval tokenizer and stemmer, numbers left out) that occur in the section."""
    words = {t for t in terms(text) if not t.isdigit() and t not in NUMBER_WORDS}
    known = set(terms(section["title"] + " " + section["text"]))
    return len(words & known) / len(words) if words else 0.0


def section_numbers(section):
    return number_forms(section["title"] + "\n" + section["text"], True)


def supported_by(text, section):
    """A cited point is backed by its section when each of its numbers is in the section and enough of its key words are too."""
    return number_forms(text) <= section_numbers(section) and word_support(text, section) >= MIN_WORD_SUPPORT


def unsupported(bottom, why, hits):
    """True when a point is not backed by the section it cites, or the bottom line has a number that none of the cited sections has."""
    by_title = {h["title"]: h for h in hits}
    if any(not supported_by(w["text"], by_title[w["section"]]) for w in why):
        return True
    return bool(why) and not number_forms(bottom) <= set().union(*(section_numbers(by_title[w["section"]]) for w in why))


def parse_answer(raw, hits):
    """(bottom_line, why, related, problems) from the AI's JSON. Only points that cite a retrieved section survive."""
    data = agent.extract_json(raw)
    titles = {h["title"].lower(): h["title"] for h in hits}
    everything = {s["title"].lower() for s in agent.load_playbook()}
    bottom = str(data.get("bottom_line") or "").strip()[:MAX_BOTTOM_LINE]
    why, problems = [], {"missing_section": 0, "not_retrieved": 0}
    for item in data.get("why") or []:
        if not isinstance(item, dict):
            continue
        text, section = str(item.get("text") or "").strip(), str(item.get("section") or "").strip().strip("[]")
        if not text:
            continue
        if section.lower() in titles:
            why.append({"text": text[:400], "section": titles[section.lower()]})
        elif section.lower() in everything:
            problems["not_retrieved"] += 1
        else:
            problems["missing_section"] += 1
    related = [str(q).strip() for q in (data.get("related") or []) if isinstance(q, str) and 5 < len(q.strip()) < 100][:3]
    return bottom, why[:MAX_WHY], related, problems


def ask(question, index=None, llm=None, ai=True):
    """The answer as a plain dict. llm is a function(messages) -> assistant message dict (the agent's Groq call by default)."""
    question = clean_question(question)
    if len(question) < 3:
        return {"question": question, "kind": "error", "bottom_line": "Type a question about the playbook.", "why": [], "related": DEFAULT_RELATED,
                "evidence": [], "used_ai": False, "guardrails": guardrails(), "note": ""}
    index = index or Index()
    if ADVICE_REQUEST_RE.search(question):
        wording = [s for s in index.sections if s["title"] == "Wording rules"]
        hits = [{**wording[0], "score": 0, "order": 0}] if wording else []
        why = [{"text": DECLINED[1], "section": "Wording rules"}] if hits else []
        return base_answer(question, "declined", DECLINED[0], why, hits)
    hits = index.search(question)
    if not hits:
        return base_answer(question, "no_match", NOT_IN_PLAYBOOK, [], [], note="No section of the playbook matches these words. Try other words.")
    if not ai:
        return retrieval_only(question, hits, "AI answers are off, so this shows the matching playbook text.")
    llm = llm or agent.call_llm
    sections = "\n\n".join(f"### {h['title']}\n{h['text']}" for h in hits)
    messages = [{"role": "system", "content": SYSTEM + "\n\nPlaybook sections:\n\n" + sections},
                {"role": "user", "content": question}]
    try:
        reply = llm(messages)
        bottom, why, related, problems = parse_answer(reply.get("content") or "", hits)
    except (agent.AgentError, agent.ShapeError) as error:
        return retrieval_only(question, hits, f"The AI answer was not used ({error}). This shows the matching playbook text.")
    wording_question = hits[0]["title"] == "Wording rules"      # the answer may then list the banned words, because that is the topic
    text = " ".join([bottom] + [w["text"] for w in why])
    advice = bool(agent.ADVICE_RE.search(text)) and not wording_question
    backed = not unsupported(bottom, why, hits)
    checks = guardrails(cites_exist=problems["missing_section"] == 0, no_advice=not advice, from_retrieved=problems["not_retrieved"] == 0, supported=backed)
    if advice:
        out = retrieval_only(question, hits, "The AI answer was withheld because it used advice wording. This shows the matching playbook text.")
        out["guardrails"] = checks
        return out
    if not backed:
        out = retrieval_only(question, hits, "The AI answer was withheld because a number or key words in it were not found in the section it cites. "
                                             "This shows the matching playbook text.")
        out["guardrails"] = checks
        return out
    if bottom.rstrip(".") == NOT_IN_PLAYBOOK.rstrip(".") and not why:
        return base_answer(question, "not_in_playbook", NOT_IN_PLAYBOOK, [], [], note="The retrieved sections do not answer this question.", used_ai=True)
    if not why or not bottom:
        out = retrieval_only(question, hits, "The AI answer had no valid citation, so it was not used. This shows the matching playbook text.")
        out["guardrails"] = checks
        return out
    related = [q for q in (related or RELATED.get(why[0]["section"], DEFAULT_RELATED)) if q.lower().rstrip("?") != question.lower().rstrip("?")]
    return base_answer(question, "answer", bottom, why, hits, related=related, used_ai=True, guardrails=checks)


def topics(index=None):
    index = index or Index()
    return [{"n": i + 1, "title": s["title"], "line": summary_line(s)} for i, s in enumerate(index.sections)]


def reference():
    """Facts from the agent's own settings, shown beside every answer so they cannot drift from the code."""
    return {"alert_rank": agent.ALERT_RANK, "near_rank": 90.0, "max_chars": agent.MAX_MESSAGE_CHARS, "banned_words": list(agent.BANNED_WORDS),
            "history_limit": agent.HISTORY_LIMIT, "min_history_days": agent.MIN_HISTORY_DAYS}


# ------------------------------------------------------------------------------------------------
# 3. The retrieval test set. `python rag.py --check` and tests/test_rag.py run it offline.
# ------------------------------------------------------------------------------------------------
CHECKS = [   # (question, the section that must be in the top three)
    ("Can an extreme Fear and Greed reading raise an alert?", "Sentiment index"),
    ("Which words are banned in an alert message?", "Wording rules"),
    ("How long can an alert message be?", "Wording rules"),
    ("What does near threshold mean?", "Alert rules"),
    ("When does the agent not repeat an alert?", "Repeat alerts"),
    ("What happens when Kraken data is stale or missing?", "Data quality"),
    ("How is 30-day volatility calculated?", "Volatility rule"),
    ("What is drawdown and how is it ranked?", "Drawdown rule"),
    ("How is the distance from the 30-day average defined?", "Trend against the 30-day average"),
    ("Does unusual volume raise an alert?", "Volume rule"),
    ("What is relative strength?", "Relative strength rule"),
    ("When are two coins unusually correlated?", "Correlation rule"),
    ("How is the trend label up, down or mixed decided?", "Trend label"),
    ("What does the order book spread tell us?", "Liquidity (order book)"),
    ("What does a positive funding rate mean?", "Derivatives (funding rate and open interest)"),
    ("What is DVOL implied volatility?", "Implied volatility"),
    ("What is CoinGecko market-wide volume used for?", "Market-wide volume"),
    ("What if Kraken and CoinGecko prices disagree?", "Price source check"),
    ("How often is the alert rank reached in history?", "Alert history base rate"),
    ("When does the agent raise an alert?", "Alert rules"),
    ("What triggers an alert?", "Alert rules"),
]
NO_MATCH = ["what is the weather in Dubai tomorrow", "zzz qqq xxx"]
DECLINE = ["Should I buy BTC now?", "Will bitcoin go up next week?", "Give me a price target for ETH"]


def run_checks(index=None):
    """[(passed, text)] for every case. Retrieval only: no AI, no network."""
    index = index or Index()
    results = []
    for question, expected in CHECKS:
        titles = [h["title"] for h in index.search(question)]
        results.append((expected in titles, f"{question!r} -> {expected}" + ("" if expected in titles else f" (got {titles})")))
    for question in NO_MATCH:
        hits = index.search(question)
        results.append((not hits, f"{question!r} -> no section" + ("" if not hits else f" (got {[h['title'] for h in hits]})")))
    for question in DECLINE:
        answer = ask(question, index, llm=lambda m: (_ for _ in ()).throw(AssertionError("the AI must not be called")))
        results.append((answer["kind"] == "declined", f"{question!r} -> declined by code"))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ask the playbook.")
    parser.add_argument("question", nargs="*")
    parser.add_argument("--check", action="store_true", help="run the retrieval test set (no AI call)")
    parser.add_argument("--no-ai", action="store_true", help="show the matching playbook text only")
    args = parser.parse_args(argv)
    index = Index()
    if args.check:
        results = run_checks(index)
        for passed, text in results:
            print(("PASS " if passed else "FAIL ") + text)
        failed = sum(1 for passed, _ in results if not passed)
        print(f"[rag] {len(index.sections)} sections indexed; {len(results) - failed} of {len(results)} retrieval checks passed")
        return 1 if failed else 0
    if not args.question:
        parser.error("give a question, or use --check")
    ai = not args.no_ai and bool(__import__("os").environ.get("GROQ_API_KEY"))
    print(json.dumps(ask(" ".join(args.question), index, ai=ai), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
