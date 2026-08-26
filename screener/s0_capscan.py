"""
s0_capscan.py — S0 領先訊號①：聰明錢籌碼累積 × 高毛利低基期
=====================================================================
月營收螺絲是 S1 財報漏斗（只找財報已動的收稅口/雙擊）。S0（成長前·賭拐點）
的證據不在損益表裡——這支補「世芯 cap-table 訊號的資料版」：

  在「高毛利(收稅口/IP DNA) 但 EPS 在虧/趴（低基期）」的科技股裡，
  找「大戶(≥400張)籌碼正在累積(趨勢↑)」的 —— 聰明錢在財報動之前先卡位。

宇宙：讀螺絲最新季財報 fin_{ROC}.json（{code:{revenue,gm,eps}}），
      取 gm≥GM_MIN 且 eps≤EPS_MAX（收稅口margin但沒獲利＝pre-inflection）。
籌碼：重用 chip_momentum._fetch_tdcc()（一次抓全市場大戶%）。
歷史：本檔自維護 data/chip/s0_history.json（每次 snapshot append），
      約 5 週後「趨勢(4週pp變化)」成熟＝真累積訊號；成熟前只用「水平」當背景。

用法：
  python s0_capscan.py --snapshot   # 抓當週全市場大戶%、存 S0 宇宙快照（週跑）
  python s0_capscan.py              # 讀快取算 水平+趨勢、印候選（月掃/隨時）
硬規則：只產研究資料；S0 屬彩券倉單檔≤1%、絕不動房款；水平高可能是董監鎖股，
        趨勢↑才是累積；候選仍要人工過「可交易/未飛/真科技IP」三關。
"""
import glob
import json
import os
from datetime import datetime

import chip_momentum as cm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIN_DIR = os.path.join(ROOT, "data", "screener", "history")
S0_HISTORY = os.path.join(ROOT, "data", "chip", "s0_history.json")
SECTOR_MAP = os.path.join(ROOT, "data", "sector_map_auto.json")

GM_MIN = 55.0      # 收稅口/IP 級毛利
EPS_MAX = 0.5      # 在虧或趴（低基期）＝S1漏斗踢掉的、S0要的
# S0 的 tech-IP/收稅口 論述只在科技類成立；濾掉飯店/資產股/紡織（租金/一次性灌高毛利=假訊號）
TECH_KEYWORDS = ("半導體", "電子", "光電", "軟體", "電腦", "通信", "通訊",
                 "網通", "網路", "資訊", "IC", "電機")


def _load_sectors() -> dict:
    try:
        return json.load(open(SECTOR_MAP, encoding="utf-8")).get("mapping", {})
    except Exception:  # noqa: BLE001
        return {}


def _is_tech(sector: str) -> bool:
    return bool(sector) and any(k in sector for k in TECH_KEYWORDS)


def _latest_fin() -> tuple[str, dict]:
    """回傳 (檔名tag, {code:{revenue,gm,eps}})，取最新一季 fin_*.json。"""
    files = sorted(glob.glob(os.path.join(FIN_DIR, "fin_*.json")))
    if not files:
        return "", {}
    p = files[-1]
    tag = os.path.basename(p).replace("fin_", "").replace(".json", "")
    return tag, json.load(open(p, encoding="utf-8"))


def s0_universe(gm_min: float = GM_MIN, eps_max: float = EPS_MAX,
                tech_only: bool = False) -> dict:
    """高毛利低基期宇宙：{code:{gm,eps,revenue,sector}}。
    tech_only=True 只留科技/IP 類（snapshot 存廣的、scan 顯示窄的科技）。"""
    _, fin = _latest_fin()
    sectors = _load_sectors()
    uni = {}
    for c, v in fin.items():
        gm, eps = v.get("gm"), v.get("eps")
        if gm is None or eps is None:
            continue
        if gm >= gm_min and eps <= eps_max:
            sec = sectors.get(str(c), "")
            if tech_only and not _is_tech(sec):
                continue
            uni[str(c)] = {"gm": gm, "eps": eps, "revenue": v.get("revenue"), "sector": sec}
    return uni


def _load_hist() -> dict:
    if os.path.exists(S0_HISTORY):
        try:
            return json.load(open(S0_HISTORY, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def snapshot() -> str:
    """抓當週全市場大戶%、把 S0 宇宙那批存進 s0_history.json（依資料日去重、留20週）。
    週跑（掛在每日 pipeline 的 chip refresh 後）＝趨勢隨週長出來。回傳資料日。"""
    uni = s0_universe()
    date, allc = cm._fetch_tdcc()
    slice_ = {c: allc[c] for c in uni if c in allc}
    hist = _load_hist()
    hist[date] = slice_
    for k in sorted(hist)[:-20]:
        del hist[k]
    os.makedirs(os.path.dirname(S0_HISTORY), exist_ok=True)
    json.dump(hist, open(S0_HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return date


def scan() -> list:
    """讀 s0_history 算 大戶%水平+趨勢(4週pp)，回傳候選 list（accumulate 優先、再高水平）。
    每筆：{code,gm,eps,sector,big,trend_pp,signal,weeks}。趨勢不足5週＝signal 只有水平背景。"""
    uni = s0_universe(tech_only=True)
    hist = _load_hist()
    dates = sorted(hist)
    if not dates:
        return []
    cur_date = dates[-1]
    base_date = dates[-5] if len(dates) >= 5 else None  # 需5週才算趨勢
    cur = hist[cur_date]
    out = []
    for c, u in uni.items():
        ch = cur.get(c)
        if not ch:
            continue
        big = ch.get("big", 0)
        trend_pp = None
        if base_date and c in hist.get(base_date, {}):
            trend_pp = round(big - hist[base_date][c].get("big", 0), 1)
        if trend_pp is not None and trend_pp >= 1.0:
            signal = "accumulate"
        elif trend_pp is not None and trend_pp <= -1.0:
            signal = "distribute"
        else:
            signal = "neutral"
        out.append({"code": c, "gm": u["gm"], "eps": u["eps"], "sector": u.get("sector", ""),
                    "big": big, "k1000": ch.get("k1000", 0), "trend_pp": trend_pp,
                    "signal": signal, "weeks": len(dates)})
    # accumulate 優先(有趨勢且↑)，其次大戶%水平高
    out.sort(key=lambda x: (x["signal"] != "accumulate", -(x["trend_pp"] or -99),
                            -x["big"]))
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if "--snapshot" in sys.argv:
        d = snapshot()
        print(f"[s0_capscan] snapshot 資料日 {d}，S0宇宙 {len(s0_universe())} 檔已存")
    res = scan()
    if not res:
        print("[s0_capscan] 無快照，先跑 --snapshot")
    else:
        weeks = res[0]["weeks"]
        mode = f"趨勢成熟({weeks}週)" if weeks >= 5 else f"⚠️趨勢未熟({weeks}/5週)·先看水平"
        print(f"[s0_capscan] S0宇宙 {len(res)} 檔 | {mode} | 大戶%由高到低（accumulate優先）:")
        for r in res[:25]:
            t = f"{r['trend_pp']:+.1f}pp" if r["trend_pp"] is not None else "—"
            mark = "🟢累積" if r["signal"] == "accumulate" else ("🔴派發" if r["signal"] == "distribute" else "")
            print(f"  {r['code']} [{r['sector']}] gm{r['gm']:.0f}% eps{r['eps']:+.2f} | 大戶{r['big']:.1f}% 千張{r['k1000']:.1f}% 趨勢{t} {mark}")
