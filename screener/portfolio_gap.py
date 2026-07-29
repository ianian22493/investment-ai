"""
portfolio_gap.py — 組合缺口 / 分散透鏡
=====================================
系統裡第一個「對抗集中」而非「製造集中」的工具。

讀 portfolio.json，把每檔持倉用 FACTOR_MAP 標上 geo/sector/factors，
以現價加權（US 取 dashboard 現價 × USD/TWD，TW 取 portfolio 的 value），
算出整個組合（TW+US 合併）的因子曝險，然後：
  - 標出**過度集中**（單因子 > 上限）
  - 標出**缺口**（該有卻幾乎為 0 的方向：新興市場、非科技防禦…）

輸出 data/portfolio_gap.json —— 給「組合缺口分散掃描」排程當第一層，
讓它往缺口方向去獵，而不是又找一堆你已經太多的 AI/科技。

純 stdlib，不吃 LLM quota。新持倉要在 FACTOR_MAP 補標籤（未標會列 untagged）。
"""

import json
import ssl
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "portfolio_gap.json"
TW_TZ = timezone(timedelta(hours=8))
CTX = ssl.create_default_context()
if "--insecure" in sys.argv:
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE

# ── 因子標籤表（新持倉要來這裡補）──────────────────────────────────────────
# geo: TW / US / EM(新興市場) / DM-ex-US(非美已開發)
# tags: 自由標註的因子；用於 aggregate。ETF/核心層另標 core=True
FACTOR_MAP = {
    # TW
    "00692": {"geo": "TW", "sector": "ETF",        "tags": ["broad-etf"],                 "core": True},
    "00915": {"geo": "TW", "sector": "ETF",        "tags": ["dividend-etf"],              "core": True},
    "1104":  {"geo": "TW", "sector": "水泥",        "tags": ["cyclical-traditional"]},
    "2211":  {"geo": "TW", "sector": "鋼鐵",        "tags": ["cyclical-traditional"]},
    "2330":  {"geo": "TW", "sector": "半導體",      "tags": ["AI-hardware", "semis"]},
    "2603":  {"geo": "TW", "sector": "航運",        "tags": ["cyclical-shipping"]},
    "2834":  {"geo": "TW", "sector": "金融",        "tags": ["financials"]},
    "3293":  {"geo": "TW", "sector": "遊戲軟體",    "tags": ["gaming", "software"]},
    "3491":  {"geo": "TW", "sector": "衛星微波",    "tags": ["satellite", "AI-hardware"]},
    "3665":  {"geo": "TW", "sector": "連接線",      "tags": ["Musk", "AI-hardware"]},
    "3703":  {"geo": "TW", "sector": "營建",        "tags": ["cyclical-traditional"]},
    "6442":  {"geo": "TW", "sector": "光通訊",      "tags": ["AI-hardware", "optical"]},
    "8299":  {"geo": "TW", "sector": "記憶體IC",    "tags": ["AI-hardware", "memory-cycle"]},
    "2308":  {"geo": "TW", "sector": "AI電源散熱",  "tags": ["AI-hardware"]},
    "3661":  {"geo": "TW", "sector": "AI-ASIC設計", "tags": ["AI-hardware", "semis"]},
    "00675L": {"geo": "TW", "sector": "2X槓桿ETF",  "tags": ["leveraged-TW-index"]},
    "00685L": {"geo": "TW", "sector": "2X槓桿ETF",  "tags": ["leveraged-TW-index"]},
    # US
    "AMZN":  {"geo": "US", "sector": "科技電商",    "tags": ["US-megacap-tech"]},
    "CAVA":  {"geo": "US", "sector": "餐飲",        "tags": ["consumer-discretionary"]},
    "CELH":  {"geo": "US", "sector": "飲料",        "tags": ["consumer-staples"]},
    "GOOGL": {"geo": "US", "sector": "科技",        "tags": ["US-megacap-tech", "AI-software"]},
    "MSFT":  {"geo": "US", "sector": "軟體",        "tags": ["US-megacap-tech", "AI-software"]},
    "NVDA":  {"geo": "US", "sector": "半導體",      "tags": ["AI-hardware", "semis"]},
    "ONDS":  {"geo": "US", "sector": "無人機國防",  "tags": ["defense", "speculative"]},
    "RBRK":  {"geo": "US", "sector": "資安SaaS",    "tags": ["cybersecurity", "AI-software"]},
    "S":     {"geo": "US", "sector": "資安SaaS",    "tags": ["cybersecurity", "AI-software"]},
    "SOUN":  {"geo": "US", "sector": "語音AI",      "tags": ["AI-software", "speculative"]},
    "TSLA":  {"geo": "US", "sector": "電動車",      "tags": ["Musk", "AI-software"]},
    "ZS":    {"geo": "US", "sector": "資安SaaS",    "tags": ["cybersecurity", "AI-software"]},
}

# ── 又瑄的既定政策（2026-07-27 本人表述）──────────────────────────────
# 這兩項是「刻意選擇」，不是要修的問題——工具尊重它，列 policy_accepted 不示警：
#   1) 台股重：房款/近期負債是台幣，資產負債匹配（正確）
#   2) AI 重：本人信 AI 是長期趨勢，刻意高信念配置
# 但仍守三條「連 AI 多頭也該同意」的底線 guard（越線才示警）：
#   Musk 單一個人、cybersecurity 單因子、（單股上限在 watchlist.json position_rules）
POLICY_ACCEPTED = {
    "single-geo": 92,   # 台股重可接受到 92%（liquidity/liability matching）；超過才提醒
    "AI合計": 60,       # AI 高信念可接受到 60%；超過才提醒過熱
}
# 真正的 guard（本人也會同意的底線）
GUARD_LIMITS = {
    "Musk": 20,
    "cybersecurity": 15,
}
# 缺口＝「機會，不是義務」——列出來讓你知道可以往哪分散，要不要補是你的選擇
OPPORTUNITY_MIN = {
    "EM(新興市場)": 5,
    "非美非台(海外分散)": 10,
}


def fetch_us_prices():
    try:
        req = urllib.request.Request(
            "https://ianian22493.github.io/investment-dashboard/data.json",
            headers={"User-Agent": "gap"})
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            d = json.load(r)
        px = {k: (v or {}).get("price") for k, v in d.get("us", {}).items()}
        return px, float(d.get("usd_twd") or 32.0)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] dashboard fetch failed: {e}; US 用成本價、FX=32")
        return {}, 32.0


def main():
    pf = json.loads((ROOT / "data" / "portfolio.json").read_text(encoding="utf-8"))
    us_px, fx = fetch_us_prices()

    # 建立 holdings: code -> value_twd + 標籤
    holdings = []
    untagged = []
    for s in pf.get("tw_stocks", []):
        code = s["code"]
        m = FACTOR_MAP.get(code)
        if not m:
            untagged.append(code)
            m = {"geo": "TW", "sector": "?", "tags": ["untagged"]}
        holdings.append({"code": code, "value_twd": float(s.get("value") or 0), **m})
    for s in pf.get("us_stocks", []):
        t = s["ticker"]
        m = FACTOR_MAP.get(t)
        if not m:
            untagged.append(t)
            m = {"geo": "US", "sector": "?", "tags": ["untagged"]}
        px = us_px.get(t) or s.get("avg_cost_usd") or 0
        val_twd = float(s.get("shares") or 0) * float(px) * fx
        holdings.append({"code": t, "value_twd": val_twd, **m})

    total = sum(h["value_twd"] for h in holdings) or 1

    def pct_where(pred):
        return round(sum(h["value_twd"] for h in holdings if pred(h)) / total * 100, 1)

    # 地區
    geo = {}
    for g in ("TW", "US", "EM", "DM-ex-US"):
        geo[g] = pct_where(lambda h, g=g: h["geo"] == g)

    # 因子聚合
    def tag_pct(tag):
        return pct_where(lambda h, tag=tag: tag in h["tags"])

    ai = pct_where(lambda h: "AI-hardware" in h["tags"] or "AI-software" in h["tags"])
    non_tech_div = pct_where(lambda h: any(t in h["tags"] for t in
                             ("consumer-discretionary", "consumer-staples", "financials",
                              "cyclical-traditional", "cyclical-shipping")))
    core = pct_where(lambda h: h.get("core"))

    exposures = {
        "AI合計(hardware+software)": ai,
        "US-megacap-tech": tag_pct("US-megacap-tech"),
        "Musk": tag_pct("Musk"),
        "cybersecurity": tag_pct("cybersecurity"),
        "AI-hardware": tag_pct("AI-hardware"),
        "AI-software": tag_pct("AI-software"),
        "speculative": tag_pct("speculative"),
        "core-ETF": core,
        "非科技分散(消費/金融/防禦)": non_tech_div,
        "EM(新興市場)": geo["EM"],
        "非美非台(海外分散)": round(geo["EM"] + geo["DM-ex-US"], 1),
    }

    # guard 底線（本人也同意的）——越線才示警
    guard_breached = []
    for k in ("Musk", "cybersecurity"):
        if exposures[k] > GUARD_LIMITS[k]:
            guard_breached.append({"factor": k, "now": exposures[k], "limit": GUARD_LIMITS[k],
                                   "why": "Musk=單一個人風險 / cyber=單因子——非「AI 多空」問題，AI 多頭也該守"})

    # 政策接受的集中（刻意選擇，不示警；僅在超出政策上限時提醒）
    policy_accepted = []
    top_geo = max(geo["US"], geo["TW"])
    policy_accepted.append({"factor": "台股重（房款流動性）", "now": top_geo,
                            "accepted_upto": POLICY_ACCEPTED["single-geo"],
                            "status": "政策接受" if top_geo <= POLICY_ACCEPTED["single-geo"] else "已超政策上限，可留意"})
    policy_accepted.append({"factor": "AI 高信念配置", "now": ai,
                            "accepted_upto": POLICY_ACCEPTED["AI合計"],
                            "status": "政策接受" if ai <= POLICY_ACCEPTED["AI合計"] else "已超政策上限，可留意過熱"})

    # 缺口＝機會（不是義務）
    opportunities = []
    if exposures["EM(新興市場)"] < OPPORTUNITY_MIN["EM(新興市場)"]:
        opportunities.append({"direction": "EM 新興市場", "now": exposures["EM(新興市場)"],
                              "suggest": OPPORTUNITY_MIN["EM(新興市場)"],
                              "note": "拉美/東南亞/印度金融科技與電商（如 NU/SE/STNE）——與 AI/科技不相關，是選項不是義務"})
    if exposures["非美非台(海外分散)"] < OPPORTUNITY_MIN["非美非台(海外分散)"]:
        opportunities.append({"direction": "美台以外地區", "now": exposures["非美非台(海外分散)"],
                              "suggest": OPPORTUNITY_MIN["非美非台(海外分散)"],
                              "note": "地緣目前集中美+台；想分散地緣風險時可往這找"})

    out = {
        "generated_at": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M %z"),
        "fx_usd_twd": fx,
        "total_equity_twd": round(total),
        "doc": "組合缺口透鏡（尊重又瑄政策版）：台股重與AI重是刻意選擇→policy_accepted不示警；"
               "guard_breached=連AI多頭也該守的底線（Musk單人/單因子）；opportunities=缺口機會（選項非義務）。",
        "policy_note": "又瑄政策：①台股重=房款台幣負債匹配（正確）②AI重=信長期趨勢的高信念配置。"
                       "工具只在 Musk/單因子越線、或集中超出政策上限時提醒。",
        "geography": geo,
        "exposures": exposures,
        "guard_breached": guard_breached,
        "policy_accepted": policy_accepted,
        "opportunities": opportunities,
        "untagged": untagged,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  地區: TW {geo['TW']}% / US {geo['US']}%   AI合計 {ai}% · Musk {exposures['Musk']}% · 資安 {exposures['cybersecurity']}%")
    print(f"  guard越線 {len(guard_breached)} · 政策接受 {len(policy_accepted)} · 缺口機會 {len(opportunities)} · untagged {len(untagged)}")


if __name__ == "__main__":
    main()
