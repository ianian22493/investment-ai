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
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

# Windows 主控台預設 cp950，印不出 ≤/✓/🔥 等字元會 UnicodeEncodeError；
# CI(Linux) 為 utf-8 不受影響。reconfigure 讓本機執行也不因 print 崩潰
# （alerts.json 寫檔本就指定 utf-8，資料一向正確）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "data" / "watchlist.json"
ALERTS = ROOT / "data" / "alerts.json"
TW_TZ = timezone(timedelta(hours=8))

# 寶藏接月營收：純本地讀 history 快照算營收動能（論述追蹤器）。抓不到就降級不掛。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from rev_momentum import rev_momentum
except Exception:  # noqa: BLE001
    rev_momentum = None
try:
    from chip_momentum import chip_momentum  # 大戶籌碼動能（聰明錢·台股版，讀TDCC快取）
except Exception:  # noqa: BLE001
    chip_momentum = None


def _alt_symbol(symbol):
    """.TW <-> .TWO 互換（上市/上櫃後綴猜錯時的 fallback）。"""
    if symbol.endswith(".TWO"):
        return symbol[:-4] + ".TW"
    if symbol.endswith(".TW"):
        return symbol[:-3] + ".TWO"
    return None


def fetch_hist(symbol, period="1y"):
    """抓歷史。#1 資料 sanity：給定後綴抓不到就自動試另一個(.TW<->.TWO)，
    回傳 (closes, volumes, used_symbol)；全失敗回 (None, None, None)。
    避免「後綴猜錯→靜默消失」——主流程會把 fetch 失敗的檔列出來。"""
    for sym in (symbol, _alt_symbol(symbol)):
        if not sym:
            continue
        try:
            df = yf.Ticker(sym).history(period=period, auto_adjust=False)
            closes = df["Close"].dropna()
            if len(closes):
                vols = df["Volume"].reindex(closes.index)
                return closes, vols, sym
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] {sym}: {e}")
    return None, None, None


def last_closes(symbol, period="1y"):
    closes, _, _ = fetch_hist(symbol, period)
    return closes


def check_stock(entry, prev_zone_since=None):
    closes, vols, _ = fetch_hist(entry["symbol"])
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
    elif rule == "zone":
        # 寶藏股入場區：start=起手上緣、add=加碼上緣（更深）。每日 pick 頁永遠列出
        # 全部名單＋現價＋起手/加碼價，到區間者明顯標示。zstatus 供前端分級渲染。
        start = entry.get("start")
        add = entry.get("add")
        if add is not None and px <= add:
            hit, zstatus, detail = True, "加碼區", f"現價 {px:.2f} ≤ 加碼區 {add} 🔥深回檔"
        elif start is not None and px <= start:
            hit, zstatus, detail = True, "起手區", f"現價 {px:.2f} ≤ 起手區 {start} ✓ 進場區"
        elif start is not None and px <= start * 1.10:
            hit, zstatus, detail = False, "接近", f"現價 {px:.2f}（距起手 {start} 之上 {(px/start-1)*100:.0f}%）"
        else:
            hit, zstatus, detail = False, "還遠", f"現價 {px:.2f}（等 ≤{start} 進起手區）"
    key = f"{entry['symbol']}|{rule}|{entry.get('value', '')}"
    out = {"key": key, "symbol": entry["symbol"], "name": entry["name"],
           "rule": rule, "hit": hit, "detail": detail, "note": entry["note"]}
    if rule == "zone":
        # #5 流動性守門：近20日均量(張)，<30張標薄量(死因之一)
        try:
            avgvol = int(vols.tail(20).mean() / 1000) if vols is not None else None
        except Exception:  # noqa: BLE001
            avgvol = None
        thin = avgvol is not None and avgvol < 30
        # #4 在區間幾天：hit(起手/加碼) 才計；沿用上次的 zone_since，離開就清空
        today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
        zsince = (prev_zone_since or today) if hit else None
        days = 0
        if zsince:
            try:
                days = (datetime.strptime(today, "%Y-%m-%d").date()
                        - datetime.strptime(zsince, "%Y-%m-%d").date()).days + 1
            except ValueError:
                days = 1
        out.update({"px": round(px, 2), "start": start, "add": add, "status": zstatus,
                    "vol": avgvol, "thin": thin, "zone_since": zsince, "days_in_zone": days,
                    "priority": entry.get("priority"), "tier": entry.get("tier")})
        if thin:
            out["detail"] += f"  💧薄量~{avgvol}張(小量掛限價)"
    return out


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


def check_positions(cfg):
    """部位規格哨兵：TW 權重用 portfolio.json 的 value 欄（時效由 staleness 檢查把關），
    US 權重用 shares × dashboard data.json 現價（抓不到退回成本價）。"""
    rules = cfg.get("position_rules", [])
    if not rules:
        return []
    try:
        pf = json.loads((ROOT / "data" / "portfolio.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] portfolio.json read failed: {e}")
        return []
    out = []

    # 持倉檔時效：整套規格檢查都站在這個檔上，過期先警告
    upd = pf.get("_updated")
    if upd:
        try:
            age = (datetime.now(TW_TZ).date()
                   - datetime.strptime(upd, "%Y-%m-%d").date()).days
            out.append({"key": "pos|staleness", "symbol": "portfolio.json",
                        "name": "持倉檔時效", "rule": "stale_portfolio",
                        "hit": age > 30,
                        "detail": f"_updated={upd}（{age} 天前）",
                        "note": "超過 30 天未更新＝規格檢查可能基於幽靈持倉；下單後回報研究員更新"})
        except ValueError:
            pass

    us_px = {}
    try:
        req = urllib.request.Request(
            "https://ianian22493.github.io/investment-dashboard/data.json",
            headers={"User-Agent": "watcher"})
        with urllib.request.urlopen(req, timeout=30) as r:
            us_px = {k: (v or {}).get("price") for k, v in json.load(r).get("us", {}).items()}
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] dashboard data.json failed (US weights fall back to cost): {e}")

    tw = {s["code"]: float(s.get("value") or 0) for s in pf.get("tw_stocks", [])}
    tw_names = {s["code"]: s.get("name", "") for s in pf.get("tw_stocks", [])}
    us = {}
    for s in pf.get("us_stocks", []):
        px = us_px.get(s["ticker"]) or s.get("avg_cost_usd") or 0
        us[s["ticker"]] = float(s.get("shares") or 0) * float(px)
    books = {"tw": (tw, sum(tw.values())), "us": (us, sum(us.values()))}

    for rule in rules:
        book, total = books.get(rule["market"], ({}, 0))
        if not total:
            continue
        if rule["type"] == "single_max_pct":
            exempt = tuple(rule.get("exempt_prefixes", ["00"]))  # ETF＝核心層，不受個股上限
            for code, val in sorted(book.items(), key=lambda x: -x[1]):
                if code.startswith(exempt):
                    continue
                pct = val / total * 100
                if pct > rule["max"]:
                    out.append({
                        "key": f"pos|{rule['market']}|{code}",
                        "symbol": code,
                        "name": f"{code} {tw_names.get(code, '')}".strip(),
                        "rule": "position_over_limit", "hit": True,
                        "detail": f"佔 {rule['market'].upper()} 部位 {pct:.1f}%＞上限 {rule['max']}%",
                        "note": rule["note"]})
        elif rule["type"] == "group_max_pct":
            val = sum(book.get(c, 0) for c in rule["symbols"])
            pct = val / total * 100
            out.append({
                "key": f"pos|{rule['market']}|{rule['name']}",
                "symbol": "+".join(rule["symbols"]), "name": rule["name"],
                "rule": "factor_over_limit", "hit": pct > rule["max"],
                "detail": f"合計佔 {rule['market'].upper()} 部位 {pct:.1f}%（上限 {rule['max']}%）",
                "note": rule["note"]})
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

    # 先讀上次 alerts：prev_hits(新觸發判定) + zone_since(在區間幾天延續) 對照表
    prev_hits, prev_zone_since = set(), {}
    if ALERTS.exists():
        try:
            prev = json.loads(ALERTS.read_text(encoding="utf-8"))
            for a in prev.get("results", []):
                if a.get("hit"):
                    prev_hits.add(a["key"])
                if a.get("rule") == "zone" and a.get("zone_since"):
                    prev_zone_since[a.get("symbol")] = a["zone_since"]
        except Exception:  # noqa: BLE001
            pass

    results = check_panic(cfg["panic"])
    failed = []  # #1 fetch 失敗的檔（後綴猜錯/下市）— 浮現而非靜默消失
    for entry in cfg["stocks"]:
        r = check_stock(entry, prev_zone_since.get(entry.get("symbol")))
        if r:
            results.append(r)
        elif entry.get("rule") == "zone":
            failed.append(f"{entry.get('symbol')} {entry.get('name', '')}".strip())
    results.extend(check_positions(cfg))

    # 寶藏接月營收：對每個 zone 檔附掛「月營收年增趨勢」＝論述兌現/亮紅的真訊號
    # （收稅口/雙擊靠營收連續加速，不是價格）。純本地、零網路呼叫。
    if rev_momentum:
        zone_codes = [r["symbol"].split(".")[0] for r in results if r.get("rule") == "zone"]
        if zone_codes:
            try:
                rev = rev_momentum(zone_codes)
                zone_rows = [r for r in results if r.get("rule") == "zone"]
                for r in zone_rows:
                    code = r["symbol"].split(".")[0]
                    if code in rev:
                        r["rev"] = rev[code]
                # ── warn escalation ─────────────────────────────────────
                # 營收轉負＝論述正在破，比「價格到區間」更該喊——升級成 alert 事件
                # （進 hits/new_hits → 推播＋提醒去審），而不是只把面板變紅。獨立 key
                # 讓它只在「轉負當下」推一次，不每天洗版。
                for r in zone_rows:
                    rv = r.get("rev")
                    if rv and rv.get("signal") == "warn":
                        results.append({
                            "key": f"{r['symbol']}|rev_warn",
                            "symbol": r["symbol"], "name": r["name"],
                            "rule": "rev_warn", "hit": True,
                            "detail": f"🔴 論述亮紅燈：{rv.get('brief', '月營收轉負')}",
                            "note": "月營收轉負＝收稅口/雙擊論述正在破。去審：是一次性還是趨勢？"
                                    "是→從入場區撤出、記分卡標壞；否→續觀察。",
                        })
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] rev_momentum attach failed: {e}")

    # 大戶籌碼動能（聰明錢·台股版）：讀 TDCC 集保快取（週檢 refresh）附掛到 zone 檔。
    # 大戶(≥400張)% 上升＝主力/隱形大戶累積＝起漲前訊號，補寶藏系統「進場計時」。零網路。
    if chip_momentum:
        zcodes = [r["symbol"].split(".")[0] for r in results if r.get("rule") == "zone"]
        if zcodes:
            try:
                import chip_momentum as _cm
                _cm.refresh_if_stale()   # TDCC 週更：快取過期自動抓（靠GH cron自我更新）
                chip = chip_momentum(zcodes)
                for r in results:
                    if r.get("rule") == "zone":
                        code = r["symbol"].split(".")[0]
                        if code in chip:
                            r["chip"] = chip[code]
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] chip_momentum attach failed: {e}")

    # 事件行事曆 heads-up（財報/法說事前提醒）：rev_momentum 是事後抓到，這補事前。
    upcoming = []
    today_d = datetime.now(TW_TZ).date()
    for ev in cfg.get("events", []):
        try:
            d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        except Exception:  # noqa: BLE001
            continue
        days = (d - today_d).days
        if -1 <= days <= 14:  # 未來兩週內（保留昨天，交易日落差容錯）
            upcoming.append({**ev, "days_away": days})
            if 0 <= days <= 3:  # 迫近 → 也推播一次（獨立 key 不洗版）
                when = "今天" if days == 0 else f"還{days}天"
                results.append({
                    "key": f"{ev.get('symbol', '')}|event|{ev['date']}",
                    "symbol": ev.get("symbol", ""), "name": ev.get("name", ""),
                    "rule": "event", "hit": True,
                    "detail": f"📅 {ev['date']}（{when}）{ev.get('type', '')}",
                    "note": ev.get("note", ""),
                })
    upcoming.sort(key=lambda e: e["days_away"])

    hits = [r for r in results if r["hit"]]
    new_hits = [r for r in hits if r["key"] not in prev_hits]

    ALERTS.write_text(json.dumps({
        "checked_at": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M %z"),
        "hit_count": len(hits),
        "new_hit_count": len(new_hits),
        "failed": failed,
        "upcoming_events": upcoming,
        "results": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"checked {len(results)} rules: {len(hits)} hit ({len(new_hits)} new)")
    if failed:
        print(f"  [FETCH 失敗 ×{len(failed)}] {', '.join(failed)} — 檢查代號/後綴/是否下市")
    for a in hits:
        marker = "NEW" if a in new_hits else "ongoing"
        print(f"  [{marker}] {a['name']} {a['detail']} -> {a['note']}")
    if new_hits:
        discord_notify(new_hits)


if __name__ == "__main__":
    main()
