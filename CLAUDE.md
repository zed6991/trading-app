# Project purpose

This is a **learning app first**: "Zero to AI-powered quant trader". It teaches complete beginners to trade US
stocks, ETFs and crypto step by step, with pretend money and real prices, then takes them through statistics,
proper strategy testing and (on the roadmap) Python, ML and LLM agents. The user learns as they go: short lessons, quick quizzes, hands-on practice challenges, and
explanations of their own trades. Topics get more complex level by level, and the app's features unlock as the
matching topic is taught.

## The course (content in `backend/app/curriculum.py`, progress in `backend/app/learn.py`)

All 13 levels are built: 1 how markets work → 2 risk first → 3 charts and classic signals → 4 returns and volatility →
5 how assets move together → 6 edge or luck? → 7 testing strategies properly → 8 Python for quants → 9 your strategy
in code → 10 portfolios and factors → 11 machine-learning signals → 12 LLMs as research analysts → 13 agents and the
capstone. `ROADMAP` is for future levels (shown as "coming soon").

A level is complete when every lesson quiz is answered correctly and every non-bonus challenge is done; that unlocks
the next level. Level N's features are usable while on level N (frontend: `data-unlock="N"`, hidden until
`body.unlock-N`, gating CSS covers 2-13). Quiz choices are shuffled per question by `choice_order` and answers are
checked on the server. "Unlock everything" exists for experienced users.

Assets live in `backend/app/assets.py` (names, kind stock/etf/crypto, fees). Stocks come from Yahoo Finance
(`stocks.py`, polled, US market hours with holidays) and crypto from Binance (`market.py`); `engine.CombinedMarket`
joins them. The Lab (`frontend/lab.js`, maths in `quant.py`) works on daily data via `prices.cached_history`.

Challenges complete automatically from real actions: the engine calls `engine.learn.record*` on fills, risk and
bracket changes, limit and risk-sized orders, auto-trading, AI analysis, Lab runs, backtests and explanations. Browser-only actions (viewing charts,
toggling indicators, using the order ticket) go through `POST /api/learn/events` (whitelisted in `CLIENT_EVENTS`).

## Rules for every change

- **Teach, don't just decide.** Recommendations still end in a clear action, but say *why* in terms the user
  has learned, so they can make the call themselves next time.
- **Introduce jargon only when it's taught.** Every trading term shown in the UI must exist in `GLOSSARY` so it's
  tappable (lesson text uses `[[slug]]` / `[[slug|shown text]]`; dashboard labels are auto-linked by
  `frontend/learn.js`). Don't show a feature before the level that teaches it.
- **Plain English, short sentences.** Lessons are 2–4 short cards plus 2–3 quiz questions with a one-line "why".
  Explain with dollar examples before percentages and ratios.
- **Learning by doing.** New features should come with a practice challenge that uses them.
- **Review every trade.** Trade explanations (`rule_explanation` + optional AI rewording in `ai.py`) must keep
  the exact numbers, use only terms from the learner's level or below, and end with one lesson.
- **The coach teaches from facts.** `app/coach.py` computes stats and habits with rules; the LLM only rewords
  (reviews are checked with `numbers_match` and may not add points). Chat gets the learner's level, taught and
  untaught words, account, recent trades and current screen, and must never answer buy/sell with a yes/no.
  Keep the offline (no Ollama) path working.
- **Protect the user by default.** Conservative risk limits, auto-trading hidden until level 13 and opted in, no
  implied guaranteed profit. This is paper trading. The agent proposes and the learner approves (auto mode is
  opt-in at level 13); rules set trade sizes, the LLM may only flag concerns. Keep the kill switch working.
- **AI is local first.** All LLM calls go through `app/llm.py` (`engine.llm.chat(task, ...)`): Ollama by default,
  OpenRouter only for tasks the learner ticked, under their monthly cap, falling back to Ollama. Never send the key to
  the browser. Learner and AI code only runs through `codelab.run_code` (sandboxed, time-limited).
- **Check what the AI says.** Numbers an LLM produces must be verified against facts (`verify_extraction` for filings,
  `numbers_match` for reviews and the capstone) or clearly flagged.
- **Be honest about evidence.** Lab results compare against buy & hold and random traders, include costs, and say
  plainly when something looks like luck or overfitting.
- **Keep the curriculum valid.** `backend/tests/test_learn.py` checks that every glossary link resolves, quiz
  answers are valid and challenge events exist. Run the tests after editing content.

See `CONTRACT.md` for module interfaces and `README.md` for setup.
