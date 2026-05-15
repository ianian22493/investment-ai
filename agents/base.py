"""Base for all investment agents — uses Google Gemini API (free tier)."""

import json
import os
import random
import re
import time

from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-2.5-flash"

# Errors worth retrying — covers quota (429), Gemini overload (503/UNAVAILABLE),
# upstream transient failures (500/502/504), and network blips.
RETRYABLE_TOKENS = (
    "429", "503", "500", "502", "504",
    "UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "DEADLINE_EXCEEDED",
    "ConnectionError", "Timeout", "TimeoutError",
)

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
    """Call Gemini API and parse JSON response. Retries on transient errors (429/503/5xx)."""
    MAX_ATTEMPTS = 5
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt + "\n\n" + RESPONSE_SCHEMA,
                ),
            )
            raw = resp.text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            raw = re.sub(r'\[\d+\]', '', raw)
            return json.loads(raw)
        except json.JSONDecodeError as e:
            if attempt < MAX_ATTEMPTS - 1:
                print(f"  [{agent_name}] JSON parse error, retry {attempt+1}/{MAX_ATTEMPTS}...")
                time.sleep(15)
                continue
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
            retryable = any(tok in err for tok in RETRYABLE_TOKENS)
            if retryable and attempt < MAX_ATTEMPTS - 1:
                wait = min(60, 10 * (2 ** attempt)) + random.uniform(0, 3)
                print(f"  [{agent_name}] {err[:80]}, backoff {wait:.1f}s (attempt {attempt+1}/{MAX_ATTEMPTS})")
                time.sleep(wait)
                continue
            return {
                "verdict": "ERROR",
                "confidence": 0,
                "summary": f"Agent {agent_name} error: {e}",
                "key_insights": [],
                "recommendations": [],
                "risk_flags": [err],
                "agent_note": "API call failed",
            }
