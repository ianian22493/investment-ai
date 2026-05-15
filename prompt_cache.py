"""
Prompt-level LLM cache — keyed on sha256(model + system + user).

Complementary to agent_cache.py:
- agent_cache.py: TTL-by-day, per-agent NAME (e.g. fx_fund cached 5 days)
- prompt_cache.py: TTL-by-hour, per-prompt-CONTENT (catches duplicate calls
  within the day — manual reruns, post-failure retries, dev iteration)

Bypass via env: LLM_CACHE_DISABLED=1
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CACHE_PATH = os.path.join(DATA_DIR, "llm_cache.json")
DEFAULT_TTL_HOURS = 24

DISABLED = os.environ.get("LLM_CACHE_DISABLED") == "1"


def _hash(model: str, system: str, user: str) -> str:
    blob = f"{model}\n---\n{system}\n---\n{user}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _load() -> dict:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(cache: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    # Evict entries older than 2× default TTL
    cutoff = datetime.now(TZ) - timedelta(hours=DEFAULT_TTL_HOURS * 2)
    pruned = {}
    for k, v in cache.items():
        try:
            if datetime.fromisoformat(v["stored_at"]) > cutoff:
                pruned[k] = v
        except Exception:
            pass
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=2)


def get(model: str, system: str, user: str, ttl_hours: int = DEFAULT_TTL_HOURS):
    """Return cached response or None."""
    if DISABLED:
        return None
    key = _hash(model, system, user)
    cache = _load()
    entry = cache.get(key)
    if not entry:
        return None
    try:
        stored_at = datetime.fromisoformat(entry["stored_at"])
    except Exception:
        return None
    if datetime.now(TZ) - stored_at > timedelta(hours=ttl_hours):
        return None
    return entry["response"]


def set(model: str, system: str, user: str, agent: str, response: dict):
    """Store response under hash(model+system+user)."""
    if DISABLED:
        return
    key = _hash(model, system, user)
    cache = _load()
    cache[key] = {
        "response":  response,
        "agent":     agent,
        "model":     model,
        "stored_at": datetime.now(TZ).isoformat(timespec="seconds"),
    }
    _save(cache)
