# Project purpose

This is a **learning app first**. It teaches complete beginners to trade, step by step, with pretend money and
real prices. The user learns as they go: short lessons, quick quizzes, hands-on practice challenges, and
explanations of their own trades. Topics get more complex level by level, and the app's features unlock as the
matching topic is taught.

## The course (`backend/app/learn.py`)

Six levels: 1 basics → 2 protecting your money → 3 reading charts → 4 indicators → 5 strategies & backtesting →
6 automation & your trading plan. A level is complete when every lesson quiz is answered correctly and every
non-bonus challenge is done; that unlocks the next level. Level N's features are usable while on level N
(frontend: `data-unlock="N"`, hidden until `body.unlock-N`). "Unlock everything" exists for experienced users.

Challenges complete automatically from real actions: the engine calls `engine.learn.record*` on fills, risk and
bracket changes, auto-trading, AI analysis, backtests and explanations. Browser-only actions (viewing charts,
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
- **Protect the user by default.** Conservative risk limits, auto-trading off until level 6 and opted in, no
  implied guaranteed profit. This is paper trading.
- **Keep the curriculum valid.** `backend/tests/test_learn.py` checks that every glossary link resolves, quiz
  answers are valid and challenge events exist. Run the tests after editing content.

See `CONTRACT.md` for module interfaces and `README.md` for setup.
