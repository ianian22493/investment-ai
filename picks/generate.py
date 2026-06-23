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
from datetime import datetime
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
    }


def render(template_path: str, data: dict) -> str:
    """把 data 注入 template 的 {{DATA_JSON}} placeholder。"""
    with open(template_path, encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(data, ensure_ascii=False)
    # Embed safely — escape </script> if it appears in any string (XSS-ish)
    payload = payload.replace("</script>", "<\\/script>")
    return html.replace("{{DATA_JSON}}", payload)


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
                "SELECT date, stock_code, return_pct, success, resolved FROM picks"
            ).fetchall()
            by_date = {r["date"]: r for r in rows}
            conn.close()
            for entry in manifest:
                if entry["type"] != "pick":
                    continue
                r = by_date.get(entry["date"])
                if not r:
                    continue
                if r["resolved"] == 1 and r["return_pct"] is not None:
                    entry["result"] = "win" if r["return_pct"] > 0 else "loss"
                    sign = "+" if r["return_pct"] > 0 else ""
                    entry["pnl"] = f"{sign}{r['return_pct']:.1f}%"
                else:
                    entry["result"] = "pending"
        except Exception as e:
            print(f"  [generate.py] alpha.db enrich failed: {e}")
    return manifest


def write_latest_pointer():
    """把最近一篇 pick 的日期寫到 picks/latest.json，讓 hub
    不用依賴 analysis.json 的 generated_at（盤前會清空 tw_daily_pick）。"""
    manifest = build_picks_manifest()
    picks_only = [m for m in manifest if m.get("type") == "pick"]
    if not picks_only:
        return
    latest = max(picks_only, key=lambda m: m["date"])
    out = {
        "date":    latest["date"],
        "code":    latest.get("code"),
        "name":    latest.get("name"),
        "verdict": latest.get("verdict"),
        "result":  latest.get("result"),
    }
    path = os.path.join(PICKS_DIR, "latest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[generate.py] [OK] wrote picks/latest.json → {latest['date']} {latest.get('code')}")


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

    # Detect date — prefer analysis.generated_at, fallback to today (Asia/Taipei)
    gen_at = analysis.get("generated_at", "")
    if gen_at:
        date_str = gen_at[:10]
    else:
        date_str = datetime.now(TZ).strftime("%Y-%m-%d")

    # Only generate post-market — pre-market run has no tw_daily_pick.
    # But still refresh calendar + latest pointer so hub always finds the
    # most recent existing pick page.
    if not analysis.get("agents", {}).get("tw_daily_pick"):
        print(f"[generate.py] {date_str} 沒有 tw_daily_pick (盤前 run?) — skip page generation")
        render_calendar_index()
        write_latest_pointer()
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
            # Find and update the prev_date / next_date in injected JSON
            new_prev_html = re.sub(
                r'"next_date"\s*:\s*(?:"[^"]*"|null)',
                f'"next_date": "{date_str}"',
                prev_html, count=1
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


if __name__ == "__main__":
    main()
