"""
watchlist_watcher.py — 觀察名單哨兵（每日隨 daily_analysis 執行）
==================================================================
讀 data/watchlist.json 的價格/指數觸發規則，用 yfinance 檢查：
  - panic：VIX > 門檻、加權指數單日跌幅 < 門檻 → 恐慌日訊號
  - stocks：below_price / below_ma20 / near_52w_low

輸出 data/alerts.json（含現值與觸發狀態）；「新觸發」（上次未觸發、這次觸發）
會透過 DISCORD_WEBHOOK_URL 推播（未設定該 secret 則只落檔）。
純價格哨兵——事件型觸發（認證公告、財報條件）仍靠月度掃描與人工。
"""

import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "data" / "watchlist.json"
ALERTS = ROOT / "data" / "alerts.json"
TW_TZ = timezone(timedelta(hours=8))


def last_closes(symbol, period="1y"):
    try:
        df = yf.Ticker(symbol).history(period=period, auto_adjust=False)
        closes = df["Close"].dropna()
        return closes if len(closes) else None
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] {symbol}: {e}")
        return None


def check_stock(entry):
    closes = last_closes(entry["symbol"])
    if closes is None or len(closes) < 25:
        return None
    px = float(closes.iloc[-1])
    rule = entry["rule"]
    hit, detail = False, ""
    if rule == "below_price":
        hit = px < entry["value"]
        detail = f"現價 {px:.2f} vs 觸發價 {entry['value']}"
    elif rule == "below_ma20":
        ma20 = float(closes.tail(20).mean())
        hit = px < ma20
        detail = f"現價 {px:.2f} vs 月線 {ma20:.2f}"
    elif rule == "near_52w_low":
        low = float(closes.min())
        pct = entry.get("pct", 3.0)
        hit = px <= low * (1 + pct / 100)
        detail = f"現價 {px:.2f} vs 52週低 {low:.2f}（門檻 +{pct}%）"
    key = f"{entry['symbol']}|{rule}|{entry.get('value', '')}"
    return {"key": key, "symbol": entry["symbol"], "name": entry["name"],
            "rule": rule, "hit": hit, "detail": detail, "note": entry["note"]}


def check_panic(cfg):
    out = []
    vix = last_closes("^VIX", period="1mo")
    if vix is not None and len(vix):
        v = float(vix.iloc[-1])
        out.append({"key": "panic|vix", "symbol": "^VIX", "name": "VIX",
                    "rule": f"above_{cfg['vix_above']}", "hit": v > cfg["vix_above"],
                    "detail": f"VIX {v:.1f}", "note": "恐慌日條件一：照恐慌日SOP操作"})
    twii = last_closes("^TWII", period="1mo")
    if twii is not None and len(twii) >= 2:
        chg = (float(twii.iloc[-1]) / float(twii.iloc[-2]) - 1) * 100
        out.append({"key": "panic|twii", "symbol": "^TWII", "name": "加權指數",
                    "rule": f"daily_drop_below_{cfg['twii_daily_drop_pct_below']}",
                    "hit": chg <= cfg["twii_daily_drop_pct_below"],
                    "detail": f"單日 {chg:+.2f}%", "note": "恐慌日條件二：照恐慌日SOP操作"})
    return out


def discord_notify(new_hits):
    hook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not hook:
        print("  DISCORD_WEBHOOK_URL 未設定，僅落檔不推播")
        return
    lines = [f"**{a['name']}**（{a['symbol']}）{a['detail']}\n→ {a['note']}" for a in new_hits]
    payload = {
        "username": "寶藏股哨兵",
        "embeds": [{
            "title": f"🔔 觀察名單觸發 ×{len(new_hits)}",
            "description": "\n\n".join(lines)[:3900],
            "color": 13931573,
        }],
    }
    req = urllib.request.Request(
        hook, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "watcher"})
    try:
        urllib.request.urlopen(req, timeout=30)
        print(f"  Discord 推播 {len(new_hits)} 則")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] Discord 推播失敗: {e}")


def main():
    cfg = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    results = check_panic(cfg["panic"])
    for entry in cfg["stocks"]:
        r = check_stock(entry)
        if r:
            results.append(r)

    prev_hits = set()
    if ALERTS.exists():
        try:
            prev = json.loads(ALERTS.read_text(encoding="utf-8"))
            prev_hits = {a["key"] for a in prev.get("results", []) if a.get("hit")}
        except Exception:  # noqa: BLE001
            pass

    hits = [r for r in results if r["hit"]]
    new_hits = [r for r in hits if r["key"] not in prev_hits]

    ALERTS.write_text(json.dumps({
        "checked_at": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M %z"),
        "hit_count": len(hits),
        "new_hit_count": len(new_hits),
        "results": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"checked {len(results)} rules: {len(hits)} hit ({len(new_hits)} new)")
    for a in hits:
        marker = "NEW" if a in new_hits else "ongoing"
        print(f"  [{marker}] {a['name']} {a['detail']} -> {a['note']}")
    if new_hits:
        discord_notify(new_hits)


if __name__ == "__main__":
    main()
