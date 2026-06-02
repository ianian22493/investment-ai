"""
Signal Fusion Layer — 純 Python，無 LLM
把所有 agent output 壓縮成統一 numeric signal vector。
所有分數必須可追溯來源（_sources 欄位）。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

# Verdict string → direction score (-1 to 1)
_VERDICT_DIR = {
    "全面進攻": 1.0, "全力進攻": 1.0, "強烈買進": 1.0,
    "進攻": 0.8, "局部進攻": 0.6, "加碼": 0.6,
    "觀望": 0.0, "持倉不動": 0.0, "維持現狀": 0.0,
    "局部防守": -0.4, "減碼": -0.5,
    "防守": -0.7, "全面防守": -1.0, "賣出": -0.8,
}

# Regime name → base score (-1 to 1)
_REGIME_BASE = {
    "AI主升段": 1.0, "趨勢多頭": 0.8, "牛市整理": 0.5,
    "資金輪動": 0.3, "區間盤": 0.0, "高檔震盪": -0.1,
    "震盪整理": -0.1, "中性": 0.0, "防禦模式": -0.5,
    "空頭賣壓": -0.8, "恐慌盤": -1.0,
}

# Regime risk_level → adjustment
_REGIME_RISK_DELTA = {
    "low": 0.10, "moderate": 0.0, "elevated": -0.15,
    "high": -0.35, "extreme": -0.60,
}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(v)))


def _safe(val, default: float = 0.0) -> float:
    try:
        return float(val or default)
    except (TypeError, ValueError):
        return default


def _verdict_dir(verdict: str) -> float:
    for k, v in _VERDICT_DIR.items():
        if k in (verdict or ""):
            return v
    return 0.0


def compute(
    market_data: dict,
    outputs: dict,
    regime: dict,
    candidates: list,
) -> dict:
    sources = {}
    data_gaps = []

    # ──────────────────────────────────────────────────────────────────────────
    # 1. market_regime_score  (-1.0 ~ 1.0)
    #    Source: regime_engine.market_regime name + risk_level adjustment
    # ──────────────────────────────────────────────────────────────────────────
    regime_name = regime.get("market_regime", "區間盤")
    regime_risk = regime.get("risk_level", "moderate")
    base = _REGIME_BASE.get(regime_name, 0.0)
    delta = _REGIME_RISK_DELTA.get(regime_risk, 0.0)
    market_regime_score = _clamp(base + delta, -1.0, 1.0)
    sources["market_regime_score"] = (
        f"regime={regime_name}(base={base:+.1f}) "
        f"risk_level={regime_risk}(delta={delta:+.2f})"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 2. trend_strength  (0.0 ~ 1.0)
    #    Source: TAIEX daily change% + market_overview verdict direction
    #    +3% → 1.0 | 0% → 0.5 | -3% → 0.0
    # ──────────────────────────────────────────────────────────────────────────
    indices = market_data.get("indices", {})
    taiex_chg = _safe(indices.get("taiex", {}).get("change_pct"), 0.0)
    trend_base = _clamp(0.5 + taiex_chg / 6.0)
    mo_dir = _verdict_dir(outputs.get("market_overview", {}).get("verdict", "")) * 0.1
    trend_strength = _clamp(trend_base + mo_dir)
    sources["trend_strength"] = (
        f"taiex_chg={taiex_chg:+.2f}%→base={trend_base:.3f} "
        f"market_overview_adj={mo_dir:+.2f}"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 3. risk_pressure  (0.0 ~ 1.0)
    #    Source: VIX (primary) + tw_short_term verdict + devils_advocate conf
    #    VIX 12 → 0.0 | 22 → 0.5 | 32 → 1.0
    # ──────────────────────────────────────────────────────────────────────────
    vix = _safe(
        indices.get("vix", {}).get("close") or indices.get("vix", {}).get("value"),
        20.0,
    )
    vix_component = _clamp((vix - 12.0) / 20.0)
    tw_dir = _verdict_dir(outputs.get("tw_short_term", {}).get("verdict", ""))
    tw_adj = -tw_dir * 0.08   # bullish tw → lower pressure
    da_conf = _safe(outputs.get("devils_advocate", {}).get("confidence"), 0.5)
    da_component = da_conf * 0.15   # high DA confidence = more bearish concern
    risk_pressure = _clamp(vix_component + tw_adj + da_component)
    sources["risk_pressure"] = (
        f"vix={vix:.1f}→{vix_component:.3f} "
        f"tw_verdict_adj={tw_adj:+.3f} "
        f"da_conf={da_conf:.2f}→{da_component:.3f}"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 4. volatility_risk  (0.0 ~ 1.0)
    #    Source: VIX (70%) + TAIEX absolute daily move (30%)
    #    VIX 10 → 0.0 | 25 → 0.5 | 40 → 1.0
    # ──────────────────────────────────────────────────────────────────────────
    vix_vol = _clamp((vix - 10.0) / 30.0)
    taiex_abs_vol = _clamp(abs(taiex_chg) / 4.0)  # 4% daily move → 1.0
    volatility_risk = _clamp(vix_vol * 0.7 + taiex_abs_vol * 0.3)
    sources["volatility_risk"] = (
        f"vix={vix:.1f}→{vix_vol:.3f}×0.7 "
        f"taiex_abs={abs(taiex_chg):.2f}%→{taiex_abs_vol:.3f}×0.3"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 5. confidence_score  (0.0 ~ 1.0)
    #    Source: avg of key agent confidence scores, -0.10 per ERROR agent
    # ──────────────────────────────────────────────────────────────────────────
    key_agents = ["market_overview", "news_sentiment", "tw_short_term", "devils_advocate"]
    confs, errors, degraded = [], 0, 0
    for k in key_agents:
        a = outputs.get(k, {})
        # Hard error (no cache, no fallback) — penalize heavily
        if a.get("verdict") in ("ERROR", "—") or not a.get("verdict"):
            errors += 1
            continue
        # Degraded (live failed → reusing stale cache). Confidence still
        # usable but worth flagging — half penalty.
        if a.get("_degraded"):
            degraded += 1
            confs.append(_safe(a.get("confidence"), 0.5) * 0.7)
        else:
            confs.append(_safe(a.get("confidence"), 0.5))
    avg_conf = sum(confs) / max(1, len(confs))
    confidence_score = _clamp(avg_conf - errors * 0.10 - degraded * 0.05)
    sources["confidence_score"] = (
        f"avg_agent_conf={avg_conf:.3f} error_agents={errors} "
        f"degraded={degraded} penalty={errors*0.10 + degraded*0.05:.2f}"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 6. scanner_momentum  (0.0 ~ 1.0)
    #    Source: scanner candidate avg score (max 6 signals per stock)
    # ──────────────────────────────────────────────────────────────────────────
    if candidates:
        top_scores = [
            c.get("score", 0) for c in candidates[:10]
            if isinstance(c.get("score"), (int, float))
        ]
        if top_scores:
            avg_sc = sum(top_scores) / len(top_scores)
            scanner_momentum = _clamp(avg_sc / 6.0)
            sources["scanner_momentum"] = f"top{len(top_scores)}_avg={avg_sc:.2f}/6.0"
        else:
            scanner_momentum = 0.5
            sources["scanner_momentum"] = "no valid scores"
    else:
        scanner_momentum = 0.5
        sources["scanner_momentum"] = "no candidates loaded"

    # ──────────────────────────────────────────────────────────────────────────
    # 7. ai_sector_strength  (0.0 ~ 1.0)
    #    Source: inferred from regime_factors.ai_hits (no direct sector ETF data)
    # ──────────────────────────────────────────────────────────────────────────
    ai_hits = int(_safe(regime.get("regime_factors", {}).get("ai_hits"), 0))
    if ai_hits >= 5:
        ai_sector_strength = 0.85
    elif ai_hits >= 3:
        ai_sector_strength = 0.70
    elif ai_hits >= 1:
        ai_sector_strength = 0.55
    else:
        ai_sector_strength = 0.35
    sources["ai_sector_strength"] = (
        f"inferred from ai_headline_hits={ai_hits} "
        f"(no direct sector ETF — batch 2 target)"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 8. foreign_flow_strength  (0.0 ~ 1.0)
    #    Source: TWSE BFI82U 外資及陸資買賣超「金額」（全市場合計，單位:元）
    #    Normalize: +500億 → 1.0 | 0 → 0.5 | -500億 → 0.0
    # ──────────────────────────────────────────────────────────────────────────
    inst = market_data.get("institutional_market", {})
    f_net_amt = inst.get("foreign_net_amount", inst.get("foreign_net_shares"))
    if f_net_amt is not None and inst.get("foreign_net_amount") is not None:
        f_net = _safe(f_net_amt, 0.0)
        # Clamp to ±500 億元 range (±5e10)
        foreign_flow_strength = _clamp(0.5 + f_net / 1e11)
        sources["foreign_flow_strength"] = (
            f"BFI82U_foreign_net={f_net/1e8:+.1f}億元 → "
            f"normalized={foreign_flow_strength:.3f} (÷500億 clamp±0.5)"
        )
    else:
        foreign_flow_strength = 0.5
        sources["foreign_flow_strength"] = "BFI82U data unavailable (盤中/假日) → 0.50 neutral"
        data_gaps.append("foreign_flow_strength")

    # ──────────────────────────────────────────────────────────────────────────
    # 9. breadth_score  (0.0 ~ 1.0)
    #    Source: TWSE MI_INDEX 漲跌家數
    #    advance_ratio = 上漲家數 / (上漲+下跌) — already includes 漲停/跌停
    #    limit_up/limit_down skew: extreme limit-up days → bonus; limit-down → penalty
    # ──────────────────────────────────────────────────────────────────────────
    breadth = market_data.get("breadth", {})
    if breadth.get("advance_ratio") is not None:
        # advance_ratio already includes limit_up in numerator — use directly
        adv_ratio = _safe(breadth["advance_ratio"], 0.5)
        lu = int(_safe(breadth.get("limit_up", 0), 0.0))
        ld = int(_safe(breadth.get("limit_down", 0), 0.0))
        adv = int(_safe(breadth.get("advance", 0), 0.0))
        dec = int(_safe(breadth.get("decline", 0), 0.0))
        breadth_score = _clamp(adv_ratio)
        sources["breadth_score"] = (
            f"MI_INDEX advance={adv}(+{lu}漲停) decline={dec}(+{ld}跌停) "
            f"advance_ratio={adv_ratio:.3f}"
        )
    else:
        breadth_score = 0.5
        sources["breadth_score"] = "MI_INDEX data unavailable → 0.50 neutral"
        data_gaps.append("breadth_score")

    # ──────────────────────────────────────────────────────────────────────────
    # 10. liquidity_score  (0.0 ~ 1.0)
    #    Source: TWSE MI_MARGN 融資餘額日變化
    #    Logic: 融資餘額大增 = 散戶加槓桿 = 流動性脆弱（score 偏低）
    #           融資餘額減少 = 去槓桿 = 較健康（score 偏高）
    #    Normalize: -2% → 0.7  | 0% → 0.5 | +2% → 0.3
    # ──────────────────────────────────────────────────────────────────────────
    margin = market_data.get("margin_balance", {})
    if margin and margin.get("margin_change_pct") is not None:
        pct = _safe(margin["margin_change_pct"], 0.0)
        # Higher margin balance = more leverage = less healthy liquidity (inverted)
        # ±2% change maps to ±0.2 from 0.5
        liquidity_score = _clamp(0.5 - pct / 10.0)
        sources["liquidity_score"] = (
            f"margin_balance={margin.get('margin_balance_amount',0)/1e8:.0f}億元 "
            f"變化={pct:+.2f}% → score={liquidity_score:.3f} (-2%→0.7, +2%→0.3)"
        )
    else:
        liquidity_score = 0.5
        sources["liquidity_score"] = "margin balance unavailable → 0.50 neutral"
        data_gaps.append("liquidity_score")

    return {
        "market_regime_score": round(market_regime_score, 3),
        "trend_strength":      round(trend_strength, 3),
        "risk_pressure":       round(risk_pressure, 3),
        "volatility_risk":     round(volatility_risk, 3),
        "confidence_score":    round(confidence_score, 3),
        "scanner_momentum":    round(scanner_momentum, 3),
        "ai_sector_strength":  round(ai_sector_strength, 3),
        "foreign_flow_strength": round(foreign_flow_strength, 3),
        "breadth_score":       round(breadth_score, 3),
        "liquidity_score":     round(liquidity_score, 3),
        "_sources":            sources,
        "_data_gaps":          data_gaps,
        "_computed_at":        datetime.now(TZ).isoformat(),
    }
