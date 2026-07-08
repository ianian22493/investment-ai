"""
Holdings correlation analyzer.

給定今日 pick 的股票代號 + 使用者持股，回傳「相關持倉提示」
用於警告過度集中在同產業（或同大類）。

純 Python 邏輯，不呼叫 LLM。輸出 dict 會被塞進
outputs["tw_daily_pick"]["correlation"] 給前端顯示。

Matching rules:
- exact:  pick sector == holding sector       (權重最高)
- family: 大類相同（"-" 前的字串）             (權重中)
- none:   完全不同大類                        (無警告)

Warning levels:
- high:    exact match, 或同 family 曝險 ≥ 15%
- medium:  family match, 曝險 5% – 15%
- low:     family match, 曝險 < 5%
- none:    沒有 match，或 pick 未登記 sector
"""
from __future__ import annotations
import json
import os
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SECTOR_MAP_PATH = os.path.join(ROOT, "data", "sector_map.json")


def _load_sector_map() -> dict[str, str]:
    """讀 data/sector_map.json 的 mapping 區段。"""
    try:
        with open(SECTOR_MAP_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("mapping", {})
    except Exception:
        return {}


def _family_of(sector: str) -> str:
    """半導體-代工 → 半導體 · ETF-broad → ETF · 水泥 → 水泥"""
    if not sector:
        return ""
    return sector.split("-", 1)[0]


def compute(pick_code: str, portfolio: dict[str, Any]) -> dict[str, Any]:
    """回傳相關持倉分析。

    Args:
        pick_code: 今日 pick 的股票代號，例如 "6719"。若空或 "—"，回傳 empty result。
        portfolio: portfolio.json 內容（含 tw_stocks / tw_summary）

    Returns:
        dict:
        {
            "pick_code":  str,
            "pick_sector":  str | None,     # 未登記時為 None
            "pick_sector_family":  str | None,
            "matches": [{ code, name, sector, match_type, value_twd, exposure_pct }],
            "family_exposure_pct":  float,   # 加碼後同大類佔比
            "exact_exposure_pct":   float,   # 加碼後同精細類佔比
            "warning_level":  "high" | "medium" | "low" | "none",
            "summary":  str,                 # 一句話中文摘要
        }
    """
    result = {
        "pick_code": pick_code,
        "pick_sector": None,
        "pick_sector_family": None,
        "matches": [],
        "family_exposure_pct": 0.0,
        "exact_exposure_pct": 0.0,
        "warning_level": "none",
        "summary": "",
    }

    if not pick_code or pick_code in ("—", "NONE", "", None):
        return result

    sector_map = _load_sector_map()
    pick_sector = sector_map.get(str(pick_code))
    if not pick_sector:
        result["summary"] = f"{pick_code} 產業未登記 · 無法計算相關性"
        return result

    result["pick_sector"] = pick_sector
    pick_family = _family_of(pick_sector)
    result["pick_sector_family"] = pick_family

    holdings = portfolio.get("tw_stocks", []) or []
    total_value = sum(h.get("value", 0) for h in holdings) or 1  # 避免除零

    matches = []
    exact_value = 0.0
    family_value = 0.0
    for h in holdings:
        code = str(h.get("code", ""))
        # 用 portfolio.json 的 sector 欄位優先；沒有就查 sector_map
        h_sector = h.get("sector") or sector_map.get(code) or ""
        if not h_sector:
            continue

        match_type = None
        if h_sector == pick_sector:
            match_type = "exact"
            exact_value += h.get("value", 0)
            family_value += h.get("value", 0)
        elif _family_of(h_sector) == pick_family:
            match_type = "family"
            family_value += h.get("value", 0)

        if match_type:
            matches.append({
                "code": code,
                "name": h.get("name", ""),
                "sector": h_sector,
                "match_type": match_type,
                "value_twd": h.get("value", 0),
                "exposure_pct": round(h.get("value", 0) / total_value * 100, 1),
            })

    # 排序：exact 優先，然後 exposure 由大到小
    matches.sort(key=lambda m: (0 if m["match_type"] == "exact" else 1,
                                 -m["exposure_pct"]))
    result["matches"] = matches
    result["family_exposure_pct"] = round(family_value / total_value * 100, 1)
    result["exact_exposure_pct"] = round(exact_value / total_value * 100, 1)

    # Warning level
    if exact_value > 0 or result["family_exposure_pct"] >= 15:
        result["warning_level"] = "high"
    elif result["family_exposure_pct"] >= 5:
        result["warning_level"] = "medium"
    elif matches:
        result["warning_level"] = "low"
    else:
        result["warning_level"] = "none"

    # Summary
    if not matches:
        result["summary"] = f"{pick_code}({pick_sector}) 跟你目前的持股沒有明顯產業重疊"
    else:
        exact_names = [m["name"] for m in matches if m["match_type"] == "exact"]
        family_names = [m["name"] for m in matches if m["match_type"] == "family"]
        parts = []
        if exact_names:
            parts.append(f"完全重疊：{'、'.join(exact_names)}")
        if family_names:
            parts.append(f"同大類（{pick_family}）：{'、'.join(family_names)}")
        exposure = result["family_exposure_pct"]
        result["summary"] = (
            f"{pick_code}({pick_sector}) · " + " · ".join(parts) +
            f" · 加碼前該大類已佔 {exposure}%"
        )

    return result


# CLI smoke test
if __name__ == "__main__":
    import sys
    portfolio = json.load(open(os.path.join(ROOT, "data", "portfolio.json"), encoding="utf-8"))
    code = sys.argv[1] if len(sys.argv) > 1 else "6719"
    r = compute(code, portfolio)
    print(json.dumps(r, ensure_ascii=False, indent=2))
