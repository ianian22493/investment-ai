"""TW stock code → canonical name validator.

Downloads upper-listed (TWSE) + OTC (TPEx) stock lists, caches to
data/tw_stock_names.json, re-downloads when stale (>7 days).

Used by outcome_tracker.save_today_pick to override AI hallucinations
(e.g. AI labels 6150 as '勤誠' when the canonical name is '撼訊').
"""

import json
import os
import urllib3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TZ = ZoneInfo("Asia/Taipei")
CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "tw_stock_names.json")
CACHE_TTL_DAYS = 7

def _download_list() -> dict:
    """Fetch full TW stock list. Returns {code: name}.
    Primary: FinMind (4000+ entries incl. 上市/上櫃/興櫃, including stocks
    missing from TWSE/TPEx OpenAPI feeds like 6150 撼訊).
    Fallback: TWSE OpenAPI for listed only.
    """
    out = {}

    # Primary — FinMind TaiwanStockInfo (free tier OK for this dataset)
    try:
        r = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={"dataset": "TaiwanStockInfo"},
            timeout=30,
        )
        d = r.json()
        if d.get("status") == 200:
            for row in d.get("data", []):
                code = (row.get("stock_id") or "").strip()
                name = (row.get("stock_name") or "").strip()
                if code and name:
                    out[code] = name
            print(f"[stock_lookup] FinMind: {len(out)} entries")
    except Exception as e:
        print(f"[stock_lookup] FinMind fetch error: {e}")

    # Fallback — TWSE OpenAPI (listed only)
    if len(out) < 500:
        try:
            r = requests.get(
                "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                timeout=20, verify=False,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            for row in r.json():
                code = (row.get("公司代號") or row.get("Code") or "").strip()
                name = (row.get("公司簡稱") or row.get("Name") or "").strip()
                if code and name and code not in out:
                    out[code] = name
            print(f"[stock_lookup] TWSE fallback: total {len(out)} entries")
        except Exception as e:
            print(f"[stock_lookup] TWSE fetch error: {e}")

    return out


def _load_cache():
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        cached_at = datetime.fromisoformat(d.get("cached_at", ""))
        if datetime.now(TZ) - cached_at > timedelta(days=CACHE_TTL_DAYS):
            return None  # stale
        return d.get("names", {})
    except Exception:
        return None


def _save_cache(names: dict):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"cached_at": datetime.now(TZ).isoformat(timespec="seconds"), "names": names},
            f, ensure_ascii=False, indent=2,
        )


_NAMES_CACHE = None


def _ensure_loaded() -> dict:
    global _NAMES_CACHE
    if _NAMES_CACHE is not None:
        return _NAMES_CACHE
    names = _load_cache()
    if names is None:
        names = _download_list()
        if names:
            _save_cache(names)
    _NAMES_CACHE = names or {}
    return _NAMES_CACHE


def get_name(code: str):
    """Return canonical name for `code`, or None if unknown."""
    return _ensure_loaded().get(str(code))


def validate_name(code: str, ai_name: str):
    """Validate AI-supplied name. Returns (canonical_name, was_corrected).
    If lookup fails (e.g. download failed), returns ai_name unchanged.
    """
    canonical = get_name(code)
    if not canonical:
        return ai_name, False
    if canonical == ai_name:
        return canonical, False
    return canonical, True
