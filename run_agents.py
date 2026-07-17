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
    master_agent, tw_daily_pick, holdings_correlation,
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


def evaluate_panic_sop(market_data: dict) -> dict:
    """連動 #3：對照寶藏股研究室的恐慌日 SOP 觸發條件。
    條件的唯一正本在 data/watchlist.json 的 panic 區塊（觀察名單機器鏡像）：
    VIX > 25 或 台股單日跌幅 ≤ -2.5%。觸發時前端顯示 SOP 啟動 banner。
    swing 系統在恐慌日封鎖新倉；寶藏系統（長波）反而視為買點窗口——
    這個 banner 就是兩套系統的交班訊號。
    """
    rules = {"vix_above": 25, "twii_daily_drop_pct_below": -2.5}
    path = os.path.join(DATA_DIR, "watchlist.json")
    try:
        if os.path.exists(path):
            rules.update({k: v for k, v in (load_json(path).get("panic") or {}).items()
                          if isinstance(v, (int, float))})
    except Exception:
        pass

    indices = market_data.get("indices", {})
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    vix = _f(indices.get("vix", {}).get("close"))
    taiex_chg = _f(indices.get("taiex", {}).get("change_pct"))

    vix_hit = vix is not None and vix > float(rules["vix_above"])
    drop_hit = taiex_chg is not None and taiex_chg <= float(rules["twii_daily_drop_pct_below"])
    reasons = []
    if vix_hit:
        reasons.append(f"VIX {vix:.1f} > {rules['vix_above']}")
    if drop_hit:
        reasons.append(f"台股單日 {taiex_chg:+.2f}% ≤ {rules['twii_daily_drop_pct_below']}%")

    return {
        "triggered": bool(vix_hit or drop_hit),
        "reasons": reasons,
        "vix": vix,
        "taiex_chg_pct": taiex_chg,
        "rules": rules,
    }


def load_treasure_watchlist() -> dict:
    """讀 data/watchlist.json（寶藏股研究室觀察名單的機器鏡像），
    回傳 {台股代號: entry} 對照表。symbol 格式 "2330.TW"/"8299.TWO" → 取代號。
    美股 symbol（無 .TW 後綴）跳過——swing pick 只選台股。
    """
    path = os.path.join(DATA_DIR, "watchlist.json")
    if not os.path.exists(path):
        return {}
    try:
        w = load_json(path)
        out = {}
        for s in w.get("stocks", []):
            sym = str(s.get("symbol", ""))
            if ".TW" not in sym:   # covers .TW and .TWO
                continue
            code = sym.split(".")[0]
            # 同一代號可能有多條 rule（如 SOUN 有兩條）— 台股目前一檔一條，
            # 若重複就併 note
            if code in out:
                out[code]["note"] += "；" + s.get("note", "")
            else:
                out[code] = {
                    "name": s.get("name", ""),
                    "rule": s.get("rule", ""),
                    "note": s.get("note", ""),
                }
        return out
    except Exception:
        return {}


# Trial-pick gating thresholds — surfaces at this trading budget level,
# regardless of master's 觀望 verdict. Lowered 2026-06-18 from 0.15 → 0.05
# because Capital Flow was locking trading at 5% during pre-payment windows
# and the user was getting zero recommendations for 14+ days even when
# scanner had high-conviction candidates.
MICRO_PICK_MIN_TRADING_BUDGET = 0.05    # was 0.15
MICRO_PICK_MIN_SCANNER_SCORE  = 5       # scanner score on 0-8 swing scale
MICRO_PICK_SIZE_RATIO         = 0.30    # 30% of trading budget = trial size

# Swing position management (2026-07-17 波段改版)
# 最多同時 3 檔在倉。倉滿時跳過 pick LLM call（省 quota + 避免誘惑）。
MAX_OPEN_POSITIONS = 3

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

    # 倉滿日不給試單建議 — 倉位紀律優先於任何新機會（含 micro）
    if len(pick.get("open_positions") or []) >= pick.get("max_positions", MAX_OPEN_POSITIONS):
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
    SIG_KEYS = ("trend_up", "above_ma60", "pullback_buy", "base_breakout", "vol_accumulate", "rsi_swing", "rs_20d_strong")
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
        # 寶藏雷達標記（連動 #2）：候選股若在寶藏觀察名單，掛上標記
        # 讓 tw_daily_pick 的 prompt 看得到（agent 可加權或警惕）
        _treasure = load_treasure_watchlist()
        for c in candidates:
            if c.get("code") in _treasure:
                c["treasure_watch"] = _treasure[c["code"]]
        # 真實基本面注入（連動 #1）：寶藏真篩選器的全市場月營收/毛利率
        # 資料（data/screener/history/）。波段策略要求基本面動能——
        # 在此之前 agent 只能從新聞猜，現在每檔候選都有真數據。
        try:
            import fundamental_feed
            _funds = fundamental_feed.get_fundamentals([c["code"] for c in candidates])
            for c in candidates:
                c["fundamental_line"] = fundamental_feed.format_for_prompt(
                    c["code"], _funds.get(c["code"]))
            print(f"  [fundamental] 月營收/毛利率覆蓋 {len(_funds)}/{len(candidates)} 檔")
        except Exception as e:
            print(f"  [WARN] fundamental_feed failed: {e}")

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

        # ── Swing position gate ──────────────────────────────────────────
        # resolve_pending() (run earlier) has refreshed open-position stats.
        # 倉滿 → 直接空手，不呼叫 LLM（省 quota，也避免 agent 硬找標的）。
        open_positions = alpha_db.get_open_positions()
        if open_positions:
            print(f"  [swing book] 在倉 {len(open_positions)}/{MAX_OPEN_POSITIONS}:")
            for op in open_positions:
                cur = op.get("current_return_pct")
                cur_str = f"{cur:+.1f}%" if isinstance(cur, (int, float)) else "?"
                print(f"    · {op['stock_name']}({op['stock_code']}) "
                      f"{op['date']} 進 · 現況 {cur_str} · D+{op.get('hold_days_actual') or 0}")

        print("  tw_daily_pick...")
        if len(open_positions) >= MAX_OPEN_POSITIONS:
            outputs["tw_daily_pick"] = {
                "verdict": "空手觀望",
                "confidence": 1.0,
                "pick": {"name": "—", "code": "—", "entry_zone": "—",
                         "stop_loss": "—", "target": "—", "risk_reward": "—",
                         "ref_close": "—", "hold_days": "—"},
                "market_regime": regime.get("market_regime", ""),
                "risk_appetite": "中性",
                "suitable_for_trading": False,
                "summary": (
                    f"波段倉位已滿（{len(open_positions)}/{MAX_OPEN_POSITIONS} 檔在倉），"
                    f"今日不開新倉。專注管理現有部位：依既定停損 / 目標紀律執行。"
                ),
                "core_logic": {},
                "risk_flags": ["倉位已滿——新機會再好也不加倉，等現有部位結算"],
                "counter_argument": "",
                "agent_note": "positions-full gate (rule-based, no LLM call)",
            }
        else:
            outputs["tw_daily_pick"] = tw_daily_pick.run(
                market_data, portfolio_live, outputs["market_overview"], news,
                regime=regime, candidates=candidates,
                open_positions=open_positions,
            )
        pick = outputs["tw_daily_pick"]

        # 防呆：agent 不聽話重複推薦在倉股票 → 強制降為空手（避免 DB 重複開倉）
        open_codes = {op["stock_code"] for op in open_positions}
        picked_code = (pick.get("pick") or {}).get("code")
        if picked_code and picked_code in open_codes:
            print(f"    [WARN] agent 重複推薦在倉股票 {picked_code} — 強制空手")
            pick["verdict"] = "空手觀望"
            pick["summary"] = f"[系統攔截] agent 推薦了已在倉的 {picked_code}，已強制改為觀望。" + pick.get("summary", "")
            pick["pick"] = {"name": "—", "code": "—", "entry_zone": "—",
                            "stop_loss": "—", "target": "—", "risk_reward": "—",
                            "ref_close": "—", "hold_days": "—"}

        # 在倉部位隨 analysis.json 出去給前端（dashboard swing book + pick 頁）
        pick["open_positions"] = open_positions
        pick["max_positions"] = MAX_OPEN_POSITIONS
        print(f"    → {pick.get('verdict')} | {pick.get('pick',{}).get('name','?')}({pick.get('pick',{}).get('code','?')})")

        # Holdings correlation (Task C) — 純 Python，不 call LLM。
        # 提示同大類/相同精細類的重疊，警告過度集中風險。
        try:
            pick_code = (pick.get("pick") or {}).get("code", "")
            corr = holdings_correlation.compute(pick_code, portfolio_live)
            pick["correlation"] = corr
            if corr.get("warning_level") in ("high", "medium"):
                print(f"    → 相關性: {corr['warning_level']} · {corr.get('summary','')[:80]}")
        except Exception as e:
            print(f"    [WARN] holdings_correlation failed: {e}")

        # 寶藏雷達交叉比對（連動 #2）— swing pick 命中寶藏股研究室的
        # 觀察名單（data/watchlist.json）時附掛收斂徽章。兩套獨立方法論
        # （日線波段 vs 長波論述卡）同時看上同一檔 = 強訊號；反之若命中
        # 的是「持倉警戒」條目，則是重要的反向提示。
        try:
            treasure = load_treasure_watchlist()
            pick_code = (pick.get("pick") or {}).get("code", "")
            if pick_code and pick_code in treasure:
                pick["treasure_watch"] = {"code": pick_code, **treasure[pick_code]}
                print(f"    → ⭐ 寶藏雷達也在追蹤 {pick_code}: {treasure[pick_code].get('note','')[:60]}")
        except Exception as e:
            print(f"    [WARN] treasure watchlist lookup failed: {e}")

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

    # 恐慌日 SOP 判定（連動 #3）— 觸發時前端顯示 banner
    panic_sop = evaluate_panic_sop(market_data)
    if panic_sop["triggered"]:
        print(f"  [panic-sop] 🚨 恐慌日 SOP 條件觸發：{' · '.join(panic_sop['reasons'])}")

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
        "panic_sop":       panic_sop,
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
