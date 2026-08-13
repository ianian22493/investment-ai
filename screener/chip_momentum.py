"""
chip_momentum.py — 寶藏名單的「大戶籌碼動能」引擎（聰明錢·台股版）
=====================================================================
美股用 13F 追聰明錢；台股小型股投信/外資覆蓋低（原型已證），對的訊號是
**集保(TDCC)大戶籌碼集中**——大戶(≥400張)% 上升＝主力/隱形大戶悄悄累積，
若股價還沒動＝「起漲前的腳步聲」，正好補寶藏系統「進場計時」的缺口。

資料源：TDCC 官方 opendata 集保戶股權分散表（id=1-5，每週更新，全市場4千+檔）。
  持股分級 12-15＝≥400張大戶、15＝千張大戶、1-2＝散戶(≤5張)、17＝合計。
分工：refresh() 每週抓一次(週檢呼叫)→寫 data/chip/{latest,history}.json；
      chip_momentum() 讀快取算 大戶%/趨勢/訊號（哨兵每日呼叫，零網路）。

signal：accumulate(🟢大戶累積·起漲前訊號) / distribute(🔴大戶派發) / neutral
注意：大戶%「水平高」可能是董監/創辦人鎖住(靜態)，**真正該看的是「趨勢(4週變化)」**
      ——大戶%上升才是累積。水平只當籌碼集中度參考。
"""
import csv
import io
import json
import os
import ssl
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHIP_DIR = os.path.join(ROOT, "data", "chip")
LATEST = os.path.join(CHIP_DIR, "latest.json")
HISTORY = os.path.join(CHIP_DIR, "history.json")
TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"

BIG_LEVELS = ("12", "13", "14", "15")   # ≥400張大戶
K1000_LEVEL = "15"                       # 千張大戶(≥1,000張)
RETAIL_LEVELS = ("1", "2")               # 散戶(≤5張)


def _ssl_ctx():
    # TDCC 憑證缺 Subject Key Identifier，新版 OpenSSL 會拒絕；公開 open data 用寬鬆 context
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_tdcc() -> tuple[str, dict]:
    """抓 TDCC 集保股權分散表，回傳 (資料日, {code: {big, k1000, retail}})。"""
    req = urllib.request.Request(TDCC_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx()) as r:
        raw = r.read().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(raw)))[1:]
    date = ""
    lv: dict[str, dict[str, float]] = {}
    for r in rows:
        if len(r) < 6:
            continue
        date = r[0].strip()
        code = r[1].strip()
        try:
            lv.setdefault(code, {})[r[2].strip()] = float(r[5] or 0)
        except ValueError:
            continue
    out = {}
    for code, d in lv.items():
        out[code] = {
            "big": round(sum(d.get(l, 0) for l in BIG_LEVELS), 1),
            "k1000": round(d.get(K1000_LEVEL, 0), 1),
            "retail": round(sum(d.get(l, 0) for l in RETAIL_LEVELS), 1),
        }
    return date, out


def refresh(codes: list[str] | None = None) -> str:
    """每週抓一次 TDCC（週檢呼叫）：寫 latest.json（指定 codes）+ append history.json。
    codes 省略＝讀 watchlist.json 的 rule=zone 全部。回傳資料日。"""
    if codes is None:
        try:
            wl = json.load(open(os.path.join(ROOT, "data", "watchlist.json"), encoding="utf-8"))
            codes = [s["symbol"].split(".")[0] for s in wl.get("stocks", []) if s.get("rule") == "zone"]
        except Exception:  # noqa: BLE001
            codes = []
    date, allc = _fetch_tdcc()
    stocks = {c: allc[c] for c in codes if c in allc}
    os.makedirs(CHIP_DIR, exist_ok=True)
    json.dump({"date": date, "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"), "stocks": stocks},
              open(LATEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    hist = {}
    if os.path.exists(HISTORY):
        try:
            hist = json.load(open(HISTORY, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            hist = {}
    hist[date] = stocks   # 依資料日去重（同週重跑覆蓋）
    # 只留最近 20 週
    for k in sorted(hist)[:-20]:
        del hist[k]
    json.dump(hist, open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return date


def refresh_if_stale(days: int = 6) -> bool:
    """快取超過 days 天(TDCC 週更)就自動 refresh。哨兵每日呼叫＝靠可靠的 GH cron
    自我更新，不依賴本機週檢排程。回傳是否有 refresh。"""
    try:
        if os.path.exists(LATEST):
            latest = json.load(open(LATEST, encoding="utf-8"))
            asof = latest.get("as_of", "")[:10]
            if asof:
                age = (datetime.now().date() - datetime.strptime(asof, "%Y-%m-%d").date()).days
                if age < days:
                    return False
        refresh()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [chip] refresh_if_stale skipped: {e}")
        return False


def _conc_label(big: float) -> str:
    if big >= 70:
        return "籌碼超集中🔒"
    if big >= 55:
        return "集中"
    if big >= 40:
        return "中等"
    return "散戶多籌碼鬆"


def chip_momentum(codes: list[str]) -> dict:
    """讀快取算大戶籌碼 + 趨勢。回傳 code -> {big,k1000,retail,trend_pp,signal,brief,as_of}。"""
    if not os.path.exists(LATEST):
        return {}
    latest = json.load(open(LATEST, encoding="utf-8"))
    stocks = latest.get("stocks", {})
    date = latest.get("date", "")
    hist = {}
    if os.path.exists(HISTORY):
        try:
            hist = json.load(open(HISTORY, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            hist = {}
    dates = sorted(hist)
    # ~4 週前那筆當趨勢基準（不足就取最舊一筆）
    base_date = dates[-5] if len(dates) >= 5 else (dates[0] if dates else None)
    out = {}
    for code in [str(c) for c in codes]:
        cur = stocks.get(code)
        if not cur:
            continue
        big = cur["big"]
        trend_pp = None
        if base_date and base_date != date and code in hist.get(base_date, {}):
            trend_pp = round(big - hist[base_date][code]["big"], 1)
        # 訊號：趨勢優先；水平只當背景
        if trend_pp is not None and trend_pp >= 1.0:
            signal, tlabel = "accumulate", f"、大戶{trend_pp:+.1f}pp↑累積🟢"
        elif trend_pp is not None and trend_pp <= -1.0:
            signal, tlabel = "distribute", f"、大戶{trend_pp:+.1f}pp↓派發🔴"
        else:
            signal = "neutral"
            tlabel = f"、大戶{trend_pp:+.1f}pp持平" if trend_pp is not None else "、趨勢累積中"
        brief = f"大戶{big:.0f}%·千張{cur['k1000']:.0f}%·散戶{cur['retail']:.0f}%（{_conc_label(big)}{tlabel}）"
        out[code] = {"big": big, "k1000": cur["k1000"], "retail": cur["retail"],
                     "trend_pp": trend_pp, "signal": signal, "brief": brief, "as_of": date}
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if "--refresh" in sys.argv:
        d = refresh()
        print(f"[chip] refreshed TDCC 集保 資料日 {d}")
    wl = ["6902", "1256", "6534", "7749", "3546", "5306", "2755", "4571"]
    res = chip_momentum(wl)
    print(f"[chip_momentum] 資料日 {list(res.values())[0]['as_of'] if res else '—'}")
    for c in wl:
        r = res.get(c)
        print(f"  {c}: {r['brief']} | {r['signal']}" if r else f"  {c}: (無資料)")
