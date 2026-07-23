"""
Agent Output Cache (agent_cache.json) — TTL by AGENT NAME in DAYS.

This is one of TWO cache layers in the project. They cover different scenarios:

  agent_cache.py  ─ THIS FILE
    Key:        agent name (e.g. "tw_long_term")
    TTL:        days (3-7, per agent)
    Purpose:    Long-running agents whose view doesn't change daily
                (long-term portfolio, macro views, asset allocation).
                Saves LLM calls when re-running multiple times per week.
    Storage:    data/agent_cache.json

  prompt_cache.py
    Key:        sha256(model + system_prompt + user_content)
    TTL:        hours (default 24)
    Purpose:    1) Catch identical re-calls within 24h (rare in production
                   but useful for dev iteration + retry-after-failure).
                2) Stale fallback when Gemini quota is exhausted —
                   get_latest_for_agent(name, 48h) returns whatever
                   recent response we have, marked _degraded=true.
    Storage:    data/llm_cache.json

In a normal run order:
  agent.run() → agent_cache.get_or_run(name, fn)
    ↓ (cache miss or no TTL set)
    fn() → base.call_llm(system, user, name)
       ↓
       prompt_cache.get(hash)  ← layer 2 cache check
       ↓ (miss)
       Gemini API call (with key rotation)
       ↓ (success)
       prompt_cache.set(hash, response) ← store for layer 2
       ↓
    return parsed response
  ↓
  agent_cache.set(name, response) ← store for layer 1 (if TTL > 0)
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CACHE_PATH = os.path.join(DATA_DIR, "agent_cache.json")
PORTFOLIO_PATH = os.path.join(DATA_DIR, "portfolio.json")

# ── TTL 設定（天）────────────────────────────────────────────────────────────
# None = 永不快取（每次都重新跑）
# TTLs were shortened 2026-06-12 after we found that 7-day caches on the
# wealth cluster survived a portfolio.json edit (cash 300K → 1.06M) and
# kept the system locked at "局部防守" for 7 days even though the
# real cash position had changed. New TTLs + portfolio-hash invalidation.
TTL: dict[str, int | None] = {
    # Trading Desk — 每天都要跑
    "market_overview":  None,
    "news_sentiment":   None,
    "tw_short_term":    None,
    "tw_daily_pick":    None,
    "trading_master":   None,
    "devils_advocate":  None,
    "reflection":       None,
    "master_agent":     None,
    "pick_explainer":   None,    # /picks/YYYY-MM-DD.html — daily fresh

    # Portfolio Desk
    "us_portfolio":     3,

    # 慢速 agents — TTL 縮短，並對 wealth_cluster 加 portfolio hash 偵測
    "tw_long_term":     5,
    "fx_fund":          3,    # was 5
    "asset_allocation": 3,    # was 7
    "portfolio_master": 3,    # was 5
    "wealth_master":    3,    # was 7 — most impacted by stale cash
}

# Wealth cluster agents read directly from portfolio.json (cash / shares /
# real_estate payment schedule). When portfolio.json changes, their cache
# must be invalidated regardless of TTL. We track a sha256 hash of the
# file at set() time and compare on get().
WEALTH_CLUSTER = {"wealth_master", "asset_allocation", "portfolio_master", "fx_fund"}


_PF_HASH_CACHE: str | None = None

def _portfolio_hash() -> str | None:
    """Short hash of portfolio.json content. Memoized per-process to avoid
    re-reading the file on every is_fresh() call (12+ agents × per cron).
    Returns None if file missing or unreadable.

    Assumes portfolio.json doesn't change mid-run — safe for GH Actions
    cron context. Reset _PF_HASH_CACHE manually if you need to force a
    re-read in a long-running process (we don't have one).
    """
    global _PF_HASH_CACHE
    if _PF_HASH_CACHE is not None:
        return _PF_HASH_CACHE
    try:
        with open(PORTFOLIO_PATH, "rb") as f:
            _PF_HASH_CACHE = hashlib.sha256(f.read()).hexdigest()[:12]
        return _PF_HASH_CACHE
    except Exception:
        return None


def _load_cache() -> dict:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def is_fresh(agent_name: str) -> bool:
    """是否有未過期的快取。對 WEALTH_CLUSTER 額外檢查 portfolio.json 是否改過。"""
    ttl = TTL.get(agent_name)
    if ttl is None:
        return False   # 永不快取，每次都跑

    cache = _load_cache()
    entry = cache.get(agent_name)
    if not entry:
        return False

    cached_at = datetime.fromisoformat(entry["cached_at"])
    expires_at = cached_at + timedelta(days=ttl)
    if datetime.now(TZ) >= expires_at:
        return False

    # Portfolio-hash invalidation: wealth-cluster agents become stale the
    # moment portfolio.json changes, regardless of TTL.
    if agent_name in WEALTH_CLUSTER:
        cur_hash = _portfolio_hash()
        cached_hash = entry.get("portfolio_hash")
        # Force-refresh legacy entries that pre-date the portfolio_hash
        # feature (cached_hash absent). Without this, the invalidation
        # never fires for entries cached before 2026-06-12.
        if cur_hash and not cached_hash:
            return False
        if cur_hash and cached_hash and cur_hash != cached_hash:
            return False
    return True


def get(agent_name: str) -> dict | None:
    """取得快取輸出，若不存在或已過期回傳 None。"""
    if not is_fresh(agent_name):
        return None
    cache = _load_cache()
    entry = cache.get(agent_name)
    return entry["output"] if entry else None


def set(agent_name: str, output: dict):
    """儲存 agent 輸出到快取。對 WEALTH_CLUSTER 一起記下當時 portfolio hash。"""
    ttl = TTL.get(agent_name)
    if ttl is None:
        return   # 不快取這個 agent

    cache = _load_cache()
    entry = {
        "output":    output,
        "cached_at": datetime.now(TZ).isoformat(),
        "ttl_days":  ttl,
    }
    if agent_name in WEALTH_CLUSTER:
        entry["portfolio_hash"] = _portfolio_hash()
    cache[agent_name] = entry
    _save_cache(cache)


def get_or_run(agent_name: str, fn, sleep_fn=None) -> tuple[dict, bool]:
    """
    若快取有效，直接回傳快取（不跑 LLM，不 sleep）。
    若快取過期，執行 fn()，儲存結果，執行 sleep_fn（若提供）。
    回傳 (output, was_cached)。
    """
    cached = get(agent_name)
    if cached is not None:
        ttl = TTL.get(agent_name, "?")
        cached_at = _load_cache().get(agent_name, {}).get("cached_at", "?")[:10]
        print(f"    💾 [cache hit] {agent_name} (TTL {ttl}d, 快取自 {cached_at})")
        return cached, True

    output = fn()
    set(agent_name, output)
    if sleep_fn:
        sleep_fn()
    return output, False


def status_summary() -> str:
    """印出各 agent 快取狀態，方便 debug。"""
    cache = _load_cache()
    now = datetime.now(TZ)
    lines = ["[agent_cache] 快取狀態："]
    for name, ttl in TTL.items():
        if ttl is None:
            lines.append(f"  {name}: 不快取（每次執行）")
            continue
        entry = cache.get(name)
        if not entry:
            lines.append(f"  {name}: 無快取")
            continue
        cached_at = datetime.fromisoformat(entry["cached_at"])
        expires_at = cached_at + timedelta(days=ttl)
        remaining = (expires_at - now).total_seconds() / 3600
        status = f"有效（剩 {remaining:.1f}h）" if remaining > 0 else "已過期"
        lines.append(f"  {name}: {status} (TTL {ttl}d)")
    return "\n".join(lines)
