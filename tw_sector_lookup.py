"""台股代號 → 產業別 自動查詢/快取.

從 FinMind TaiwanStockInfo 拉整個上市/上櫃股票清單，把 FinMind 的
57 種 industry_category 對應到我們的 sector taxonomy，快取到
data/sector_map_auto.json.

執行方式：
  python tw_sector_lookup.py [--refresh]

沒 --refresh 且快取存在時直接讀 cache；有 --refresh 或 cache >7 天強制重抓.

holdings_correlation.py 會同時讀 sector_map.json (人工細分) + sector_map_auto.json
(自動粗分)，人工優先.
"""
from __future__ import annotations
import json
import os
import sys
import urllib3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TZ = ZoneInfo("Asia/Taipei")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
CACHE_PATH = os.path.join(DATA_DIR, "sector_map_auto.json")
CACHE_TTL_DAYS = 7

# FinMind 同一 stock 會列多個 industry_category（例如 2330 台積電同時列
# 「半導體業」+「電子工業」）。用 priority 選最具體那個。
# 高分 = 較具體、優先保留
_CAT_PRIORITY = {
    "半導體業":              100,
    "光電業":                 92,
    "通信網路業":              88,
    "電腦及週邊設備業":        84,
    "電子零組件業":            70,
    "電機機械":                60,
    "電子通路業":              55,
    "資訊服務業":              54,
    "數位雲端":                53,
    "數位雲端類":              53,
    "電子商務業":              52,
    "電器電纜":                50,
    "其他電子業":              25,
    "其他電子類":              25,
    "電子工業":                 5,   # 最低——這是所有電子的傘型類
    "其他":                     1,
}

# FinMind category → 我們的 sector taxonomy
# 命名慣例：「大類」或「大類-子類」，方便同大類 prefix match
_FINMIND_TO_SECTOR = {
    # 半導體 → 統一貼上「半導體」，日後手動細分
    "半導體業":              "半導體",
    # 電子相關
    "電子工業":              "電子零組件",
    "電子零組件業":          "電子零組件",
    "光電業":                "光電",
    "電腦及週邊設備業":      "電腦",
    "電機機械":              "電機機械",
    "通信網路業":            "通訊網路",
    "其他電子業":            "電子-其他",
    "其他電子類":            "電子-其他",
    "電子通路業":            "電子通路",
    "電子商務業":            "電子商務",
    "資訊服務業":            "軟體服務",
    "數位雲端":              "軟體服務-雲端",
    "數位雲端類":            "軟體服務-雲端",
    "電器電纜":              "電器電纜",
    # 生技化學
    "生技醫療業":            "生技",
    "化學生技醫療":          "生技-化學",
    "化學工業":              "塑化",
    "塑膠工業":              "塑化",
    "橡膠工業":              "橡膠",
    # 傳產
    "水泥工業":              "水泥",
    "鋼鐵工業":              "鋼鐵",
    "紡織纖維":              "紡織",
    "食品工業":              "食品",
    "汽車工業":              "汽車",
    "建材營造":              "營建",
    "造紙工業":              "造紙",
    "玻璃陶瓷":              "玻璃陶瓷",
    # 服務業
    "金融保險":              "金融",
    "金融業":                "金融",
    "航運業":                "航運",
    "觀光餐旅":              "觀光餐旅",
    "觀光事業":              "觀光餐旅",
    "貿易百貨":              "零售",
    "居家生活":              "零售-居家",
    "居家生活類":            "零售-居家",
    "運動休閒":              "運動休閒",
    "運動休閒類":            "運動休閒",
    "文化創意業":            "文創",
    # 能源
    "油電燃氣業":            "電力",
    "綠能環保":              "綠能",
    "綠能環保類":            "綠能",
    # 農業
    "農業科技":              "農業",
    "農業科技業":            "農業",
    # ETF / 基金
    "ETF":                   "ETF",
    "上櫃ETF":               "ETF",
    "上櫃指數股票型基金(ETF)": "ETF",
    "ETN":                   "ETN",
    "指數投資證券(ETN)":     "ETN",
    "受益證券":              "受益證券",
    # Skip (非個股)
    "所有證券":              None,
    "存託憑證":              None,
    "創新板股票":            None,
    "創新版股票":            None,
    "Index":                 None,
    "大盤":                  None,
    "其他":                  "其他",
}


def _is_stale(path: str, ttl_days: int) -> bool:
    if not os.path.exists(path):
        return True
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=TZ)
    return (datetime.now(TZ) - mtime) > timedelta(days=ttl_days)


def _fetch_finmind() -> list[dict]:
    """從 FinMind 抓完整台股清單"""
    r = requests.get(
        "https://api.finmindtrade.com/api/v4/data",
        params={"dataset": "TaiwanStockInfo"},
        timeout=30,
    )
    d = r.json()
    if d.get("status") != 200:
        raise RuntimeError(f"FinMind failed: {d}")
    return d.get("data", [])


def build_map(force_refresh: bool = False, verbose: bool = True) -> dict[str, str]:
    """回傳 {code: sector} 大字典，並更新 cache 檔.

    Args:
        force_refresh: True 時強制重抓即使 cache 還新
        verbose: 印出統計資訊
    """
    if not force_refresh and not _is_stale(CACHE_PATH, CACHE_TTL_DAYS):
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("mapping", {})

    rows = _fetch_finmind()
    if verbose:
        print(f"[tw_sector_lookup] fetched {len(rows)} stocks from FinMind")

    mapping: dict[str, str] = {}
    best_cat: dict[str, str] = {}     # code → 目前選中的 category（用於 priority 比較）
    unmapped_cats: dict[str, int] = {}
    skipped = 0
    for row in rows:
        code = row.get("stock_id", "").strip()
        cat = row.get("industry_category", "").strip()
        if not code or not cat:
            skipped += 1
            continue
        sector = _FINMIND_TO_SECTOR.get(cat)
        if sector is None and cat in _FINMIND_TO_SECTOR:
            skipped += 1
            continue
        if sector is None:
            unmapped_cats[cat] = unmapped_cats.get(cat, 0) + 1
            sector = "其他"

        # 選最具體那個：若這 cat priority 高於目前記錄，才覆蓋
        cur = best_cat.get(code)
        new_prio = _CAT_PRIORITY.get(cat, 50)   # 非電子類都預設 50（單一分類，具體）
        cur_prio = _CAT_PRIORITY.get(cur, 50) if cur else -1
        if new_prio > cur_prio:
            mapping[code] = sector
            best_cat[code] = cat

    if verbose:
        print(f"[tw_sector_lookup] mapped {len(mapping)} codes, skipped {skipped}")
        if unmapped_cats:
            print(f"[tw_sector_lookup] unmapped categories:")
            for c, n in sorted(unmapped_cats.items(), key=lambda x: -x[1]):
                print(f"  {c}: {n} 檔")

    out = {
        "_comment": "自動生成 · 從 FinMind TaiwanStockInfo 拉，套用 _FINMIND_TO_SECTOR 對應表",
        "_updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "_source": "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo",
        "_total": len(mapping),
        "mapping": mapping,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    if verbose:
        print(f"[tw_sector_lookup] saved to {CACHE_PATH}")
    return mapping


def load_map() -> dict[str, str]:
    """僅讀 cache，不觸發重抓（給 holdings_correlation 用）。"""
    if not os.path.exists(CACHE_PATH):
        return {}
    with open(CACHE_PATH, encoding="utf-8") as f:
        return json.load(f).get("mapping", {})


if __name__ == "__main__":
    force = "--refresh" in sys.argv
    m = build_map(force_refresh=force)
    print(f"\ntotal codes: {len(m)}")
    # Show sample
    for code in ["2330", "8299", "6442", "3703", "2603", "6719", "6672"]:
        s = m.get(code, "NOT FOUND")
        print(f"  {code}: {s}")
