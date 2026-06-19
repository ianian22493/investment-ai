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

    # Only generate post-market — pre-market run has no tw_daily_pick
    if not analysis.get("agents", {}).get("tw_daily_pick"):
        print(f"[generate.py] {date_str} 沒有 tw_daily_pick (盤前 run?) — skip page generation")
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


if __name__ == "__main__":
    main()
