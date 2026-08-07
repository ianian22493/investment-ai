"""
Daily pick page generator.

讀 data/analysis.json + candidate_stocks.json + market_data.json，
判斷今日是 pick 還是 watch 模式，呼叫 pick_explainer agent 生 deep
analysis JSON，最後把 JSON inject 進對應的 template (template.html
或 template-watch.html)，寫成 picks/YYYY-MM-DD.html。

每天 cron 跑一次，盤後 (Phase 4 之後)。前盤跑會 skip — 因為 Phase 4
沒跑 tw_daily_pick，pick 資料不存在。

Usage:
  python picks/generate.py
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Make project root importable
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, "data")
PICKS_DIR = HERE
TZ = ZoneInfo("Asia/Taipei")


def _load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _is_pick_day(analysis: dict) -> bool:
    """有出手 = tw_daily_pick.pick.code 存在且不是 placeholder。"""
    pick = (analysis.get("agents", {}).get("tw_daily_pick") or {}).get("pick", {}) or {}
    code = pick.get("code")
    return bool(code) and code not in ("—", "NONE", "", None)


def _load_tw_holidays() -> set[str]:
    """讀 data/tw_holidays.json，回傳 YYYY-MM-DD 字串 set。"""
    path = os.path.join(DATA_DIR, "tw_holidays.json")
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        out = set()
        for year_hols in d.get("holidays", {}).values():
            out.update(year_hols)
        return out
    except Exception:
        return set()


def _load_tw_special_trading_days() -> set[str]:
    """補班日 - 週末但要開盤那些天。"""
    path = os.path.join(DATA_DIR, "tw_holidays.json")
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        out = set()
        for year_days in d.get("special_trading_days", {}).values():
            if isinstance(year_days, list):
                out.update(year_days)
        return out
    except Exception:
        return set()


def _is_trading_day(d: datetime) -> bool:
    """週一至週五，且不在假日名單；週末但在補班日也算。"""
    date_str = d.strftime("%Y-%m-%d")
    holidays = _load_tw_holidays()
    special = _load_tw_special_trading_days()
    if date_str in holidays:
        return False
    if date_str in special:
        return True
    return d.weekday() < 5   # Mon-Fri


def _next_trading_day(dt: datetime) -> str:
    """從 cron 執行日，往後找下一個交易日。
    跳過：週末、台股封關日、國定假日。
    含：補班日（週末但要開盤）。
    """
    d = dt + timedelta(days=1)
    while not _is_trading_day(d):
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _scan_existing_picks() -> list[str]:
    """掃描 picks/ 下已存在的 YYYY-MM-DD.html，回傳排序好的日期 list。"""
    dates = []
    for fn in os.listdir(PICKS_DIR):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\.html$", fn)
        if m:
            dates.append(m.group(1))
    return sorted(dates)


def _compute_prev_next(target_date: str, all_dates: list[str]) -> tuple[str | None, str | None]:
    """從已存在 picks 中找出 target 的前/後一筆。"""
    sorted_dates = [d for d in sorted(set(all_dates + [target_date])) if d != target_date]
    prev = next_d = None
    for d in sorted(all_dates):
        if d < target_date:
            prev = d  # keep updating; latest before target wins
        elif d > target_date and next_d is None:
            next_d = d
            break
    return prev, next_d


def _format_budget(cf: dict, pf: dict) -> str:
    """trading 預算用 % 也帶絕對金額 (淨資產 × trading_pct)。"""
    trading_pct = cf.get("budget", {}).get("trading", 0)
    # Rough portfolio total for absolute display
    cash = pf.get("cash_savings_twd", 0) if pf else 0
    return f"{trading_pct*100:.0f}%" + (f" · ~NT$ {int(cash * trading_pct):,}" if cash else "")


def _load_watchlist_zones() -> list:
    """讀 data/alerts.json（哨兵輸出），回傳「全部」rule=zone 的寶藏股（含未到價），
    給 pick / watch 頁「⭐ 寶藏觀察名單·入場區」面板：永遠列出全名單＋現價＋
    起手/加碼價＋狀態，到起手/加碼區者明顯標示。長波埋伏，與波段 pick 是兩套
    獨立紀律。依狀態排序（到價的排前面）。"""
    alerts = _load_json(os.path.join(DATA_DIR, "alerts.json"))
    order = {"加碼區": 0, "起手區": 1, "接近": 2, "還遠": 3}
    out = []
    for a in (alerts.get("results") or []):
        if a.get("rule") == "zone":
            out.append({
                "code": str(a.get("symbol", "")).split(".")[0],
                "name": a.get("name", ""),
                "px": a.get("px"),
                "start": a.get("start"),
                "add": a.get("add"),
                "status": a.get("status", ""),
                "hit": bool(a.get("hit")),
                "note": a.get("note", ""),
                "vol": a.get("vol"),
                "thin": bool(a.get("thin")),
                "days_in_zone": a.get("days_in_zone", 0),
            })
    out.sort(key=lambda x: order.get(x.get("status"), 9))
    return out


def build_pick_data(analysis: dict, market_data: dict, candidates: list, explainer: dict, date_str: str) -> dict:
    """組裝 PICK day 完整 data dict（給 template.html 用）。"""
    agents = analysis.get("agents", {})
    regime = analysis.get("regime", {})
    cf = analysis.get("capital_flow", {})
    pick_agent = agents.get("tw_daily_pick") or {}
    pick = pick_agent.get("pick") or {}

    portfolio = _load_json(os.path.join(DATA_DIR, "portfolio.json"))
    pf = portfolio.get("personal_finance", {})

    # Merge explainer + raw pick + meta
    return {
        # Meta
        "date":            date_str,
        "regime":          regime.get("market_regime", "—"),
        "risk_level":      regime.get("risk_level", "—"),
        "trading_budget":  _format_budget(cf, pf),
        # Stock identity
        "code":            pick.get("code", "—"),
        "name":            pick.get("name", "—"),
        "verdict":         pick_agent.get("verdict", "進攻"),
        "confidence":      pick_agent.get("confidence", 0),
        # Key numbers
        "entry_zone":      pick.get("entry_zone", "—"),
        "stop_loss":       pick.get("stop_loss", "—"),
        "target":          pick.get("target", "—"),
        "hold_days":       pick.get("hold_days", "—"),
        "ref_close":       pick.get("ref_close"),
        "risk_reward":     pick.get("risk_reward", "—"),
        # Explainer-generated (deep analysis)
        "context_paragraph": explainer.get("context_paragraph", ""),
        "why_this_stock":    explainer.get("why_this_stock", ""),
        "entry_rationale":   explainer.get("entry_rationale", ""),
        "stop_rationale":    explainer.get("stop_rationale", ""),
        "target_rationale":  explainer.get("target_rationale", ""),
        "hold_rationale":    explainer.get("hold_rationale", ""),
        "risk_scenarios":    explainer.get("risk_scenarios", []),
        "execution_checklist": explainer.get("execution_checklist", []),
        # Devil's Advocate (prefer explainer's rewrite, fallback to raw DA)
        "devils_advocate":   explainer.get("devils_advocate") or {
            "verdict":  (agents.get("devils_advocate") or {}).get("verdict", "—"),
            "summary":  (agents.get("devils_advocate") or {}).get("summary", ""),
            "counter_arguments": (agents.get("devils_advocate") or {}).get("counter_argument", [])
                                or (agents.get("devils_advocate") or {}).get("counter_arguments", []),
        },
        # Holdings correlation (Task C) — 產業重疊警示
        "correlation":       pick_agent.get("correlation") or {},
        # Swing book (2026-07-17 波段改版) — 在倉部位快照
        "open_positions":    pick_agent.get("open_positions") or [],
        "max_positions":     pick_agent.get("max_positions", 3),
        # 寶藏雷達收斂徽章（連動 #2）
        "treasure_watch":    pick_agent.get("treasure_watch"),
        # 恐慌日 SOP（連動 #3）
        "panic_sop":         analysis.get("panic_sop"),
        # 部位大小建議（2026-07-24 風險基準法）
        "position_sizing":   pick_agent.get("position_sizing"),
        # 寶藏觀察名單·入場區（連動 #4：全名單＋現價＋起手/加碼＋狀態 → pick 頁）
        "watchlist_zones":   _load_watchlist_zones(),
    }


def build_watch_data(analysis: dict, market_data: dict, candidates: list, explainer: dict, date_str: str) -> dict:
    """組裝 WATCH day data dict（給 template-watch.html 用）。"""
    agents = analysis.get("agents", {})
    regime = analysis.get("regime", {})
    cf = analysis.get("capital_flow", {})
    pick_agent = agents.get("tw_daily_pick") or {}

    return {
        "date":             date_str,
        "regime":           regime.get("market_regime", "—"),
        "risk_level":       regime.get("risk_level", "—"),
        "verdict":          pick_agent.get("verdict", "空手觀望"),
        "context_paragraph": explainer.get("context_paragraph", ""),
        "why_watching":     explainer.get("why_watching", ""),
        "reactivation_triggers": explainer.get("reactivation_triggers", []),
        "scanner_top1":     explainer.get("scanner_top1") or None,
        "micro_pick":       pick_agent.get("micro_pick"),
        # Swing book (2026-07-17 波段改版) — 在倉部位快照
        "open_positions":   pick_agent.get("open_positions") or [],
        "max_positions":    pick_agent.get("max_positions", 3),
        # 恐慌日 SOP（連動 #3）
        "panic_sop":        analysis.get("panic_sop"),
        # 寶藏觀察名單·入場區（連動 #4：全名單＋現價＋起手/加碼＋狀態 → 觀望日頁）
        "watchlist_zones":  _load_watchlist_zones(),
    }


def _strip_md(v):
    """遞迴清除 LLM 偶發輸出的 markdown 記號（**粗體**、`code`）。

    Template 用純文字渲染，這些記號會原樣顯示在頁面上（2026-07-08
    前有 4 頁觀望日中獎）。只清 ** 與反引號，單一 * 可能是合法字元不動。
    """
    if isinstance(v, str):
        return v.replace("**", "").replace("`", "")
    if isinstance(v, list):
        return [_strip_md(x) for x in v]
    if isinstance(v, dict):
        return {k: _strip_md(x) for k, x in v.items()}
    return v


PICK_DATA_TAG = '<script id="pick-data" type="application/json">'


def render(template_path: str, data: dict) -> str:
    """把 data 注入 template 的 {{DATA_JSON}} placeholder。

    只注入 <script id="pick-data"> 裡那一個 token——過去用全域
    replace，把整包 JSON 也塞進了兩段註解（檔案肥 3 倍），還害
    backfill 的 count=1 regex 改到註解、真資料的 next_date 留 null。
    """
    with open(template_path, encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(_strip_md(data), ensure_ascii=False)
    # Embed safely — escape </script> if it appears in any string (XSS-ish)
    payload = payload.replace("</script>", "<\\/script>")
    marker = PICK_DATA_TAG + "{{DATA_JSON}}</script>"
    if marker not in html:
        raise RuntimeError(f"pick-data script tag with token not found in {template_path}")
    return html.replace(marker, PICK_DATA_TAG + payload + "</script>", 1)


def build_picks_manifest() -> list[dict]:
    """掃描 picks/*.html 從每個檔案抽出注入的 pick-data JSON，
    建出 calendar 用的 manifest。順便從 alpha.db 補 win/loss 結果。
    """
    import sqlite3
    manifest = []
    for fn in sorted(os.listdir(PICKS_DIR)):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\.html$", fn)
        if not m:
            continue
        page_date = m.group(1)
        path = os.path.join(PICKS_DIR, fn)
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            data_match = re.search(
                r'<script id="pick-data" type="application/json">(.+?)</script>',
                content, re.S,
            )
            if not data_match:
                continue
            d = json.loads(data_match.group(1))
            has_pick = d.get("code") and d.get("code") not in ("—", "NONE", "", None)
            manifest.append({
                "date":       page_date,
                "type":       "pick" if has_pick else "watch",
                "code":       d.get("code") if has_pick else None,
                "name":       d.get("name") if has_pick else None,
                "verdict":    d.get("verdict", "—"),
                "entry_zone": d.get("entry_zone") if has_pick else None,
                "stop_loss":  d.get("stop_loss") if has_pick else None,
                "target":     d.get("target") if has_pick else None,
                "result":     None,   # filled below from alpha.db
                "pnl":        None,
            })
        except Exception as e:
            print(f"  [generate.py] skip manifest scan {fn}: {e}")

    # Enrich with outcomes from alpha.db
    db_path = os.path.join(DATA_DIR, "alpha.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT date, target_date, stock_code, return_pct, success, resolved,
                          benchmark_return_pct, alpha_pct, exit_reason
                   FROM picks"""
            ).fetchall()
            conn.close()
            # Join key = 頁面日期。DB 的 date 是 cron 決策日、target_date 是
            # 可下單交易日（= 頁面檔名）。7/6 檔名改版後兩者錯開一個交易日，
            # 舊的 date join 從那天起全部 miss（月曆永遠 pending）——
            # 優先用 target_date，老 rows（target_date 為空）退回 date。
            by_date = {}
            for r in rows:
                by_date[r["target_date"] or r["date"]] = r
            for entry in manifest:
                if entry["type"] != "pick":
                    continue
                r = by_date.get(entry["date"])
                if not r:
                    continue
                if r["resolved"] == 1 and r["exit_reason"] == "not_filled":
                    # v5：進場窗未觸價 — 不算勝負，月曆標「未成交」
                    entry["result"] = "not_filled"
                elif r["resolved"] == 1 and r["return_pct"] is not None:
                    entry["result"] = "win" if r["return_pct"] > 0 else "loss"
                    sign = "+" if r["return_pct"] > 0 else ""
                    entry["pnl"] = f"{sign}{r['return_pct']:.1f}%"
                    # Numeric fields for baseline UI
                    entry["return_pct"] = r["return_pct"]
                    entry["benchmark_return_pct"] = r["benchmark_return_pct"]
                    entry["alpha_pct"] = r["alpha_pct"]
                else:
                    entry["result"] = "pending"
        except Exception as e:
            print(f"  [generate.py] alpha.db enrich failed: {e}")
    return manifest


def write_stats_json():
    """輸出所有 alpha.db.picks 已結案紀錄到 picks/stats.json，供
    calendar 頁的勝率/alpha 統計使用（獨立於檔案系統，含歷史）。"""
    import sqlite3
    db_path = os.path.join(DATA_DIR, "alpha.db")
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, stock_code, stock_name, verdict, return_pct, success,
               benchmark_return_pct, alpha_pct, resolved
        FROM picks
        WHERE stock_code != 'NONE' AND resolved = 1
        ORDER BY date ASC
    """).fetchall()
    conn.close()
    settled = [dict(r) for r in rows]
    out = {
        "_comment": "全部已結案 picks（含歷史），供 calendar 頁 stats 面板使用",
        "_updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "_count": len(settled),
        "picks": settled,
    }
    path = os.path.join(PICKS_DIR, "stats.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[generate.py] [OK] wrote picks/stats.json ({len(settled)} settled picks)")


def write_latest_pointer():
    """把最近一篇日記頁的日期寫到 picks/latest.json，讓 hub
    不用依賴 analysis.json 的 generated_at（盤前會清空 tw_daily_pick）。
    含 watch day — 觀望日也有頁面（reflection + 為什麼不出手），
    使用者該看得到。如果存在 pick day 比較新就用 pick，否則 watch。"""
    manifest = build_picks_manifest()
    if not manifest:
        return
    # 取所有 entries（pick + watch）中最新日期
    latest = max(manifest, key=lambda m: m["date"])
    out = {
        "date":    latest["date"],
        "type":    latest.get("type"),   # 'pick' or 'watch'
        "code":    latest.get("code"),
        "name":    latest.get("name"),
        "verdict": latest.get("verdict"),
        "result":  latest.get("result"),
    }
    path = os.path.join(PICKS_DIR, "latest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[generate.py] [OK] wrote picks/latest.json → {latest['date']} "
          f"{latest.get('type')} {latest.get('code') or '(watch)'}")


def render_calendar_index():
    """把當前 picks/*.html 整理成 manifest，注入 picks/index.html
    的 <script id="picks-data"> 區塊。每次 cron 都會跑，所以月曆永遠是
    最新狀態（不會殘留 SAMPLE 假資料）。"""
    manifest = build_picks_manifest()
    index_path = os.path.join(PICKS_DIR, "index.html")
    if not os.path.exists(index_path):
        return
    with open(index_path, encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(manifest, ensure_ascii=False)
    payload = payload.replace("</script>", "<\\/script>")
    # Match the script tag content (both unreplaced {{PICKS_JSON}} and
    # previously-injected JSON). Replace text node only.
    new_html = re.sub(
        r'(<script id="picks-data" type="application/json">)(.*?)(</script>)',
        lambda m: m.group(1) + payload + m.group(3),
        html, count=1, flags=re.S,
    )
    if new_html != html:
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(new_html)
        print(f"[generate.py] [OK] refreshed calendar manifest ({len(manifest)} entries)")
    else:
        print(f"[generate.py] [WARN] calendar manifest unchanged (script tag not found?)")


def main():
    analysis = _load_json(os.path.join(DATA_DIR, "analysis.json"))
    if not analysis:
        print("[generate.py] data/analysis.json missing — skipping")
        return

    candidates_data = _load_json(os.path.join(DATA_DIR, "candidate_stocks.json"))
    candidates = candidates_data.get("candidates", []) if candidates_data else []
    market_data = _load_json(os.path.join(DATA_DIR, "market_data.json"))

    # Pick 檔名 = **隔天交易日**（不是 cron 執行日）。
    # Sun 16:30 → 檔名是週一日期；Mon 16:30 → 檔名是週二日期。
    # 這樣使用者看到的檔名就是「可以下單的那天」。
    gen_at = analysis.get("generated_at", "")
    if gen_at:
        run_dt = datetime.fromisoformat(gen_at)
    else:
        run_dt = datetime.now(TZ)
    date_str = _next_trading_day(run_dt)
    run_date_str = run_dt.strftime("%Y-%m-%d")
    print(f"[generate.py] cron run at {run_date_str} → pick 檔名 = {date_str} (下一交易日)")

    # Only produce fresh pick pages on POST-MARKET runs (hour >= 13).
    # Pre-market runs (08:30) now preserve yesterday's tw_daily_pick in
    # run_agents.py so latest.json stays populated, but that data is
    # STALE — we must NOT generate a new-day page from it, or the
    # "07-07.html" file will contain yesterday's decision with tomorrow's
    # date. Still refresh calendar + latest pointer for hub freshness.
    if run_dt.hour < 13:
        print(f"[generate.py] pre-market run ({run_dt.hour:02d}:xx) — skip page generation, refresh index only")
        render_calendar_index()
        write_latest_pointer()
        write_stats_json()
        return

    # Post-market but Phase 4 wasn't triggered (e.g. Phase 4 skipped for
    # some other reason) — no fresh pick data, refresh index and bail.
    if not analysis.get("agents", {}).get("tw_daily_pick"):
        print(f"[generate.py] {date_str} 沒有 tw_daily_pick — skip page generation")
        render_calendar_index()
        write_latest_pointer()
        write_stats_json()
        return

    pick_day = _is_pick_day(analysis)
    mode = "PICK" if pick_day else "WATCH"
    print(f"[generate.py] {date_str} · {mode} mode")

    # Generate explainer JSON via LLM
    print(f"[generate.py] calling pick_explainer agent...")
    from agents import pick_explainer
    explainer = pick_explainer.run(analysis, market_data, candidates)

    # Build full data dict + render template
    if pick_day:
        data = build_pick_data(analysis, market_data, candidates, explainer, date_str)
        template_path = os.path.join(PICKS_DIR, "template.html")
    else:
        data = build_watch_data(analysis, market_data, candidates, explainer, date_str)
        template_path = os.path.join(PICKS_DIR, "template-watch.html")

    # Wire prev/next from existing pages
    existing = _scan_existing_picks()
    prev, nxt = _compute_prev_next(date_str, existing)
    data["prev_date"] = prev
    data["next_date"] = nxt

    html = render(template_path, data)
    out_path = os.path.join(PICKS_DIR, f"{date_str}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(out_path) / 1024
    print(f"[generate.py] ✓ wrote {out_path} ({size_kb:.1f} KB)")

    # Backfill prev/next on yesterday's page (only the next_date link)
    # so navigation works both ways without regenerating yesterday entirely
    if prev:
        prev_path = os.path.join(PICKS_DIR, f"{prev}.html")
        if os.path.exists(prev_path):
            with open(prev_path, encoding="utf-8") as f:
                prev_html = f.read()
            # Update next_date inside the #pick-data script only — the file
            # may contain other "next_date" strings (template comments in
            # pages generated before the render() fix), and a bare count=1
            # sub used to hit those instead of the real data.
            tag_idx = prev_html.find(PICK_DATA_TAG)
            head, tail = (prev_html[:tag_idx], prev_html[tag_idx:]) if tag_idx != -1 else ("", prev_html)
            new_prev_html = head + re.sub(
                r'"next_date"\s*:\s*(?:"[^"]*"|null)',
                f'"next_date": "{date_str}"',
                tail, count=1
            )
            if new_prev_html != prev_html:
                with open(prev_path, "w", encoding="utf-8") as f:
                    f.write(new_prev_html)
                print(f"[generate.py] ✓ backfilled next_date in {prev}.html")

    # Refresh calendar index — scan all existing picks/*.html, build a
    # manifest, inject into picks/index.html. Replaces any stale SAMPLE
    # data and ensures every dot on the calendar links to a real file.
    render_calendar_index()
    write_latest_pointer()
    write_stats_json()


if __name__ == "__main__":
    main()
