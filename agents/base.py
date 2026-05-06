"""Base class for all investment agents."""

import anthropic
import json
import os

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-sonnet-4-6"

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
    """Call Claude API and parse JSON response."""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=system_prompt + "\n\n" + RESPONSE_SCHEMA,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = resp.content[0].text.strip()
        # strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
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
        return {
            "verdict": "ERROR",
            "confidence": 0,
            "summary": f"Agent {agent_name} error: {e}",
            "key_insights": [],
            "recommendations": [],
            "risk_flags": [str(e)],
            "agent_note": "API call failed",
        }
