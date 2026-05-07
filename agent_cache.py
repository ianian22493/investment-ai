"""
Agent Output Cache
快取慢速 agent 的輸出，避免每天重複消耗 quota。
TTL 以「天」計，到期才重新呼叫 LLM。
"""

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CACHE_PATH = os.path.join(DATA_DIR, "agent_cache.json")

# ── TTL 設定（天）────────────────────────────────────────────────────────────
# None = 永不快取（每次都重新跑）
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

    # Portfolio Desk — 每 3 天更新一次
    "us_portfolio":     3,

    # 慢速 agents — 每週更新一次（5 個交易日）
    "tw_long_term":     5,
    "fx_fund":          5,
    "asset_allocation": 7,
    "portfolio_master": 5,
    "wealth_master":    7,
}


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
    """是否有未過期的快取。"""
    ttl = TTL.get(agent_name)
    if ttl is None:
        return False   # 永不快取，每次都跑

    cache = _load_cache()
    entry = cache.get(agent_name)
    if not entry:
        return False

    cached_at = datetime.fromisoformat(entry["cached_at"])
    expires_at = cached_at + timedelta(days=ttl)
    return datetime.now(TZ) < expires_at


def get(agent_name: str) -> dict | None:
    """取得快取輸出，若不存在或已過期回傳 None。"""
    if not is_fresh(agent_name):
        return None
    cache = _load_cache()
    entry = cache.get(agent_name)
    return entry["output"] if entry else None


def set(agent_name: str, output: dict):
    """儲存 agent 輸出到快取。"""
    ttl = TTL.get(agent_name)
    if ttl is None:
        return   # 不快取這個 agent

    cache = _load_cache()
    cache[agent_name] = {
        "output":    output,
        "cached_at": datetime.now(TZ).isoformat(),
        "ttl_days":  ttl,
    }
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
