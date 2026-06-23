"""
Investment AI Orchestrator — 三層 Desk + Capital Flow 架構
"""

import json, os, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo

RATE_LIMIT_SLEEP = 15

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

from agents import (
    market_overview, news_sentiment,
    tw_short_term, tw_long_term, us_portfolio, fx_fund, asset_allocation,
    devils_advocate,
    trading_master, portfolio_master, wealth_master,
    master_agent, tw_daily_pick,
)
from agents import reflection as reflection_agent
from agents.regime_engine import determine_regime
from agents.capital_flow import compute as compute_capital_flow
from agents.constraint_validator import validate as validate_constraints
from agents.signal_fusion import compute as compute_signal_fusion
import alpha_db
import outcome_tracker
import agent_cache


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save_json(data: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_candidates() -> list:
    path = os.path.join(DATA_DIR, "candidate_stocks.json")
    if not os.path.exists(path):
        return []
    try:
        return load_json(path).get("candidates", [])
    except Exception:
        return []


# Trial-pick gating thresholds — surfaces at this trading budget level,
# regardless of master's 觀望 verdict. Lowered 2026-06-18 from 0.15 → 0.05
# because Capital Flow was locking trading at 5% during pre-payment windows
# and the user was getting zero recommendations for 14+ days even when
# scanner had high-conviction candidates.
MICRO_PICK_MIN_TRADING_BUDGET = 0.05    # was 0.15
MICRO_PICK_MIN_SCANNER_SCORE  = 5       # scanner score on 0-7 scale
MICRO_PICK_SIZE_RATIO         = 0.30    # 30% of trading budget = trial size

def _maybe_attach_micro_pick(outputs: dict, candidates: list, regime: dict, market_data: dict):
    """⚠ MUTATES `outputs`. Attaches `micro_pick` key to outputs['tw_daily_pick']
    when ALL gates pass:
      - main pick is empty/空手
      - trading budget >= MICRO_PICK_MIN_TRADING_BUDGET (5%)
      - top scanner candidate score >= MICRO_PICK_MIN_SCANNER_SCORE (5)
      - regime not in panic set

    The main verdict still says 空手 — this is purely advisory sidecar.
    """
    pick = outputs.get("tw_daily_pick", {})
    main_code = (pick.get("pick") or {}).get("code")
    verdict = pick.get("verdict") or ""
    is_empty = (
        not main_code
        or main_code in ("—", "NONE", "")
        or "觀望" in verdict
        or "空手" in verdict
    )
    if not is_empty:
        return

    cf = outputs.get("capital_flow", {})
    trading_budget = cf.get("budget", {}).get("trading", 0)
    if trading_budget < MICRO_PICK_MIN_TRADING_BUDGET:
        return  # System fully locked (e.g. 0%) — respect that

    if not candidates or candidates[0].get("score", 0) < MICRO_PICK_MIN_SCANNER_SCORE:
        return  # No scanner conviction

    regime_name = regime.get("market_regime", "")
    if regime_name in ("恐慌盤", "空頭賣壓"):
        return  # Skip during outright crash

    top = candidates[0]
    SIG_KEYS = ("breakout_ma20", "above_ma20", "vol_surge", "rsi_zone", "ma_aligned", "five_day_high", "rs_signal")
    signals = [k for k in SIG_KEYS if top.get(k)]
    ref_close = (market_data.get("tw_stocks", {}).get(top["code"], {}) or {}).get("close")
    # Trial size = % of trading budget × portfolio. At 5% trading × 30% ratio = 1.5% total.
    trial_size_pct = round(trading_budget * MICRO_PICK_SIZE_RATIO * 100, 1)

    outputs["tw_daily_pick"]["micro_pick"] = {
        "code":     top["code"],
        "name":     top.get("name", "?"),
        "score":    top.get("score"),
        "signals":  signals,
        "ref_close": ref_close,
        "size_pct_of_total": trial_size_pct,
        "rationale": (
            f"系統整體 verdict 偏保守，但 scanner 對 {top.get('name','?')}({top['code']}) "
            f"給 score={top.get('score')}（信號 {len(signals)} 個）。"
            f"可考慮以總部位 {trial_size_pct}% 試單，"
            f"並用 ref_close × 0.94 當停損、× 1.10 當目標。"
        ),
    }
    print(f"    [micro-pick] {top.get('name','?')}({top['code']}) score={top.get('score')} 試單 {trial_size_pct}%")


def _maybe_log_watch_day(outputs: dict, candidates: list, regime: dict, t0):
    """⚠ WRITES to alpha.db.watch_log. Records the day's scanner top1
    when the system chose to sit out — outcome_tracker later resolves
    the 5-day max/min so reflection can audit missed vs avoided.
    """
    pick = outputs.get("tw_daily_pick", {})
    main_code = (pick.get("pick") or {}).get("code")
    verdict = pick.get("verdict") or ""
    is_watch = (
        not main_code or main_code in ("—", "NONE", "")
        or "觀望" in verdict or "空手" in verdict
    )
    if not is_watch:
        return
    if not candidates:
        return
    top = candidates[0]
    if not top.get("code"):
        return
    reason = pick.get("summary", "") or pick.get("agent_note", "")
    alpha_db.log_watch_day(
        date=t0.strftime("%Y-%m-%d"),
        scanner_top=top,
        regime_name=regime.get("market_regime", ""),
        reason=reason,
    )
    print(f"    [watch_log] 記錄觀望日 · scanner top1: {top.get('name','?')}({top['code']}) score={top.get('score')}")


def run_all():
    t0 = datetime.now(TZ)
    print(f"\n{'='*60}")
    print(f"Investment AI — {t0.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"{'='*60}\n")

    # ── Load data ─────────────────────────────────────────────────────────────
    market_data_path = os.path.join(DATA_DIR, "market_data.json")
    portfolio_path   = os.path.join(DATA_DIR, "portfolio.json")

    if not os.path.exists(market_data_path):
        print("ERROR: market_data.json not found. Run fetch_market_data.py first.")
        sys.exit(1)

    market_data = load_json(market_data_path)
    portfolio   = load_json(portfolio_path)

    pv = market_data.get("portfolio_value", {})
    portfolio_live = {**portfolio}
    if pv.get("tw_stocks_live"):
        portfolio_live["tw_stocks"] = pv["tw_stocks_live"]
    if pv.get("tw_summary_live"):
        portfolio_live["tw_summary"] = pv["tw_summary_live"]

    # ── Learning Layer ────────────────────────────────────────────────────────
    print("  [learning] 結算昨日推薦...")
    outcome_tracker.resolve_pending(market_data)

    perf_stats   = alpha_db.get_performance_stats(30)
    perf_context = alpha_db.format_stats_for_prompt(perf_stats)
    if perf_context:
        market_data["performance_context"] = perf_context
        print(f"  [learning] 注入績效：近{perf_stats['total']}次 勝率{perf_stats['overall_rate']}%")

    latest_reflection = alpha_db.get_latest_reflection()
    if latest_reflection:
        market_data["latest_reflection"] = latest_reflection

    # ── Regime Engine (no LLM) ────────────────────────────────────────────────
    regime = determine_regime(market_data)
    market_data["regime"] = regime
    print(f"  [regime] {regime['regime_summary']}")

    candidates = load_candidates()
    if candidates:
        print(f"  [scanner] {len(candidates)} candidates loaded")

    print(agent_cache.status_summary())
    # Show how many Gemini keys are loaded — confirms key rotation is wired up
    try:
        from agents.base import keys_status
        print(f"  [api keys] {keys_status()}")
    except Exception:
        pass

    outputs = {}

    def _sleep(name: str):
        print(f"    ⏳ sleep {RATE_LIMIT_SLEEP}s...")
        time.sleep(RATE_LIMIT_SLEEP)

    # ── Phase 1: Market context (shared by all desks) ─────────────────────────
    print("\n── Phase 1: Market Context ──")
    for name, fn in [
        ("market_overview", lambda: market_overview.run(market_data, portfolio_live, regime=regime)),
        ("news_sentiment",  lambda: news_sentiment.run(market_data, portfolio_live)),
    ]:
        print(f"  {name}...")
        outputs[name] = fn()
        print(f"    → {outputs[name].get('verdict')} ({outputs[name].get('confidence')})")
        _sleep(name)

    news = outputs.get("news_sentiment", {})

    # ── Phase 2: Specialist agents ────────────────────────────────────────────
    print("\n── Phase 2: Specialist Agents ──")

    # Trading Desk — always fresh
    print("  tw_short_term...")
    outputs["tw_short_term"] = tw_short_term.run(
        market_data, portfolio_live, outputs["market_overview"], news,
        regime=regime, candidates=candidates,
    )
    print(f"    → {outputs['tw_short_term'].get('verdict')} ({outputs['tw_short_term'].get('confidence')})")
    _sleep("tw_short_term")

    # Portfolio Desk — cached up to 3-5 days
    for name, fn in [
        ("tw_long_term", lambda: tw_long_term.run(
            market_data, portfolio_live, outputs["market_overview"], news, regime=regime,
        )),
        ("us_portfolio", lambda: us_portfolio.run(
            market_data, portfolio_live, outputs["market_overview"], news, regime=regime,
        )),
    ]:
        print(f"  {name}...")
        outputs[name], was_cached = agent_cache.get_or_run(
            name, fn, sleep_fn=lambda: _sleep(name)
        )
        if not was_cached:
            print(f"    → {outputs[name].get('verdict')} ({outputs[name].get('confidence')})")

    # Wealth Desk — cached up to 5-7 days
    for name, fn in [
        ("fx_fund", lambda: fx_fund.run(
            market_data, portfolio_live, outputs["market_overview"], news,
        )),
        ("asset_allocation", lambda: asset_allocation.run(
            market_data, portfolio_live, outputs["market_overview"], news, regime=regime,
        )),
    ]:
        print(f"  {name}...")
        outputs[name], was_cached = agent_cache.get_or_run(
            name, fn, sleep_fn=lambda: _sleep(name)
        )
        if not was_cached:
            print(f"    → {outputs[name].get('verdict')} ({outputs[name].get('confidence')})")

    # ── Phase 3: Devil's Advocate ─────────────────────────────────────────────
    print("\n── Phase 3: Devil's Advocate ──")
    print("  devils_advocate...")
    outputs["devils_advocate"] = devils_advocate.run(outputs)
    print(f"    → {outputs['devils_advocate'].get('verdict')}")
    _sleep("devils_advocate")

    # ── Phase 3.5: Signal Fusion (pure Python, no LLM) ───────────────────────
    print("\n── Phase 3.5: Signal Fusion ──")
    outputs["signal_fusion"] = compute_signal_fusion(
        market_data=market_data,
        outputs=outputs,
        regime=regime,
        candidates=candidates,
    )
    sv = outputs["signal_fusion"]
    print(
        f"  → regime={sv['market_regime_score']:+.2f} "
        f"trend={sv['trend_strength']:.2f} "
        f"risk={sv['risk_pressure']:.2f} "
        f"vol={sv['volatility_risk']:.2f} "
        f"conf={sv['confidence_score']:.2f}"
    )

    # ── Phase 4: 盤後專屬（tw_daily_pick + reflection）────────────────────────
    if t0.hour >= 13:
        print("\n── Phase 4: Post-Market ──")

        print("  tw_daily_pick...")
        outputs["tw_daily_pick"] = tw_daily_pick.run(
            market_data, portfolio_live, outputs["market_overview"], news,
            regime=regime, candidates=candidates,
        )
        pick = outputs["tw_daily_pick"]
        print(f"    → {pick.get('verdict')} | {pick.get('pick',{}).get('name','?')}({pick.get('pick',{}).get('code','?')})")
        outcome_tracker.save_today_pick(pick, regime, market_data, candidates=candidates)

        # ── Micro-position trial — additive suggestion when system 空手
        # 觀望 but scanner sees strong candidates. Doesn't override the
        # main pick (which stays 空手); presents an "observation trade"
        # to the user as a sidecar. No extra LLM call. Mutates outputs.
        _maybe_attach_micro_pick(outputs, candidates, regime, market_data)

        # ── Watch-day logging — when system says 觀望 but scanner has a
        # top candidate, record it. outcome_tracker will fill in the
        # 5-day max/min later, so reflection can learn whether the
        # system's caution was warranted (avoided drops) or costly
        # (missed rallies). Writes to alpha.db.watch_log.
        _maybe_log_watch_day(outputs, candidates, regime, t0)

        _sleep("tw_daily_pick")

        print("  reflection...")
        recent_picks = alpha_db.get_recent_picks(30)
        yesterday = next((p for p in recent_picks if p.get("resolved") == 1), None)
        outputs["reflection"] = reflection_agent.run(
            recent_picks=recent_picks,
            performance_stats=perf_stats,
            yesterday_result=yesterday,
        )
        refl = outputs["reflection"]
        print(f"    → {refl.get('verdict')} | {refl.get('summary','')[:60]}...")
        alpha_db.save_reflection(
            date=t0.strftime("%Y-%m-%d"),
            regime=regime.get("market_regime", ""),
            reflection=refl,
            stats=perf_stats,
        )
        _sleep("reflection")
    else:
        print("\n── Phase 4: Skipped (pre-market) ──")

    # ── Phase 5: Desk Masters ─────────────────────────────────────────────────
    print("\n── Phase 5: Desk Masters ──")

    # trading_master always re-runs (depends on today's tw_short_term)
    print("  trading_master...")
    outputs["trading_master"] = trading_master.run(
        tw_short_term=outputs.get("tw_short_term", {}),
        tw_daily_pick=outputs.get("tw_daily_pick"),
        regime=regime,
        capital_flow=None,
    )
    print(f"    → {outputs['trading_master'].get('verdict')}")
    _sleep("trading_master")

    # portfolio_master: cache only if both inputs were cached
    print("  portfolio_master...")
    outputs["portfolio_master"], pm_cached = agent_cache.get_or_run(
        "portfolio_master",
        lambda: portfolio_master.run(
            tw_long_term=outputs.get("tw_long_term", {}),
            us_portfolio=outputs.get("us_portfolio", {}),
            capital_flow=None,
        ),
        sleep_fn=lambda: _sleep("portfolio_master"),
    )
    if not pm_cached:
        print(f"    → {outputs['portfolio_master'].get('verdict')}")

    # wealth_master: cache only if both inputs were cached
    print("  wealth_master...")
    outputs["wealth_master"], wm_cached = agent_cache.get_or_run(
        "wealth_master",
        lambda: wealth_master.run(
            fx_fund=outputs.get("fx_fund", {}),
            asset_allocation=outputs.get("asset_allocation", {}),
            portfolio=portfolio,
        ),
        sleep_fn=lambda: _sleep("wealth_master"),
    )
    if not wm_cached:
        print(f"    → {outputs['wealth_master'].get('verdict')} | risk_level={outputs['wealth_master'].get('risk_level','?')}")

    # ── Phase 6: Capital Flow Engine (no LLM) ─────────────────────────────────
    print("\n── Phase 6: Capital Flow Engine (rules) ──")
    investment_style = portfolio.get("personal_finance", {}).get("investment_style", "moderate")
    outputs["capital_flow"] = compute_capital_flow(
        regime=regime,
        trading_desk=outputs["trading_master"],
        portfolio_desk=outputs["portfolio_master"],
        wealth_desk=outputs["wealth_master"],
        investment_style=investment_style,
    )
    cf = outputs["capital_flow"]
    b  = cf.get("budget", {})
    print(f"  → 交易:{b.get('trading',0)*100:.0f}% / 配置:{b.get('portfolio',0)*100:.0f}% / 現金:{b.get('cash',0)*100:.0f}%")
    print(f"     流向:{cf['flow_direction']}")
    for flag in cf.get("override_flags", []):
        print(f"    ⚡ {flag}")

    # ── Phase 7: Master Agent (CIO) ───────────────────────────────────────────
    print("\n── Phase 7: Master Agent (CIO) ──")
    print("  master_agent...")
    outputs["_candidates"] = candidates[:3]   # scanner top3 for CIO context
    outputs["master"] = master_agent.run(outputs)
    print(f"    → FINAL: {outputs['master'].get('verdict')}")

    # ── Phase 8: Constraint Validator (pure Python, no LLM) ──────────────────
    print("\n── Phase 8: Constraint Validator ──")
    outputs["master"] = validate_constraints(
        master_output=outputs["master"],
        capital_flow=outputs["capital_flow"],
        regime=regime,
    )
    v = outputs["master"]
    if v.get("constraints_triggered"):
        print(f"  ⚡ {len(v['constraint_violations'])} 個違規已修正：")
        for viol in v["constraint_violations"]:
            print(f"    → {viol}")
    else:
        print(f"  ✓ 無違規（verdict={v.get('verdict')}）")

    # Log this run to system_decisions table for future policy review
    alpha_db.log_system_decision(
        date=t0.strftime("%Y-%m-%d"),
        capital_flow=outputs["capital_flow"],
        master_verdict_before=outputs["master"].get("_raw_verdict", v.get("verdict", "")),
        master_verdict_after=v.get("verdict", ""),
        constraint_violations=v.get("constraint_violations", []),
        regime=regime.get("market_regime", ""),
    )

    # ── Save analysis.json ────────────────────────────────────────────────────
    outputs.pop("_candidates", None)   # internal key, not for serialization

    # Pre-market preserves yesterday's tw_daily_pick (Phase 4 only runs post-market).
    # Without this, the morning cron overwrites analysis.json with an empty pick agent,
    # breaking the hub's "今日 pick" card link until the 16:30 cron runs.
    if t0.hour < 13 and not outputs.get("tw_daily_pick"):
        try:
            prev_path = os.path.join(DATA_DIR, "analysis.json")
            if os.path.exists(prev_path):
                with open(prev_path, encoding="utf-8") as f:
                    prev = json.load(f)
                prev_pick = (prev.get("agents", {}) or {}).get("tw_daily_pick")
                if prev_pick:
                    outputs["tw_daily_pick"] = prev_pick
                    print(f"  [pre-market] preserved previous tw_daily_pick "
                          f"({prev_pick.get('pick',{}).get('code','?')})")
        except Exception as e:
            print(f"  [WARN] preserve previous tw_daily_pick: {e}")

    analysis = {
        "generated_at": t0.isoformat(),
        "market_snapshot": {
            "taiex":       market_data["indices"].get("taiex", {}),
            "sp500":       market_data["indices"].get("sp500", {}),
            "vix":         market_data["indices"].get("vix", {}),
            "usd_twd":     market_data["fx"].get("usd_twd"),
            "jpy_per_usd": market_data["fx"].get("jpy_per_usd"),
            "twd_per_jpy": market_data["fx"].get("twd_per_jpy"),
        },
        "portfolio_value": market_data.get("portfolio_value", {}),
        "regime":          regime,
        "capital_flow":    outputs["capital_flow"],
        "performance":     perf_stats if perf_stats.get("available") else None,
        "agents":          outputs,
    }

    out_path = os.path.join(DATA_DIR, "analysis.json")
    save_json(analysis, out_path)

    # Also save signal vector as standalone file for dashboard / research
    sv_path = os.path.join(DATA_DIR, "signal_vector.json")
    save_json(outputs.get("signal_fusion", {}), sv_path)

    # Append to signal_history.jsonl — last 120 entries (~60 trading days × 2 runs)
    # Only the 10 numeric dims, no _sources/_data_gaps bloat.
    sv = outputs.get("signal_fusion", {})
    numeric_dims = {k: v for k, v in sv.items() if not k.startswith("_") and isinstance(v, (int, float))}
    hist_path = os.path.join(DATA_DIR, "signal_history.jsonl")
    hist_entry = {
        "ts":     datetime.now(TZ).isoformat(timespec="seconds"),
        "regime": regime.get("market_regime") if regime else None,
        "vector": numeric_dims,
    }
    try:
        lines = []
        if os.path.exists(hist_path):
            with open(hist_path, encoding="utf-8") as f:
                lines = f.readlines()
        lines.append(json.dumps(hist_entry, ensure_ascii=False) + "\n")
        if len(lines) > 120:
            lines = lines[-120:]
        with open(hist_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        print(f"  [WARN] signal_history append: {e}")

    elapsed = (datetime.now(TZ) - t0).total_seconds()
    print(f"\n{'='*60}")
    print(f"Complete in {elapsed:.1f}s → data/analysis.json")
    print(f"Master verdict: {outputs['master'].get('verdict')}")
    print(f"Capital Flow:   {cf['flow_direction']} | 交易{b.get('trading',0)*100:.0f}% / 配置{b.get('portfolio',0)*100:.0f}% / 現金{b.get('cash',0)*100:.0f}%")
    print(f"{'='*60}\n")

    master = outputs.get("master", {})
    print("MASTER SUMMARY:")
    print(master.get("summary", ""))
    for i, rec in enumerate(master.get("recommendations", [])[:3], 1):
        print(f"  {i}. [{rec.get('urgency')}] {rec.get('action')} {rec.get('target')}: {rec.get('detail','')}")


if __name__ == "__main__":
    run_all()
