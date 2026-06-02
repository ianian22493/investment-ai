"""Base for all investment agents — uses Google Gemini API (free tier)."""

import json
import os
import random
import re
import sys
import time

from google import genai
from google.genai import types

# Make project root imports work whether base.py is loaded from cwd or sibling
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prompt_cache
from error_log import log_error as _log_error

MODEL = "gemini-2.5-flash"

# ── Multi-key rotation (stay within 20 RPD free-tier per project) ───────────
# Reads up to 4 env vars: GEMINI_API_KEY, GEMINI_API_KEY_2, _3, _4.
# Each successful call rotates to the next key (round-robin).
# When a key 429s, we mark it dead for this run and skip it on retry.
_API_KEYS: list[str] = []
for _var in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
    _v = (os.environ.get(_var) or "").strip()
    if _v:
        _API_KEYS.append(_v)

if not _API_KEYS:
    raise RuntimeError(
        "No Gemini API keys found. Set GEMINI_API_KEY (and optionally "
        "GEMINI_API_KEY_2/_3/_4 for higher daily quota via key rotation)."
    )

_CLIENTS = [genai.Client(api_key=k) for k in _API_KEYS]
_DEAD_KEYS: set[int] = set()      # indices that hit 429 this run
_next_key_idx = 0                 # cursor for round-robin

# Single-key compat handle (still imported by some places — leave alive)
client = _CLIENTS[0]


def _pick_client() -> tuple[object, int]:
    """Return (client, idx) for the next available key. Round-robin across
    keys that haven't 429'd this run."""
    global _next_key_idx
    n = len(_CLIENTS)
    alive = [i for i in range(n) if i not in _DEAD_KEYS]
    if not alive:
        # All dead — return first key anyway; caller will hit quota fallback path
        return _CLIENTS[0], 0
    # Find next alive key starting from _next_key_idx
    for _ in range(n):
        if _next_key_idx not in _DEAD_KEYS:
            idx = _next_key_idx
            _next_key_idx = (_next_key_idx + 1) % n
            return _CLIENTS[idx], idx
        _next_key_idx = (_next_key_idx + 1) % n
    return _CLIENTS[alive[0]], alive[0]


def keys_status() -> str:
    """For logging."""
    parts = []
    for i in range(len(_CLIENTS)):
        parts.append(f"key{i+1}:{'✗' if i in _DEAD_KEYS else '✓'}")
    return f"[{len(_API_KEYS)} keys] {' '.join(parts)}"

# Transient errors worth retrying many times — Gemini overload, network blips.
# These typically resolve within seconds.
RETRYABLE_TOKENS = (
    "503", "500", "502", "504",
    "UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED",
    "ConnectionError", "Timeout", "TimeoutError",
)

# Quota-exhaustion tokens — daily limit hit. Retries within the same run will
# almost always also hit 429 (free-tier resets at midnight Pacific). So we
# only attempt ONCE more (in case it's a rate-limit blip), then bail to
# stale-cache fallback. Burning all 5 retries here was eating our quota.
QUOTA_TOKENS = ("429", "RESOURCE_EXHAUSTED")

RESPONSE_SCHEMA = """
Your response MUST be valid JSON only. No markdown, no explanation outside JSON.
Structure:
{
  "verdict": "string (e.g. 進攻/防守/觀望/持倉不動/減碼/加碼)",
  "confidence": 0.0-1.0,
  "summary": "2-3 sentence executive summary in Traditional Chinese",
  "key_insights": ["insight 1", "insight 2", "insight 3"],
  "recommendations": [
    {
      "action": "BUY/SELL/HOLD/ADD/CUT/WATCH",
      "target": "stock code or asset name",
      "detail": "specific instruction",
      "urgency": "HIGH/MED/LOW"
    }
  ],
  "risk_flags": ["risk 1", "risk 2"],
  "agent_note": "any bias disclosure or prohibited-behavior confirmation"
}
"""


def call_claude(system_prompt: str, user_content: str, agent_name: str) -> dict:
    """Call Gemini API and parse JSON response. Retries on transient errors (429/503/5xx).
    Prompt-hash cache layer in front — skips API call if identical prompt was seen
    within the last 24h. Cache complementary to agent_cache.py (agent-level TTL)."""
    full_system = system_prompt + "\n\n" + RESPONSE_SCHEMA
    cached = prompt_cache.get(MODEL, full_system, user_content)
    if cached is not None:
        print(f"  [prompt-cache hit] {agent_name}")
        return cached

    MAX_ATTEMPTS = 5
    last_key_idx = None
    for attempt in range(MAX_ATTEMPTS):
        active_client, last_key_idx = _pick_client()
        try:
            resp = active_client.models.generate_content(
                model=MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=full_system,
                ),
            )
            raw = resp.text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            raw = re.sub(r'\[\d+\]', '', raw)
            parsed = json.loads(raw)
            prompt_cache.set(MODEL, full_system, user_content, agent_name, parsed)
            return parsed
        except json.JSONDecodeError as e:
            if attempt < MAX_ATTEMPTS - 1:
                print(f"  [{agent_name}] JSON parse error, retry {attempt+1}/{MAX_ATTEMPTS}...")
                time.sleep(15)
                continue
            _log_error(f"agent:{agent_name}", e)
            return {
                "verdict": "ERROR",
                "confidence": 0,
                "summary": f"Agent {agent_name} JSON parse error: {e}",
                "key_insights": [],
                "recommendations": [],
                "risk_flags": ["Agent output parsing failed"],
                "agent_note": "raw output could not be parsed",
            }
        except Exception as e:
            err = str(e)
            is_quota = any(tok in err for tok in QUOTA_TOKENS)
            is_transient = any(tok in err for tok in RETRYABLE_TOKENS)
            # ── Multi-key handling: when one key 429s, mark it dead and
            # immediately try the next key (no sleep needed).
            if is_quota:
                _DEAD_KEYS.add(last_key_idx)
                alive = [i for i in range(len(_CLIENTS)) if i not in _DEAD_KEYS]
                if alive and attempt < MAX_ATTEMPTS - 1:
                    print(f"  [{agent_name}] key{last_key_idx+1} 429 — swap to key{alive[0]+1} ({keys_status()})")
                    continue
                # All keys dead: 1 quick retry in case it's a rate-limit blip
                if attempt < 1:
                    wait = 8 + random.uniform(0, 3)
                    print(f"  [{agent_name}] all keys exhausted, retry once in {wait:.1f}s")
                    time.sleep(wait)
                    _DEAD_KEYS.clear()  # let them try again
                    continue
            if is_transient and not is_quota and attempt < MAX_ATTEMPTS - 1:
                wait = min(60, 10 * (2 ** attempt)) + random.uniform(0, 3)
                print(f"  [{agent_name}] {err[:80]}, backoff {wait:.1f}s (attempt {attempt+1}/{MAX_ATTEMPTS})")
                time.sleep(wait)
                continue
            _log_error(f"agent:{agent_name}", e)
            # ── Graceful degradation: try to reuse a recent cached response ──
            # If quota is exhausted, the most recent response for this agent
            # (within 48h) is still better than ERROR + raw 429 stacktrace.
            stale = prompt_cache.get_latest_for_agent(agent_name, max_age_hours=48)
            if stale is not None:
                resp = dict(stale["response"])
                resp["_degraded"] = True
                resp["_degraded_reason"] = (
                    "Gemini 配額已滿，顯示前次分析" if is_quota
                    else "API 暫無回應，顯示前次分析"
                )
                resp["_degraded_from"] = stale["stored_at"]
                print(f"  [{agent_name}] ⚠ degraded → reusing cached response from {stale['stored_at']}")
                return resp
            # No cache to fall back to — return a friendly placeholder
            friendly = (
                "Gemini 今日配額已滿，目前無新分析（明日 00:00 PT 重置）。"
                if is_quota
                else f"AI 服務暫時無法回應：{err[:120]}"
            )
            return {
                "verdict": "—",
                "confidence": 0,
                "summary": friendly,
                "key_insights": [],
                "recommendations": [],
                "risk_flags": ["AI 分析不可用"],
                "agent_note": "API call failed; no fallback cache available",
                "_degraded": True,
                "_degraded_reason": friendly,
            }
