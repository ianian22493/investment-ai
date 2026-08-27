"""
mis_scan.py — 誤殺掃描 helper（①資料化前導檢查 ②循環股旗標）
=====================================================================
每日例行公事跑「誤殺股」時用（不自動排程·手動跑）。誤殺＝基本面完好 × 跌深 ×
近低 × 前導OK。v1 只有「品質池+跌深」、前導全手動；本 helper 把前導**部分資料化**，
先擋掉兩大死法：接到「價值陷阱」（營收/獲利在崩）、接到「循環頂」（跌深≠誤殺）。

流程：
  1. 品質池：fin 最新季 H1累計 eps>EPS_MIN(穩定獲利) 且 gm>=GM_MIN(好毛利)
  2. 跌深：yf.download(1y) 距52週高 < -DROP_MIN% 且 止穩(近20日 > -12%) 且 量>=30張
  3. ①前導三燈（資料化）：
       營收燈 = 最新月營收 YoY（>0 需求沒崩🟢 / <0 衰退🔴＝可能陷阱）
       毛利燈 = 最新季 gm vs 前季（升/平🟢 / 降🟡＝暫時or結構待查）
       eps 燈 = 去累計單季 eps 趨勢（獲利擴大/持平🟢 / 惡化🔴＝獲利在衰）
  4. ②循環旗標：鋼/航運/水泥/塑膠/造紙/玻璃/橡膠 + 記憶體/MLCC/面板(硬編碼)
       → 循環股「跌深」多是循環見頂非誤殺（盈餘創高+股價跌＝給低PE），
         **改看合約價/稼動率/月營收動能·不看跌幅**；歸「循環·另眼看」不進乾淨誤殺
  5. 分類輸出：🟢乾淨誤殺(營收正+eps沒崩+非循環) / 🔄循環另眼看 / 🔴價值陷阱
     → 只深挖🟢那批的「為什麼跌」（多半是暫時毛利/情緒殺＝真誤殺）
（③聰明錢疊加＝大戶累積 待下一版）

用法：cd /c/tmp/investment-ai && python screener/mis_scan.py
硬規則：只產研究清單、不下單不push；🟢仍要人工深挖前導才可買；信念錯配不買。
"""
import glob
import json
import os
import warnings

import yfinance as yf

import chip_momentum as cm   # ③大戶籌碼(TDCC)
import s0_capscan as s0      # 重用去累計單季 eps_trend + 品質池大戶%歷史

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HIST = os.path.join(ROOT, "data", "screener", "history")
SECTOR_MAP = os.path.join(ROOT, "data", "sector_map_auto.json")

EPS_MIN, GM_MIN, DROP_MIN = 2.0, 30.0, 30.0
WATCHLIST = {"1736", "6902", "6534", "5306", "4966", "7749", "4571", "1256", "2755", "3546"}
# ②循環：宏觀循環產業(關鍵字) + 記憶體/MLCC/面板(硬編碼·藏在半導體/電子零組件裡)
CYCLICAL_SECTORS = ("鋼", "航運", "水泥", "造紙", "玻璃", "橡膠", "塑膠", "航空", "油電")
CYCLICAL_CODES = {
    "2408", "2344", "3260", "8299", "5289", "4967", "2451", "3006", "2337",  # 記憶體/DRAM/NAND
    "2327", "2492", "3026", "2375",                                          # MLCC/被動
    "2409", "3481", "6116",                                                  # 面板
    "2303", "6770",                                                          # 循環代工
}


def _latest_rev() -> dict:
    files = sorted(glob.glob(os.path.join(HIST, "rev_*.json")))
    return json.load(open(files[-1], encoding="utf-8")) if files else {}


def _fin_two() -> tuple[dict, dict]:
    files = sorted(glob.glob(os.path.join(HIST, "fin_*.json")))
    q2 = json.load(open(files[-1], encoding="utf-8")) if files else {}
    q1 = json.load(open(files[-2], encoding="utf-8")) if len(files) >= 2 else {}
    return q1, q2


def _sectors() -> dict:
    try:
        return json.load(open(SECTOR_MAP, encoding="utf-8")).get("mapping", {})
    except Exception:  # noqa: BLE001
        return {}


def _is_cyclical(code: str, sector: str) -> bool:
    if code in CYCLICAL_CODES:
        return True
    return bool(sector) and any(k in sector for k in CYCLICAL_SECTORS)


def _chip_overlay(recs: list) -> None:
    """③聰明錢疊加：抓當前大戶%(水平)＋讀 quality_chip_history 算 4週趨勢。
    跌深+大戶累積🟢=高信念誤殺(有人在撿)；跌深+大戶派發🔴=陷阱警訊(FPS/CBRS反面)。
    趨勢需 s0_capscan 每週snapshot 累積約5週才成熟；未熟只顯示水平。"""
    if not recs:
        return
    try:
        _, allc = cm._fetch_tdcc()
    except Exception:  # noqa: BLE001
        return
    qhist = {}
    if os.path.exists(s0.QUALITY_CHIP_HISTORY):
        try:
            qhist = json.load(open(s0.QUALITY_CHIP_HISTORY, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            qhist = {}
    dates = sorted(qhist)
    base = dates[-5] if len(dates) >= 5 else None      # ~4週前(需5筆才算趨勢)
    for r in recs:
        cur = allc.get(r["code"])
        if not cur:
            continue
        r["big"] = cur["big"]
        tp = None
        if base and r["code"] in qhist.get(base, {}):
            tp = round(cur["big"] - qhist[base][r["code"]]["big"], 1)
        r["chip_trend"] = tp
        r["chip_sig"] = ("累積🟢" if tp is not None and tp >= 1 else
                         "派發🔴" if tp is not None and tp <= -1 else "")


def scan(eps_min=EPS_MIN, gm_min=GM_MIN, drop_min=DROP_MIN) -> dict:
    q1, q2 = _fin_two()
    rev = _latest_rev()
    sectors = _sectors()
    pool = [c for c, v in q2.items()
            if (v.get("eps") or 0) > eps_min and (v.get("gm") or 0) >= gm_min and c not in WATCHLIST]
    out = {"clean": [], "cyclical": [], "trap": []}
    for i in range(0, len(pool), 40):
        chunk = pool[i:i + 40]
        tks = " ".join(f"{c}.TW" for c in chunk) + " " + " ".join(f"{c}.TWO" for c in chunk)
        try:
            data = yf.download(tks, period="1y", progress=False, group_by="ticker", threads=True)
        except Exception:  # noqa: BLE001
            continue
        for c in chunk:
            for sfx in (".TW", ".TWO"):
                try:
                    ser = data[c + sfx]["Close"].dropna()
                except Exception:  # noqa: BLE001
                    continue
                if len(ser) < 60:
                    continue
                cur, hi = ser.iloc[-1], ser.max()
                frm = (cur / hi - 1) * 100
                d20 = (cur / ser.iloc[-21] - 1) * 100 if len(ser) >= 21 else 0
                if frm >= -drop_min or d20 <= -12:      # 沒跌夠 or 還在急殺
                    break
                sec = sectors.get(c, "")
                ryoy = (rev.get(c) or {}).get("yoy")            # 營收燈
                gmdir = round((q2.get(c, {}).get("gm") or 0) - (q1.get(c, {}).get("gm") or 0), 1)  # 毛利燈
                et = s0.eps_trend(c)                            # eps 燈(去累計單季)
                rec = {"code": c, "name": (rev.get(c) or {}).get("name", "?"), "sector": sec,
                       "px": round(cur, 1), "from_high": round(frm), "d20": round(d20),
                       "rev_yoy": round(ryoy, 1) if ryoy is not None else None,
                       "gm_dir": gmdir, "eps_label": et["eps_label"] if et and "eps_label" in et else (et["label"] if et else "—"),
                       "gm": q2.get(c, {}).get("gm"), "eps_h1": q2.get(c, {}).get("eps")}
                # ★分類鐵律：需求(營收)才是誤殺 vs 陷阱的分水嶺，不是單季eps。
                # 「營收強 + Q2毛利/eps dip」= 暫時成本誤殺(嘉澤/穎崴型)＝最強誤殺，留clean。
                # 「營收轉負」= 需求存疑/衰退＝可能真陷阱，另看。
                rec["margin_dip"] = bool(et and et["label"] == "惡化🔴")  # 標margin dip(強誤殺線索)
                if _is_cyclical(c, sec):
                    out["cyclical"].append(rec)
                elif ryoy is not None and ryoy < 0:
                    out["trap"].append(rec)               # 營收轉負＝需求存疑，別急著當誤殺
                else:
                    out["clean"].append(rec)              # 🟢營收正+非循環＝誤殺候選(含暫時毛利dip型)
                break
    for k in out:
        out[k].sort(key=lambda x: x["from_high"])          # 跌最深優先
    _chip_overlay(out["clean"])                            # ③只對乾淨誤殺池疊大戶籌碼
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = scan()
    def _fmt(x, chip=False):
        ry = f"營收{x['rev_yoy']:+.0f}%" if x["rev_yoy"] is not None else "營收—"
        gd = f"毛利{x['gm_dir']:+.1f}pp"
        dip = " 💠暫時毛利dip" if x.get("margin_dip") else ""
        ch = ""
        if chip and x.get("big") is not None:
            tp = x.get("chip_trend")
            ch = f" |大戶{x['big']:.0f}%{('趨勢%+.1fpp' % tp if tp is not None else '')}{x.get('chip_sig','')}"
        return (f"  {x['code']} {x['name'][:8]:8}[{(x['sector'] or '')[:8]:8}] 現{x['px']:7.0f} "
                f"距高{x['from_high']:+3}% |{ry} {gd} eps:{x['eps_label']}{dip}{ch}")
    # clean 內：大戶累積🟢優先，其次💠暫時毛利dip型(營收強+Q2毛利被咬·嘉澤型)，其次跌最深
    clean = sorted(r["clean"], key=lambda x: (x.get("chip_sig") != "累積🟢", not x.get("margin_dip"), x["from_high"]))
    print(f"🟢 乾淨誤殺候選（營收正+非循環）{len(r['clean'])} 檔——只深挖這批（💠毛利dip強誤殺·大戶累積🟢優先）：")
    for x in clean[:14]:
        print(_fmt(x, chip=True))
    print(f"\n🔄 循環·另眼看（跌深≠誤殺·改看合約價/稼動率月營收）{len(r['cyclical'])} 檔：")
    for x in r["cyclical"][:8]:
        print(_fmt(x))
    print(f"\n🔴 營收轉負·需求存疑（別急著當誤殺·先查是lumpy還是真衰退）{len(r['trap'])} 檔：")
    for x in r["trap"][:8]:
        print(_fmt(x))
