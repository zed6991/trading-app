"""LLM research analyst (Level 12): SEC filings and news, turned into checked, structured data.

- Filings come from SEC EDGAR (free; SEC asks for a User-Agent with contact details: set SEC_USER_AGENT).
- An LLM summarises a filing into JSON: summary, sentiment (-1..1), key numbers each with a verbatim quote, risks,
  guidance. Every quote is checked against the source text, and every number in the summary must appear in the
  source. Anything that fails is flagged as a possible hallucination.
- The event study scores several past earnings releases and compares the score with the stock's move over the next
  days. "Anonymised" mode hides the company, ticker and dates, because an LLM may already know what happened next
  (it was trained on the future of these dates), which would make the study look better than it is.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
from datetime import datetime, timezone

import httpx
import numpy as np

from app import assets, prices
from app.llm import LLMRouter

SEC_UA = os.environ.get("SEC_USER_AGENT", "QuantPath learning app admin@quantpath.local")
NEWS_URL = "https://query1.finance.yahoo.com/v1/finance/search"
MAX_CHARS = 12_000
CACHE_TTL = 24 * 3600
_ticker_map: dict[str, dict] = {}
_sec_lock = asyncio.Semaphore(4)  # SEC asks for no more than 10 requests a second
_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


# ------------------------------------------------------------------------------------------ EDGAR
async def _sec_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    async with _sec_lock:
        resp = await client.get(url, headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})
        await asyncio.sleep(0.12)
    if resp.status_code == 403:
        raise ValueError("SEC EDGAR refused the request. Set SEC_USER_AGENT in .env to 'Your Name your@email.com'.")
    resp.raise_for_status()
    return resp


async def company(ticker: str) -> dict:
    """{cik, name, ticker} for a US ticker."""
    ticker = ticker.upper()
    if assets.is_crypto(ticker):
        raise ValueError("Crypto has no SEC filings. Pick a US company.")
    if not _ticker_map:
        async with httpx.AsyncClient(timeout=20) as client:
            data = (await _sec_get(client, "https://www.sec.gov/files/company_tickers.json")).json()
        for row in data.values():
            _ticker_map[row["ticker"].upper()] = {"cik": int(row["cik_str"]), "name": row["title"], "ticker": row["ticker"].upper()}
    found = _ticker_map.get(ticker)
    if not found:
        raise ValueError(f"{ticker} isn't an SEC-registered company (ETFs like SPY don't file 10-Ks). Try AAPL or NVDA.")
    return found


async def filings(ticker: str, forms: tuple[str, ...] = ("8-K", "10-Q", "10-K"), limit: int = 20,
                  earnings_only: bool = False) -> dict:
    """Recent filings for a company, newest first."""
    co = await company(ticker)
    async with httpx.AsyncClient(timeout=20) as client:
        data = (await _sec_get(client, f"https://data.sec.gov/submissions/CIK{co['cik']:010d}.json")).json()
    r = data["filings"]["recent"]
    out = []
    for i in range(len(r["form"])):
        form = r["form"][i]
        if form not in forms:
            continue
        items = r.get("items", [""] * len(r["form"]))[i] or ""
        is_earnings = form == "8-K" and "2.02" in items
        if earnings_only and not is_earnings:
            continue
        out.append({"form": form, "date": r["filingDate"][i], "accession": r["accessionNumber"][i],
                    "document": r["primaryDocument"][i], "items": items, "earnings": is_earnings,
                    "label": {"8-K": "Earnings release" if is_earnings else "Current report (8-K)",
                              "10-Q": "Quarterly report (10-Q)", "10-K": "Annual report (10-K)"}[form]})
        if len(out) >= limit:
            break
    return {**co, "filings": out}


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", raw)
    raw = re.sub(r"(?i)</td>|</th>", " | ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(raw).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def _focus(text: str, form: str) -> str:
    """The part of a long report worth reading: Management's Discussion and Analysis (MD&A)."""
    if form in ("10-Q", "10-K"):
        hits = [m.start() for m in re.finditer(r"(?i)management.s discussion and analysis", text)]
        if len(hits) >= 2:
            return text[hits[1]:hits[1] + MAX_CHARS]  # first hit is the table of contents
        if hits:
            return text[hits[0]:hits[0] + MAX_CHARS]
    return text[:MAX_CHARS]


async def filing_text(cik: int, accession: str, document: str, form: str) -> dict:
    """Plain text of a filing. For an 8-K, prefer the press release exhibit (99.1), where the numbers are."""
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    async with httpx.AsyncClient(timeout=30) as client:
        doc = document
        if form == "8-K":
            try:
                page = (await _sec_get(client, f"{folder}/{accession}-index.htm")).text
                for row in re.split(r"(?i)<tr", page):
                    if re.search(r">\s*EX-99\.?1?\s*<", row):
                        m = re.search(r'href="(/Archives/[^"]+\.(?:htm|html|txt))"', row)
                        if m:
                            doc = m.group(1).rsplit("/", 1)[1]
                            break
            except (httpx.HTTPError, ValueError):
                pass
        raw = (await _sec_get(client, f"{folder}/{doc}")).text
    text = html_to_text(raw)
    return {"text": _focus(text, form), "full_chars": len(text), "url": f"{folder}/{doc}"}


# ------------------------------------------------------------------------------------------ verification
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _digits(s: str) -> str:
    return re.sub(r"[^\d.]", "", s or "")


def verify_extraction(raw: dict, source: str) -> dict:
    """Mark each key number verified only if its quote is really in the source and contains the value's digits;
    flag summary numbers that don't appear in the source."""
    src = _norm(source)
    src_digits = set(_digits(m) for m in _NUM.findall(source))
    numbers = []
    for item in (raw.get("key_numbers") or [])[:10]:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "")
        value = str(item.get("value") or "")
        in_source = len(quote) >= 8 and _norm(quote) in src
        vd = _digits(value).rstrip(".")
        value_in_quote = bool(vd) and vd.replace(".", "") in _digits(quote).replace(".", "")
        numbers.append({"label": str(item.get("label") or "")[:80], "value": value[:40], "quote": quote[:300],
                        "verified": bool(in_source and value_in_quote),
                        "problem": None if in_source and value_in_quote else
                        ("quote not found in the filing" if not in_source else "value doesn't match its quote")})
    summary = str(raw.get("summary") or "")[:900]
    unsupported = []
    for n in _NUM.findall(summary):
        d = _digits(n).rstrip(".")
        if len(d.replace(".", "")) < 2:
            continue
        if d not in src_digits and not any(d in s for s in src_digits):
            unsupported.append(n)
    try:
        sentiment = max(-1.0, min(1.0, float(raw.get("sentiment"))))
    except (TypeError, ValueError):
        sentiment = 0.0
    checked = len(numbers)
    ok = sum(n["verified"] for n in numbers)
    return {
        "summary": summary, "sentiment": round(sentiment, 2),
        "risks": [str(x)[:200] for x in (raw.get("risks") or [])][:5],
        "guidance": (str(raw["guidance"])[:300] if raw.get("guidance") else None),
        "key_numbers": numbers, "verified": ok, "checked": checked,
        "unsupported_summary_numbers": unsupported,
        "trust": "high" if checked and ok == checked and not unsupported else "check" if ok else "low",
    }


EXTRACT_SYSTEM = (
    "You are a careful equity research analyst. You read ONE company document and extract facts for a beginner. "
    "Use ONLY the document text you are given: never use outside knowledge, never guess, never invent numbers. "
    "Respond with ONLY strict JSON: "
    '{"summary": "3 short plain-English sentences: what happened and why it matters", '
    '"sentiment": number from -1 (very negative for the stock) to 1 (very positive), '
    '"key_numbers": [{"label": "what it is", "value": "the number as written", '
    '"quote": "an EXACT word-for-word quote of 8-25 words from the document that contains the number"}] (up to 6), '
    '"risks": ["up to 3 short risks mentioned in the document"], '
    '"guidance": "what the company said about the future, or null"}. '
    '"value" is just the number as written (e.g. "$13.4 billion" or "7%"). Inside strings, write any quotation marks '
    "as single quotes so the JSON stays valid."
)


def anonymise(text: str, names: list[str]) -> str:
    """Hide who and when: company names/tickers -> [COMPANY], years -> [YEAR], month names -> [MONTH]."""
    for n in sorted({n for n in names if n and len(n) >= 2}, key=len, reverse=True):
        text = re.sub(re.escape(n), "[COMPANY]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(19|20)\d{2}\b", "[YEAR]", text)
    text = re.sub(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b",
                  "[MONTH]", text)
    return text


def _short_name(name: str) -> list[str]:
    base = re.sub(r"(?i)[,.]?\s*(inc|corp|corporation|co|company|ltd|plc|holdings|group)\.?$", "", name).strip()
    first = base.split()[0] if base else name
    return [name, base, first]


# ------------------------------------------------------------------------------------------ analyst
class Researcher:
    def __init__(self, db, llm: LLMRouter):
        self.db = db
        self.llm = llm

    def _cache_get(self, key: str):
        hit = self.db.get_kv(f"research:{key}")
        if isinstance(hit, dict) and time.time() - hit.get("at", 0) < CACHE_TTL * 30:
            return hit["value"]
        return None

    def _cache_set(self, key: str, value) -> None:
        self.db.set_kv(f"research:{key}", {"at": time.time(), "value": value})

    async def analyse_filing(self, ticker: str, accession: str, anonymous: bool = False) -> dict:
        info = await filings(ticker, limit=60)
        f = next((x for x in info["filings"] if x["accession"] == accession), None)
        if not f:
            raise KeyError(f"Filing {accession} not found for {ticker}")
        key = f"filing:{accession}:{int(anonymous)}"
        cached = self._cache_get(key)
        if cached:
            return {**cached, "cached": True}
        doc = await filing_text(info["cik"], accession, f["document"], f["form"])
        text = doc["text"]
        shown = anonymise(text, _short_name(info["name"]) + [info["ticker"]]) if anonymous else text
        msgs = [{"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": f"Document type: {f['label']}.\n\nDOCUMENT:\n{shown}\n\nReturn the JSON now."}]
        reply = await self.llm.chat("research", msgs, max_tokens=2000, temperature=0.1)
        checked = verify_extraction(reply["json"] or {}, shown)
        out = {"ticker": info["ticker"], "company": info["name"], "filing": f, "url": doc["url"],
               "chars_read": len(text), "full_chars": doc["full_chars"], "anonymous": anonymous,
               "source": reply["source"], "cost_usd": reply["cost_usd"], "latency_ms": reply["latency_ms"], **checked}
        self._cache_set(key, out)
        return out

    async def news(self, ticker: str) -> dict:
        ticker = ticker.upper()
        query = assets.name(ticker) if assets.is_crypto(ticker) else ticker
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            data = (await client.get(NEWS_URL, params={"q": query, "newsCount": 10, "quotesCount": 0})).json()
        items = [{"title": n.get("title", ""), "publisher": n.get("publisher", ""), "url": n.get("link", ""),
                  "time": int(n.get("providerPublishTime") or 0)} for n in data.get("news", []) if n.get("title")][:10]
        if not items:
            return {"ticker": ticker, "items": [], "average": None, "source": None}
        key = "news:" + ticker + ":" + str(abs(hash(tuple(i["title"] for i in items))))
        cached = self._cache_get(key)
        if cached:
            return cached
        listing = "\n".join(f"{i + 1}. {it['title']}" for i, it in enumerate(items))
        msgs = [{"role": "system", "content": (
            "Score each news headline for how good or bad it is for the stock of the named company, from -1 (very bad) "
            "to 1 (very good); 0 if unrelated or unclear. Judge only the headline text. Respond with ONLY strict JSON: "
            '{"scores": [{"n": 1, "score": 0.0, "why": "max 12 words"}]}')},
            {"role": "user", "content": f"Company: {assets.name(ticker)} ({ticker}).\nHeadlines:\n{listing}"}]
        reply = await self.llm.chat("research", msgs, max_tokens=900, temperature=0.1)
        scores = {int(s.get("n", 0)): s for s in (reply["json"] or {}).get("scores", []) if isinstance(s, dict)}
        for i, it in enumerate(items):
            s = scores.get(i + 1, {})
            try:
                it["score"] = max(-1.0, min(1.0, float(s.get("score", 0))))
            except (TypeError, ValueError):
                it["score"] = 0.0
            it["why"] = str(s.get("why", ""))[:120]
        out = {"ticker": ticker, "items": items, "average": round(float(np.mean([i["score"] for i in items])), 2),
               "source": reply["source"], "cost_usd": reply["cost_usd"]}
        self._cache_set(key, out)
        return out

    async def memory_test(self, ticker: str, date: str) -> dict:
        """Ask the model, with NO documents, what happened to the stock after `date`. If it 'knows', backtests of
        LLM signals on that period are contaminated."""
        name = assets.name(ticker)
        msgs = [{"role": "system", "content": "Answer from your own knowledge only. Be honest if you don't know. "
                                              'Respond with ONLY JSON: {"knows": true|false, "answer": "2-3 sentences"}'},
                {"role": "user", "content": f"What happened to {name} ({ticker}) stock in the weeks after its earnings "
                                            f"report around {date}? Did it go up or down?"}]
        reply = await self.llm.chat("research", msgs, max_tokens=300, temperature=0.0)
        j = reply["json"] or {}
        return {"ticker": ticker, "date": date, "knows": bool(j.get("knows")), "answer": str(j.get("answer", ""))[:600],
                "source": reply["source"], "cost_usd": reply["cost_usd"]}

    async def event_study(self, ticker: str, count: int = 6, horizon: int = 5, anonymous: bool = True) -> dict:
        """Score the last `count` earnings releases and compare with the stock's return over the next `horizon` days."""
        if not 3 <= count <= 12:
            raise ValueError("Use between 3 and 12 earnings releases.")
        info = await filings(ticker, forms=("8-K",), limit=count, earnings_only=True)
        if len(info["filings"]) < 3:
            raise ValueError(f"Not enough earnings releases found for {ticker}.")
        candles = await prices.cached_history(ticker, "1d", 252 * 6 + 1)
        days = [datetime.fromtimestamp(c["time"], tz=timezone.utc).strftime("%Y-%m-%d") for c in candles]
        rows, cost = [], 0.0
        for f in info["filings"]:
            a = await self.analyse_filing(ticker, f["accession"], anonymous)
            cost += a.get("cost_usd") or 0.0
            # Filed after the close, usually: enter at the next day's close to be safe.
            idx = next((i for i, d in enumerate(days) if d > f["date"]), None)
            move = None
            if idx is not None and idx + horizon < len(candles):
                move = (candles[idx + horizon]["close"] / candles[idx]["close"] - 1) * 100
            rows.append({"date": f["date"], "accession": f["accession"], "sentiment": a["sentiment"],
                         "summary": a["summary"], "trust": a["trust"],
                         "move_pct": round(move, 2) if move is not None else None})
        pairs = [(r["sentiment"], r["move_pct"]) for r in rows if r["move_pct"] is not None]
        corr = None
        hit = None
        if len(pairs) >= 3:
            s, m = np.array(pairs).T
            if s.std() > 0 and m.std() > 0:
                corr = round(float(np.corrcoef(s, m)[0, 1]), 2)
            hit = round(float(np.mean([(si > 0) == (mi > 0) for si, mi in pairs if si != 0])) * 100, 0) if any(p[0] for p in pairs) else None
        verdict = (f"With only {len(pairs)} events, any pattern here is mostly noise: you'd need dozens of companies "
                   "and years of releases before trusting it.")
        if corr is not None:
            verdict = f"Correlation between the AI's score and the next {horizon} days' move: {corr:+.2f}. " + verdict
        if not anonymous:
            verdict += (" This run showed the AI the company name and dates. It may remember what happened next, which "
                        "would make the result look better than it really is. Compare with the anonymised run.")
        self.db.set_kv("research:last_study", {"ticker": ticker, "anonymous": anonymous, "corr": corr})
        return {"ticker": ticker.upper(), "company": info["name"], "horizon": horizon, "anonymous": anonymous,
                "rows": rows, "correlation": corr, "same_direction_pct": hit, "events": len(pairs),
                "cost_usd": round(cost, 6), "verdict": verdict}
