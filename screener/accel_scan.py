"""
accel_scan.py — 營收加速 × 高毛利 × eps轉強 × 未飛 的早期捕手
================================================================
補 S0(chip-based)對「快速飆股」lag 的盲點。聯傑(3094)教訓:營收 6 月就
YoY+74%,但大戶籌碼 9 月才 confirm 累積、股價已飛 +151%。chip 趨勢要 ~4 週
成熟、且價格跟著聰明錢同步動 → chip-based S0 永遠抓不到快手。

這支改用「營收加速」當領先訊號——月營收(拉貨→出貨→跳升)先於大戶 confirm,
抓「S0 畢業 / S1 剛啟動、高毛利、還沒飛」的成長股,給人工深挖。

篩選(全市場月營收 × 季財報):
  ① 營收加速:最新月 YoY ≥ ACCEL_YOY 且 > 前幾月 YoY 均(真加速、非高基期平盤)
  ② 高毛利:gm ≥ GM_MIN(收稅口/IP DNA、非低毛利代工 beta)
  ③ eps 轉強:eps > 0(已獲利/剛轉正)——避開純燒錢
  ④ 未飛(關鍵):52週位置 ≤ POS_MAX 且 1年報酬 ≤ RET_MAX(要早、不是追已飛)
  ⑤ 排除:gm_spike 一次性(最新季 vs 前季毛利發散 > GM_SPIKE)、記憶體循環、近零營收空殼

硬規則:只產研究資料;候選仍要人工深挖(憑什麼會動 + 拆業外 + 籌碼三關)。
用法:python accel_scan.py
"""
from __future__ import annotations
import glob
import json
import os
import re
import warnings

import yfinance as yf

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(os.path.dirname(HERE), "data", "screener", "history")

ACCEL_YOY = 30.0   # 最新月 YoY 加速門檻(%)
YOY_CAP = 500.0    # YoY 上限——>此多為零基/一次性 artifact(非可持續加速),排除
GM_MIN = 40.0      # 高毛利門檻(%)
POS_MAX = 65.0     # 52週位置上限(%)——未飛
RET_MAX = 90.0     # 1年報酬上限(%)——未飛
GM_SPIKE = 8.0     # 一次性毛利嫌疑(最新季 vs 前季 pp)
REV_MIN = 20000    # 當月營收下限(千)——擋近零空殼零基 artifact
CYCLE_KW = ("記憶體", "DRAM", "面板", "鋼", "航運", "水泥")  # 循環粗濾


def _rev_files():
    return sorted(glob.glob(os.path.join(HIST, "rev_*.json")))


def _fin_files():
    return sorted(glob.glob(os.path.join(HIST, "fin_*.json")))


def build_universe():
    """①②③⑤:基本面漏斗(不碰 yfinance)。回傳候選 list。"""
    revf = _rev_files()[-4:]
    if len(revf) < 2:
        return []
    revs = [json.load(open(f, encoding="utf-8")) for f in revf]
    latest, prior = revs[-1], revs[:-1]
    finf = _fin_files()
    fin = json.load(open(finf[-1], encoding="utf-8")) if finf else {}
    fin_prev = json.load(open(finf[-2], encoding="utf-8")) if len(finf) >= 2 else {}

    uni = []
    for code, r in latest.items():
        yoy = r.get("yoy")
        rev = r.get("rev")
        if yoy is None or (rev or 0) < REV_MIN:
            continue
        # ① 加速:最新 YoY ≥ 門檻 且 > 前幾月均
        pri = [p[code]["yoy"] for p in prior if code in p and p[code].get("yoy") is not None]
        if not pri:
            continue
        avg_prior = sum(pri) / len(pri)
        if not (ACCEL_YOY <= yoy <= YOY_CAP and yoy > avg_prior):
            continue
        # ②③ gm/eps(季財報)
        f = fin.get(code)
        if not f or f.get("gm") is None or f.get("eps") is None:
            continue
        gm, eps = f["gm"], f["eps"]
        if gm < GM_MIN or eps <= 0:
            continue
        # ⑤ gm_spike 一次性
        gm_prev = (fin_prev.get(code) or {}).get("gm")
        if gm_prev is not None and gm - gm_prev > GM_SPIKE:
            continue
        name = r.get("name", "")
        industry = r.get("industry", "")
        if any(k in name or k in industry for k in CYCLE_KW):
            continue
        uni.append({"code": code, "name": name, "industry": industry, "yoy": yoy,
                    "avg_prior": round(avg_prior, 1), "cum": r.get("cum_yoy"),
                    "gm": gm, "eps": eps})
    return uni


def scan():
    """④:對基本面漏斗過的名單抓價格位置,濾掉已飛。"""
    uni = build_universe()
    out = []
    for c in uni:
        px = pos = ret = None
        for suf in (".TW", ".TWO"):
            try:
                h = yf.Ticker(c["code"] + suf).history(period="1y")
                if len(h) > 20:
                    cur = h["Close"].iloc[-1]
                    hi, lo = h["Close"].max(), h["Close"].min()
                    pos = (cur - lo) / (hi - lo) * 100
                    ret = (cur / h["Close"].iloc[0] - 1) * 100
                    px = cur
                    break
            except Exception:  # noqa: BLE001
                pass
        if px is None:
            continue
        if pos > POS_MAX or ret > RET_MAX:  # 已飛,踢掉
            continue
        c.update({"px": round(px, 1), "pos": round(pos), "ret": round(ret)})
        out.append(c)
    out.sort(key=lambda x: -x["yoy"])
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    res = scan()
    tag = os.path.basename(_rev_files()[-1]).replace("rev_", "").replace(".json", "") if _rev_files() else "?"
    print(f"[accel_scan] 資料月={tag} | 營收加速×高毛利≥{GM_MIN:.0f}×eps正×未飛(位≤{POS_MAX:.0f}/1yr≤{RET_MAX:.0f}) 候選 {len(res)} 檔:")
    if not res:
        print("  無候選(melt-up 裡加速的多半已飛、或空手;非漏掃)")
    for c in res[:25]:
        print(f"  {c['code']} {c['name'][:6]:<7}[{c['industry'][:5]}] "
              f"月YoY{c['yoy']:+.0f}%(前均{c['avg_prior']:+.0f}) 累{c['cum']:+.0f}% "
              f"gm{c['gm']:.0f}% eps{c['eps']} | 現{c['px']:.0f} 位{c['pos']}% 1yr{c['ret']:+.0f}%")
