#!/usr/bin/env bash
# Start the AI crypto paper-trading app. Usage: ./start.sh   (PORT=8001 ./start.sh to change port)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Creating Python 3.12 virtualenv (.venv)..."
  uv venv --python 3.12 .venv
fi

echo "Installing dependencies..."
if command -v uv >/dev/null 2>&1; then
  uv pip install --quiet --python .venv/bin/python -r requirements.txt
else
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

# Read OLLAMA_URL from .env (environment wins) for the reachability check.
OLLAMA_URL="${OLLAMA_URL:-$(grep -E '^OLLAMA_URL=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"'')}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
if ! curl -fsS --max-time 3 "${OLLAMA_URL%/}/api/tags" >/dev/null 2>&1; then
  echo "WARNING: Ollama not reachable at ${OLLAMA_URL}. AI analysis will use the rule-based fallback."
  echo "         Start it with: ollama serve"
fi

PORT="${PORT:-8000}"   # note: ComfyUI uses 8188, so keep away from that
echo "Dashboard: http://127.0.0.1:${PORT}"
exec .venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "${PORT}"
