# Project purpose

This project exists to make trading accessible to anyone — including people with zero trading knowledge,
no finance background, and no interest in learning jargon. The user should be spoon-fed every decision,
insight and recommendation. They should never have to interpret a chart, an indicator or a metric themselves.

## What that means for every change

- **Decide for the user.** Every screen should end in a clear recommendation ("Buy", "Sell", "Do nothing"),
  not raw data they have to interpret. Sensible defaults everywhere; settings are optional, never required.
- **Plain English only.** No unexplained jargon (RSI, MACD, Sharpe, drawdown, bps, etc.). If a term must
  appear, put a one-line plain-language explanation right next to it ("RSI 72 — the price has risen fast and
  may be due to cool off").
- **Explain the why in one sentence.** Each recommendation, AI analysis and backtest result gets a short
  "what this means for you" line, e.g. "This strategy would have lost $450 over 3 weeks — don't use it."
- **Translate numbers into outcomes.** Prefer dollars and simple words ("you'd have made $1,200") over
  percentages and ratios. Use green/red and simple verdicts (good / risky / avoid).
- **Guide, don't overwhelm.** Show the one next action first; hide advanced controls (indicator toggles,
  strategy params, risk limits) behind an "Advanced" section.
- **Protect the user by default.** Conservative risk limits on, auto-trading off until they opt in, and
  plain warnings before anything risky. Never imply guaranteed profit; this is paper trading.
- **AI output is written for a beginner.** Prompts in `backend/app/ai.py` should ask the model for a
  plain-language verdict and a single recommended action, not analyst-speak.

See `CONTRACT.md` for module interfaces and `README.md` for setup.
