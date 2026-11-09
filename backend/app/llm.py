"""LLM router: local Ollama by default, optional OpenRouter ("cloud") for chosen tasks, with a monthly spend cap.

Tasks: "code" (pair-programmer), "research" (filings and news), "agent" (agent checks and capstone write-up),
"coach" (coach chat). Every call says which model answered and what it cost. The OpenRouter key is read from the
OPENROUTER_API_KEY env var or a key file and is never sent to the browser.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.ai import _extract_json

logger = logging.getLogger("app.llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_KEY_FILE = Path.home() / ".config" / "openrouter" / "api_key"
TASKS = ("code", "research", "agent", "coach")
# Tested on real SEC filings (Nov 2026): Gemini Flash Lite was the most reliable and cheapest (~$0.0005 a filing);
# Haiku quoted most accurately but occasionally broke its JSON; DeepSeek's route often returned empty replies.
CLOUD_MODELS = [
    {"id": "google/gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite (fast, reliable, cheapest)"},
    {"id": "anthropic/claude-haiku-5.5", "label": "Claude Haiku 5.5 (most careful, a little pricier)"},
    {"id": "qwen/qwen3-coder-next", "label": "Qwen3 Coder Next (code)"},
    {"id": "deepseek/deepseek-v4-flash", "label": "DeepSeek V4 Flash (cheap, sometimes slow)"},
]
DEFAULTS = {"cloud_enabled": False, "cloud_model": CLOUD_MODELS[0]["id"], "cloud_tasks": ["code", "research", "agent"],
            "monthly_cap_usd": 2.0}
KV_SETTINGS = "ai_settings"
KV_SPEND = "ai_spend"


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class LLMError(RuntimeError):
    """No model could answer."""


# Keep num_ctx equal to the rest of the app (ai.py uses 8192): a different value makes Ollama reload the model.
class LLMRouter:
    def __init__(self, db, ollama_url: str, ollama_model: str, ollama_timeout: float):
        self.db = db
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.ollama_timeout = ollama_timeout
        self.local_available = False  # refreshed by the engine's status loop

    # ---------------------------------------------------------------- settings + spend
    def settings(self) -> dict:
        raw = self.db.get_kv(KV_SETTINGS)
        out = dict(DEFAULTS)
        if isinstance(raw, dict):
            out.update({k: v for k, v in raw.items() if k in DEFAULTS})
        return out

    def update(self, partial: dict) -> dict:
        s = self.settings()
        if "cloud_enabled" in partial and partial["cloud_enabled"] is not None:
            if partial["cloud_enabled"] and not self.key():
                raise ValueError("No OpenRouter key found. Set OPENROUTER_API_KEY or create ~/.config/openrouter/api_key.")
            s["cloud_enabled"] = bool(partial["cloud_enabled"])
        if partial.get("cloud_model"):
            model = str(partial["cloud_model"]).strip()
            if not model or len(model) > 120 or " " in model:
                raise ValueError("That doesn't look like an OpenRouter model id, e.g. deepseek/deepseek-v4-flash.")
            s["cloud_model"] = model
        if partial.get("cloud_tasks") is not None:
            tasks = [t for t in partial["cloud_tasks"] if t in TASKS]
            s["cloud_tasks"] = tasks
        if partial.get("monthly_cap_usd") is not None:
            cap = float(partial["monthly_cap_usd"])
            if not 0 <= cap <= 100:
                raise ValueError("The monthly cap must be between $0 and $100.")
            s["monthly_cap_usd"] = cap
        self.db.set_kv(KV_SETTINGS, s)
        return self.public()

    def spend(self) -> dict:
        raw = self.db.get_kv(KV_SPEND)
        month = _month()
        if not isinstance(raw, dict) or raw.get("month") != month:
            return {"month": month, "usd": 0.0, "calls": 0}
        return raw

    def _add_spend(self, usd: float) -> None:
        s = self.spend()
        s["usd"] = float(s["usd"]) + float(usd or 0)
        s["calls"] = int(s["calls"]) + 1
        self.db.set_kv(KV_SPEND, s)

    @staticmethod
    def key() -> str | None:
        env = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if env:
            return env
        path = Path(os.environ.get("OPENROUTER_KEY_FILE", str(DEFAULT_KEY_FILE))).expanduser()
        try:
            text = path.read_text().strip()
        except OSError:
            return None
        return text or None

    def public(self) -> dict:
        s = self.settings()
        sp = self.spend()
        return {**s, "key_present": bool(self.key()), "spend_usd": round(sp["usd"], 4), "spend_calls": sp["calls"],
                "month": sp["month"], "cap_reached": sp["usd"] >= s["monthly_cap_usd"],
                "local_model": self.ollama_model, "local_available": self.local_available,
                "models": CLOUD_MODELS, "tasks": list(TASKS)}

    def uses_cloud(self, task: str) -> bool:
        s = self.settings()
        return bool(s["cloud_enabled"] and task in s["cloud_tasks"] and self.key()
                    and self.spend()["usd"] < s["monthly_cap_usd"])

    def route(self, task: str) -> str:
        """Who would answer a `task` right now: 'cloud:<model>', 'local:<model>' or 'none'."""
        if self.uses_cloud(task):
            return f"cloud:{self.settings()['cloud_model']}"
        return f"local:{self.ollama_model}" if self.local_available else "none"

    # ---------------------------------------------------------------- calls
    async def chat(self, task: str, messages: list[dict], *, json_mode: bool = True, temperature: float = 0.2,
                   max_tokens: int = 1500, num_ctx: int = 8192) -> dict:
        """Ask a model. Returns {"text", "json" (dict or None), "source", "cost_usd", "latency_ms"}.
        Raises LLMError if neither the cloud (when enabled for the task) nor the local model answers."""
        start = time.perf_counter()
        errors = []
        if self.uses_cloud(task):
            spent = 0.0
            for attempt in range(2):  # one retry: providers occasionally return empty or non-JSON replies
                if attempt and self.spend()["usd"] + spent >= self.settings()["monthly_cap_usd"]:
                    break  # don't retry past the cap
                try:
                    text, cost = await self._openrouter(messages, temperature, max_tokens)
                    spent += cost
                    out = self._result(text, json_mode, f"cloud:{self.settings()['cloud_model']}", spent, start)
                    self._add_spend(spent)
                    return out
                except Exception as exc:  # noqa: BLE001 - retry, then fall back to the local model
                    spent += getattr(exc, "cost", 0.0)
                    logger.warning("OpenRouter %s call failed (attempt %d): %r", task, attempt + 1, exc)
                    errors.append(f"cloud: {exc!r}")
            if spent:
                self._add_spend(spent)
        try:
            text = await self._ollama(messages, json_mode, temperature, num_ctx)
            return self._result(text, json_mode, f"local:{self.ollama_model}", 0.0, start)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"local: {exc!r}")
        raise LLMError("No AI model answered (" + "; ".join(errors) + "). Start Ollama or turn on the cloud model in Settings.")

    @staticmethod
    def _result(text: str, json_mode: bool, source: str, cost: float, start: float) -> dict:
        parsed = None
        if json_mode:
            parsed = _extract_json(text)
        return {"text": text, "json": parsed, "source": source, "cost_usd": round(cost, 6),
                "latency_ms": int((time.perf_counter() - start) * 1000)}

    async def _openrouter(self, messages, temperature, max_tokens) -> tuple[str, float]:
        # No response_format: some providers mishandle JSON mode and return nothing. The prompts ask for JSON and
        # replies are parsed leniently, with a retry.
        body = {"model": self.settings()["cloud_model"], "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens, "usage": {"include": True}}
        headers = {"Authorization": f"Bearer {self.key()}", "X-Title": "Quant Path (local learning app)"}
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(OPENROUTER_URL, json=body, headers=headers)
            data = resp.json()
        if resp.status_code >= 400 or data.get("error"):
            raise LLMError((data.get("error") or {}).get("message") or f"HTTP {resp.status_code}")
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        cost = float((data.get("usage") or {}).get("cost") or 0.0)
        if not isinstance(text, str) or not text.strip():
            err = LLMError("empty reply")
            err.cost = cost
            raise err
        return text, cost

    async def _ollama(self, messages, json_mode, temperature, num_ctx) -> str:
        body = {"model": self.ollama_model, "messages": messages, "stream": False, "think": False,
                "options": {"temperature": temperature, "num_ctx": num_ctx}}
        if json_mode:
            body["format"] = "json"
        async with httpx.AsyncClient(timeout=self.ollama_timeout) as client:
            resp = await client.post(f"{self.ollama_url}/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        text = (data.get("message") or {}).get("content")
        if not isinstance(text, str) or not text.strip():
            raise LLMError("empty reply from Ollama")
        return text


def dumps(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)
