"""
s0_capscan.py — S0 領先訊號①聰明錢籌碼累積 ×③虧損收窄 × 高毛利低基期
=====================================================================
月營收螺絲是 S1 財報漏斗（只找財報已動的收稅口/雙擊）。S0（成長前·賭拐點）
的證據不在損益表裡——這支疊兩個領先訊號：
  ①籌碼累積（大戶趨勢↑＝聰明錢財報動前先卡位·世芯 cap-table 的資料版）
  ③虧損收窄（eps 逐季往上逼近損平＝盈餘拐點前·分得出「拐點近」vs「還在燒」）
  **①×③同時亮＝⭐黃金組合**（聰明錢在累積、且真的要轉盈了）。
以下第①條說明：

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


def _fin_files() -> list:
    """所有季損益快照路徑，舊→新排序。"""
    return sorted(glob.glob(os.path.join(FIN_DIR, "fin_*.json")))


def _latest_fin() -> tuple[str, dict]:
    """回傳 (檔名tag, {code:{revenue,gm,eps}})，取最新一季 fin_*.json。"""
    files = _fin_files()
    if not files:
        return "", {}
    p = files[-1]
    tag = os.path.basename(p).replace("fin_", "").replace(".json", "")
    return tag, json.load(open(p, encoding="utf-8"))


def eps_trend(code: str) -> dict | None:
    """S0 領先訊號③：虧損收窄 trend（逼近損平＝盈餘拐點前）。
    讀最近兩季 fin_*.json 的 eps，回傳 {prev,cur,delta,label}。
    label：轉正🟢(虧→賺)／收窄🟢(仍虧但↑逼近損平)／惡化🔴(虧擴大or賺變虧)／持平。"""
    files = _fin_files()
    if len(files) < 2:
        return None
    cur = json.load(open(files[-1], encoding="utf-8")).get(str(code), {}).get("eps")
    prev = json.load(open(files[-2], encoding="utf-8")).get(str(code), {}).get("eps")
    if cur is None or prev is None:
        return None
    delta = round(cur - prev, 2)
    if prev <= 0 < cur:
        label = "轉正🟢"
    elif cur <= 0 and delta > 0.02:
        label = "虧損收窄🟢"      # 仍虧但往上＝逼近損平拐點
    elif delta < -0.02:
        label = "惡化🔴"
    else:
        label = "持平"
    return {"prev": prev, "cur": cur, "delta": delta, "label": label}


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
        et = eps_trend(c)   # ③虧損收窄
        narrowing = bool(et and et["label"] in ("轉正🟢", "虧損收窄🟢"))
        golden = (signal == "accumulate") and narrowing   # ①×③黃金組合
        out.append({"code": c, "gm": u["gm"], "eps": u["eps"], "sector": u.get("sector", ""),
                    "big": big, "k1000": ch.get("k1000", 0), "trend_pp": trend_pp,
                    "signal": signal, "eps_trend": et, "narrowing": narrowing,
                    "golden": golden, "weeks": len(dates)})
    # 排序：黃金組合(①累積×③收窄)→其次收窄→其次大戶累積→其次大戶%水平
    out.sort(key=lambda x: (not x["golden"], not x["narrowing"],
                            x["signal"] != "accumulate", -(x["trend_pp"] or -99), -x["big"]))
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
            et = r["eps_trend"]
            etxt = f"eps{et['prev']:+.2f}→{et['cur']:+.2f} {et['label']}" if et else "eps趨勢—"
            gold = "⭐黃金" if r["golden"] else ""
            print(f"  {r['code']} [{r['sector']}] gm{r['gm']:.0f}% | 大戶{r['big']:.1f}% 趨勢{t}{mark} | {etxt} {gold}")
