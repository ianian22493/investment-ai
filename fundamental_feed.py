"""
基本面資料饋送（寶藏雷達連動 #1 · 2026-07-17）

讀取寶藏股研究室真篩選器（screener/monthly_screener.py）累積的資料：
- data/screener/history/rev_YYYMM.json — 全市場 ~1,960 家月營收（name/industry/rev/yoy/mom/cum_yoy）
- data/screener/history/fin_YYYQn.json — 季度財務（revenue/gm/eps）

輸出給 tw_daily_pick 的 prompt 注入：每檔候選股的真實月營收動能 + 毛利率。
波段策略要求「基本面動能必要」，在此之前 LLM 只能從新聞猜——現在用真數據。

不呼叫任何 API、不用 LLM，純本地檔案讀取。
"""
from __future__ import annotations
import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.join(ROOT, "data", "screener", "history")


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _rev_files_sorted() -> list[str]:
    """rev_11406.json ... rev_11506.json → 按民國年月排序（新→舊）。"""
    if not os.path.isdir(HIST_DIR):
        return []
    files = []
    for fn in os.listdir(HIST_DIR):
        m = re.match(r"^rev_(\d{5})\.json$", fn)
        if m:
            files.append((int(m.group(1)), os.path.join(HIST_DIR, fn)))
    files.sort(reverse=True)
    return [p for _, p in files]


def _latest_fin_file() -> str | None:
    """fin_115Q1.json → 取字典序最大（年+季遞增，字典序即時間序）。"""
    if not os.path.isdir(HIST_DIR):
        return None
    fins = sorted(f for f in os.listdir(HIST_DIR) if re.match(r"^fin_\d{3}Q\d\.json$", f))
    return os.path.join(HIST_DIR, fins[-1]) if fins else None


def get_fundamentals(codes: list[str], streak_months: int = 15) -> dict[str, dict]:
    """回傳 {code: {rev_yoy, rev_mom, cum_yoy, yoy_streak, yoy_series, gm, eps, industry, data_month}}。

    yoy_streak：從最新月往回數，連續 YoY > 0 的月數（最多看 streak_months 個月）。
    yoy_series：近 streak_months 個月的 YoY（由舊到新），供呼叫端算加速/減速趨勢。
    預設 15＝載入全部可用歷史（目前 13 個月）：streak 不被上限截斷（否則「連N月正」
    永遠 ≤ 上限、且 streak≥12 的分類永遠觸不到＝死碼）。
    找不到資料的 code 不會出現在結果裡（呼叫端顯示「無資料」）。
    """
    rev_files = _rev_files_sorted()
    if not rev_files:
        return {}

    # 載入最新一個月 + 往回 streak_months 個月
    monthly: list[dict] = []
    for p in rev_files[: streak_months]:
        d = _load_json(p)
        if d:
            monthly.append(d)
    if not monthly:
        return {}

    latest = monthly[0]
    latest_month = re.search(r"rev_(\d{5})", rev_files[0]).group(1)  # e.g. "11506"

    fin_path = _latest_fin_file()
    fin = _load_json(fin_path) or {}
    # 毛利率所屬季別（附時效，避免在 Q3 看 Q1 毛利卻不知道是舊的）
    fin_q = None
    if fin_path:
        m = re.search(r"fin_(\d{3})Q(\d)", fin_path)
        if m:
            fin_q = f"{int(m.group(1)) + 1911 - 2000:02d}Q{m.group(2)}"  # 115Q1 → 26Q1

    out: dict[str, dict] = {}
    for code in codes:
        code = str(code)
        row = latest.get(code)
        if not row:
            continue
        # 連續正成長月數（從最新往回數）
        streak = 0
        for m in monthly:
            r = m.get(code)
            if r and isinstance(r.get("yoy"), (int, float)) and r["yoy"] > 0:
                streak += 1
            else:
                break
        # 近幾月 YoY 序列（由舊到新）— 供呼叫端算加速/減速
        series = []
        for m in reversed(monthly):
            r = m.get(code)
            if r and isinstance(r.get("yoy"), (int, float)):
                series.append(round(r["yoy"], 1))
        f = fin.get(code) or {}
        out[code] = {
            "industry":   row.get("industry", ""),
            "rev_yoy":    round(row["yoy"], 1) if isinstance(row.get("yoy"), (int, float)) else None,
            "rev_mom":    round(row["mom"], 1) if isinstance(row.get("mom"), (int, float)) else None,
            "cum_yoy":    round(row["cum_yoy"], 1) if isinstance(row.get("cum_yoy"), (int, float)) else None,
            "yoy_streak": streak,
            "yoy_series": series,
            "gm":         f.get("gm"),
            "gm_quarter": fin_q,          # 毛利率所屬季別，e.g. "26Q1"
            "eps":        f.get("eps"),
            "data_month": latest_month,   # 民國年月，e.g. 11506 = 2026-06
        }
    return out


def format_for_prompt(code: str, fund: dict | None) -> str:
    """把單檔基本面壓成一行 prompt 文字。"""
    if not fund:
        return "基本面：無月營收資料"
    parts = []
    if fund.get("rev_yoy") is not None:
        parts.append(f"月營收YoY{fund['rev_yoy']:+.1f}%")
    if fund.get("rev_mom") is not None:
        parts.append(f"MoM{fund['rev_mom']:+.1f}%")
    if fund.get("yoy_streak"):
        parts.append(f"連{fund['yoy_streak']}月正成長")
    if fund.get("cum_yoy") is not None:
        parts.append(f"累計YoY{fund['cum_yoy']:+.1f}%")
    if fund.get("gm") is not None:
        gmq = f"({fund['gm_quarter']})" if fund.get("gm_quarter") else ""
        parts.append(f"毛利率{fund['gm']:.1f}%{gmq}")
    if fund.get("eps") is not None:
        parts.append(f"季EPS {fund['eps']}")
    return "基本面：" + "、".join(parts) if parts else "基本面：無月營收資料"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Smoke: 用今日 scanner 候選測覆蓋率
    cands = _load_json(os.path.join(ROOT, "data", "candidate_stocks.json")) or {}
    codes = [c["code"] for c in cands.get("candidates", [])]
    if not codes:
        codes = ["2330", "8299", "1101"]
    funds = get_fundamentals(codes)
    print(f"覆蓋 {len(funds)}/{len(codes)} 檔")
    for c in codes[:8]:
        print(f"  {c}: {format_for_prompt(c, funds.get(c))}")
