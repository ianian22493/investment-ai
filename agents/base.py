"""Base class for all investment agents."""

import json
import os
import re
import time

from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-2.0-flash"

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
    """Call Gemini API and parse JSON response. Retries on 429 quota errors."""
    for attempt in range(4):
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
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = re.sub(r'\[\d+\]', '', raw)
            return json.loads(raw)
        except json.JSONDecodeError as e:
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
            if "429" in err and attempt < 3:
                wait = 15 * (attempt + 1)
                print(f"  [{agent_name}] 429 quota hit, waiting {wait}s (attempt {attempt+1}/4)...")
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
