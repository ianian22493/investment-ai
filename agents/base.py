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

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-2.5-flash"

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
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = client.models.generate_content(
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
            # Quota: 1 quick retry, then bail. Transient: full exponential back-off.
            if is_quota and attempt < 1:
                wait = 8 + random.uniform(0, 3)
                print(f"  [{agent_name}] quota signal, retry once in {wait:.1f}s")
                time.sleep(wait)
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
