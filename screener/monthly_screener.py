"""
monthly_screener.py — 台股長波寶藏股「第一層漏斗」月度資料任務
================================================================
每月 11 日（月營收申報截止翌日）執行。純資料處理，不用 LLM、不吃 quota。

資料來源（全部公開、免金鑰）：
  上市月營收   https://openapi.twse.com.tw/v1/opendata/t187ap05_L
  上櫃月營收   https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O
  上市季損益   https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci
  上櫃季損益   https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci
  上市 PE/PB   https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL      (best-effort)
  上櫃 PE/PB   https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis (best-effort)

產出：
  data/screener/history/rev_{ROCYYYMM}.json   月營收快照（累積，供「連續改善」判斷）
  data/screener/history/fin_{ROCYYY}Q{S}.json 季損益快照（累積，供毛利率趨勢判斷）
  data/screener/latest.json                   兩軌候選清單（掃描 session 經 Pages 讀取）

篩選邏輯（對應寶藏股研究室方法論）：
  Track A 雙擊型：當月營收 ≥1 億、當月與累計 YoY 皆 >0、
                  歷史夠深時要求連續 ≥3 個月 YoY>0；附毛利率與趨勢、PE/PB
  Track B 收稅口/成長：毛利率 ≥50% 且 EPS>0（市值過濾留給第二層人工驗證）
快照只有累積夠久（≥3 個月營收、≥2 季損益）才會啟用趨勢條件，之前為 bootstrap 模式。
"""

import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST_DIR = ROOT / "data" / "screener" / "history"
OUT_PATH = ROOT / "data" / "screener" / "latest.json"

TW_TZ = timezone(timedelta(hours=8))

REV_URLS = [
    ("twse", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"),
    ("tpex", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"),
]
FIN_URLS = [
    ("twse", "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"),
    ("tpex", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"),
]
VAL_URLS = [
    ("twse", "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"),
    ("tpex", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"),
]

TRACK_A_MIN_REV = 100_000       # 千元 = 1 億
TRACK_B_MIN_GM = 50.0           # 毛利率門檻 (%)
TRACK_CAP = 40                  # 每軌輸出上限
# 營收波動屬一次性/行情性而非結構性的產業 — 標記沉底，不剔除
ONE_OFF_INDUSTRIES = {"建材營造", "建材營造業", "金融保險", "金融保險業", "金融業", "證券業", "存託憑證"}

# --insecure 僅供本機測試（本機憑證鏈被安全軟體攔截時用）；CI 上不需要
INSECURE = "--insecure" in sys.argv


def fetch_json(url, retries=4):
    ctx = None
    if INSECURE:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 — 重試後才放棄
            last_err = e
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"fetch failed after {retries} tries: {url} — {last_err}")


def key_by_substr(row, *needles, exact=None):
    """API 中文欄位偶有全形/命名差異，用子字串比對取值；exact 優先。"""
    if exact:
        for k in exact:
            if k in row:
                return row[k]
    for k in row:
        if all(n in k for n in needles):
            return row[k]
    return None


def to_f(v):
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s not in ("", "-", "N/A", "None") else None
    except (ValueError, TypeError):
        return None


# ── 1. 月營收 ────────────────────────────────────────────────────────────────

def load_revenue():
    """回傳 (data_month, {code: {...}})，金額單位千元。"""
    merged, data_month = {}, None
    for market, url in REV_URLS:
        for row in fetch_json(url):
            code = str(
                key_by_substr(row, "公司代號", exact=["公司代號", "SecuritiesCompanyCode"]) or ""
            ).strip()
            if not code or not code.isdigit():
                continue
            month = str(key_by_substr(row, "資料年月") or "").strip()
            data_month = data_month or month
            merged[code] = {
                "name": key_by_substr(row, "公司名稱", exact=["公司名稱", "CompanyName"]),
                "industry": key_by_substr(row, "產業別") or "",
                "market": market,
                "rev": to_f(key_by_substr(row, "當月營收")),
                "yoy": to_f(key_by_substr(row, "去年同月增減")),
                "mom": to_f(key_by_substr(row, "上月比較增減")),
                "cum_yoy": to_f(key_by_substr(row, "前期比較增減")),
            }
    if not merged:
        raise RuntimeError("no revenue rows fetched")
    return data_month, merged


def rev_history(current_month):
    """讀 history 目錄裡 current_month 之前的快照，新到舊排序。"""
    snaps = []
    for p in sorted(HIST_DIR.glob("rev_*.json"), reverse=True):
        m = p.stem.replace("rev_", "")
        if m < current_month:
            snaps.append((m, json.loads(p.read_text(encoding="utf-8"))))
    return snaps


def yoy_streak(code, cur_yoy, snaps):
    """含當月在內，連續 YoY>0 的月數。"""
    if cur_yoy is None or cur_yoy <= 0:
        return 0
    streak = 1
    for _, snap in snaps:
        y = (snap.get(code) or {}).get("yoy")
        if y is not None and y > 0:
            streak += 1
        else:
            break
    return streak


# ── 2. 季損益（毛利率 / EPS） ────────────────────────────────────────────────

def load_financials():
    """回傳 (quarter_tag, {code: {...}})。"""
    merged, tag = {}, None
    for market, url in FIN_URLS:
        for row in fetch_json(url):
            code = str(
                key_by_substr(row, "公司代號", exact=["公司代號", "SecuritiesCompanyCode"]) or ""
            ).strip()
            if not code or not code.isdigit():
                continue
            year = str(key_by_substr(row, "年度", exact=["年度", "Year"]) or "").strip()
            season = str(key_by_substr(row, "季別", exact=["季別", "Season"]) or "").strip()
            tag = tag or f"{year}Q{season}"
            revenue = to_f(key_by_substr(row, "營業收入"))
            gross = to_f(key_by_substr(row, "毛利", "淨額")) or to_f(key_by_substr(row, "毛利"))
            gm = round(gross / revenue * 100, 2) if revenue and gross is not None else None
            merged[code] = {
                "revenue": revenue,
                "gm": gm,
                "eps": to_f(key_by_substr(row, "每股盈餘")),
            }
    if not merged:
        raise RuntimeError("no financial rows fetched")
    return tag, merged


def fin_history(current_tag):
    snaps = []
    for p in sorted(HIST_DIR.glob("fin_*.json"), reverse=True):
        t = p.stem.replace("fin_", "")
        if t < current_tag:
            snaps.append((t, json.loads(p.read_text(encoding="utf-8"))))
    return snaps


# ── 3. 估值（PE/PB，best-effort） ────────────────────────────────────────────

def load_valuations():
    merged = {}
    for _market, url in VAL_URLS:
        try:
            for row in fetch_json(url, retries=2):
                code = str(
                    key_by_substr(row, "公司代號", exact=["Code", "SecuritiesCompanyCode", "公司代號"]) or ""
                ).strip()
                if not code or not code.isdigit():
                    continue
                merged[code] = {
                    "pe": to_f(key_by_substr(row, "本益比", exact=["PEratio", "PriceEarningRatio"])),
                    "pb": to_f(key_by_substr(row, "淨值比", exact=["PBratio", "PriceBookRatio"])),
                }
        except Exception as e:  # noqa: BLE001 — 估值欄位缺了不影響主流程
            print(f"  [warn] valuation source skipped: {e}")
    return merged


# ── 4. 主流程 ────────────────────────────────────────────────────────────────

# ---- 第二層旗標補強（市值/已飛/流動性）----
# 只跑在「已 capped 的候選」(~80檔)、非全市場1900檔。yfinance 失敗留 None、不擋主流程。
# 對應：市值(10倍種子要小)、已飛旗標(鐵律#9別追已噴·決策前檢查清單B)、流動性(不可交易警戒)。
FLEW_POS_PCT = 70    # 52週位置 >70% = 貼近高檔
FLEW_1Y_PCT = 150    # 一年漲幅 >150% = 已噴(如廣閎科+227%)
THIN_LOTS = 30       # 近20日均量 <30張 = 薄量不可乾淨進出(創為/鋼聯/邦睿型)

def yf_flags(rows):
    """對已 capped 的 track 候選補：市值(億)/52週位置/一年漲幅/均量(張)＋已飛、薄量旗標。"""
    try:
        import yfinance as yf
    except Exception:
        return
    for r in rows:
        code, mk = r.get("code"), r.get("market", "twse")
        sufs = [".TWO", ".TW"] if mk == "tpex" else [".TW", ".TWO"]
        mcap_e = pos = ret1y = vlots = None
        for sfx in sufs:
            try:
                tk = yf.Ticker(code + sfx)
                h = tk.history(period="1y")
                if len(h) < 30:
                    continue
                cl = h["Close"]
                last, hi, lo = float(cl.iloc[-1]), float(cl.max()), float(cl.min())
                pos = round((last - lo) / (hi - lo) * 100) if hi > lo else None
                ret1y = round((last / float(cl.iloc[0]) - 1) * 100)
                vlots = round(float(h["Volume"].iloc[-20:].mean()) / 1000)
                try:
                    mc = tk.fast_info["market_cap"]
                except Exception:
                    mc = (tk.info or {}).get("marketCap")
                mcap_e = round(mc / 1e8) if mc else None
                break
            except Exception:
                continue
        r["mcap_e"] = mcap_e          # 市值(億 TWD)
        r["pos_52w"] = pos            # 52週位置 %(0=一年低/100=一年高)
        r["ret_1y"] = ret1y           # 一年漲幅 %
        r["avg_vol_lots"] = vlots     # 近20日均量(張)
        r["flew"] = bool((pos is not None and pos > FLEW_POS_PCT)
                         or (ret1y is not None and ret1y > FLEW_1Y_PCT))   # 🚀已飛
        r["thin"] = bool(vlots is not None and vlots < THIN_LOTS)          # ⚠️薄量


def main():
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TW_TZ)

    print("fetching monthly revenue…")
    data_month, rev = load_revenue()
    print(f"  {len(rev)} companies, data_month={data_month}")

    print("fetching quarterly financials…")
    fin_tag, fin = load_financials()
    print(f"  {len(fin)} companies, quarter={fin_tag}")

    print("fetching valuations (best-effort)…")
    val = load_valuations()
    print(f"  {len(val)} companies with PE/PB")

    # 累積快照（先讀歷史再寫入本期，避免把本期當歷史）
    rev_snaps = rev_history(data_month)
    fin_snaps = fin_history(fin_tag)
    (HIST_DIR / f"rev_{data_month}.json").write_text(
        json.dumps(rev, ensure_ascii=False), encoding="utf-8"
    )
    (HIST_DIR / f"fin_{fin_tag}.json").write_text(
        json.dumps(fin, ensure_ascii=False), encoding="utf-8"
    )

    hist_depth = len(rev_snaps) + 1
    bootstrap = hist_depth < 3
    prev_fin = fin_snaps[0][1] if fin_snaps else {}

    def enrich(code):
        f, v = fin.get(code, {}), val.get(code, {})
        prev = prev_fin.get(code, {})
        gm_trend = None
        if f.get("gm") is not None and prev.get("gm") is not None:
            gm_trend = round(f["gm"] - prev["gm"], 2)
        return {
            "gm": f.get("gm"), "gm_trend_pp": gm_trend, "eps": f.get("eps"),
            "pe": v.get("pe"), "pb": v.get("pb"),
        }

    # Track A：雙擊型漏斗
    track_a = []
    for code, r in rev.items():
        if (r["rev"] or 0) < TRACK_A_MIN_REV:
            continue
        if not r["yoy"] or r["yoy"] <= 0 or not r["cum_yoy"] or r["cum_yoy"] <= 0:
            continue
        streak = yoy_streak(code, r["yoy"], rev_snaps)
        if not bootstrap and streak < 3:
            continue
        track_a.append({
            "code": code, "name": r["name"], "industry": r["industry"], "market": r["market"],
            "rev_k": r["rev"], "yoy": r["yoy"], "cum_yoy": r["cum_yoy"], "mom": r["mom"],
            "yoy_streak_months": streak,
            "one_off_risk": r["industry"] in ONE_OFF_INDUSTRIES,
            **enrich(code),
        })
    # 一次性認列產業（建商交屋等）沉底；極端 YoY 多為基期噪音，連續性優先
    track_a.sort(key=lambda x: (x["one_off_risk"], -(x["yoy_streak_months"]), -(x["cum_yoy"] or 0)))
    track_a = track_a[:TRACK_CAP]

    # Track B：高毛利小型科技漏斗（市值過濾留給第二層）
    track_b = []
    for code, f in fin.items():
        if f.get("gm") is None or f["gm"] < TRACK_B_MIN_GM:
            continue
        if f.get("eps") is None or f["eps"] <= 0:
            continue
        r = rev.get(code, {})
        prev = prev_fin.get(code, {})
        gm_trend = round(f["gm"] - prev["gm"], 2) if prev.get("gm") is not None else None
        track_b.append({
            "code": code, "name": r.get("name"), "industry": r.get("industry"),
            "market": r.get("market"), "gm": f["gm"], "gm_trend_pp": gm_trend,
            "eps": f["eps"], "rev_yoy": r.get("yoy"), "rev_cum_yoy": r.get("cum_yoy"),
            "pe": val.get(code, {}).get("pe"), "pb": val.get(code, {}).get("pb"),
        })
    track_b.sort(key=lambda x: (-(x["gm"]), -(x["rev_yoy"] or -999)))
    track_b = track_b[:TRACK_CAP]

    # 第二層旗標補強（只跑 capped 後的候選）：市值/已飛/流動性
    yf_flags(track_a)
    yf_flags(track_b)

    out = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M %z"),
        "data_month_roc": data_month,
        "fin_quarter_roc": fin_tag,
        "rev_history_depth_months": hist_depth,
        "mode": "bootstrap（歷史<3個月，連續條件未啟用，僅要求當月+累計YoY>0）" if bootstrap
                else "full（連續3個月以上YoY>0 已啟用）",
        "notes": [
            "金額單位：千元。gm=最新季毛利率%，gm_trend_pp=較前一儲存季的百分點變化（需累積兩季後才有值）",
            "第二層旗標(yfinance補·capped後才跑)：mcap_e=市值(億)、pos_52w=52週位置%、ret_1y=一年漲幅%、avg_vol_lots=近20日均量(張)、flew=已飛(位置>70%或一年>150%·別追鐵律#9)、thin=薄量(<30張/日·不可乾淨進出)。值為 null=yfinance當次抓不到",
            "10倍種子讀法：找 mcap_e 小 × gm_trend_pp>0 × rev_yoy>0 × flew=false × thin=false；flew=true 多是已噴回檔(廣閎科型)、thin=true 多是不可交易(邦睿型)",
            f"one_off_risk=true 表示產業別屬 {sorted(ONE_OFF_INDUSTRIES)}，注意一次性認列",
        ],
        "track_a": track_a,
        "track_b": track_b,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(ROOT)}  (A:{len(track_a)} B:{len(track_b)}, mode={out['mode']})")


if __name__ == "__main__":
    main()
