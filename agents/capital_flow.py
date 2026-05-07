"""
Capital Flow Engine — 純 Python 規則引擎，無 LLM

這是系統中「唯一」有權決定資金分配比例的模組。

HARD RULE:
  ❌ Desk Committees 不能做資金分配
  ❌ Master Agent 只能在此模組設定的預算內做決策
  ✅ 只有這裡輸出 budget percentages

輸出：
  budget = {
    "trading":   0.0-1.0,   # 可用於短線交易的預算比例
    "portfolio": 0.0-1.0,   # 可用於中長線配置調整的預算比例
    "cash":      0.0-1.0,   # 建議持有現金比例
  }
  三者加總 = 1.0
"""


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val or default)
    except (TypeError, ValueError):
        return default


def compute(
    regime: dict,
    trading_desk: dict,
    portfolio_desk: dict,
    wealth_desk: dict,
) -> dict:
    """
    唯一負責資金預算分配的函式。
    Desk Committees 只提供信號和風險觀點，這裡決定錢怎麼分。
    """
    override_flags = []

    # ── Step 1: Base allocation（中性狀態的預設值）──────────────────────────
    # trading 30% / portfolio 50% / cash 20%（個人投資組合的保守基準）
    trading_pct   = 0.30
    portfolio_pct = 0.50
    cash_pct      = 0.20

    # ── Step 2: Wealth Desk risk_level 決定安全上限 ───────────────────────────
    wealth_risk    = wealth_desk.get("risk_level", "moderate")
    wealth_verdict = wealth_desk.get("verdict", "")

    if wealth_risk == "extreme" or "危險" in wealth_verdict:
        # 系統性財務危機：大幅降低可動用資金
        trading_pct   = 0.00
        portfolio_pct = 0.20
        cash_pct      = 0.80
        override_flags.append("WEALTH_EXTREME: 財務危機 → 全面轉現金")

    elif wealth_risk == "high" or "警戒" in wealth_verdict:
        trading_pct   = 0.05
        portfolio_pct = 0.35
        cash_pct      = 0.60
        override_flags.append("WEALTH_HIGH: 高財務風險 → 大幅增持現金")

    elif wealth_risk == "elevated" or "留意" in wealth_verdict:
        trading_pct   = 0.15
        portfolio_pct = 0.45
        cash_pct      = 0.40
        override_flags.append("WEALTH_ELEVATED: 財務偏緊 → 適度增持現金")

    elif wealth_risk == "low" or "穩健" in wealth_verdict:
        # 財務健康，可以積極一些
        trading_pct   = 0.35
        portfolio_pct = 0.55
        cash_pct      = 0.10

    # ── Step 3: Regime 調整交易預算 ──────────────────────────────────────────
    regime_risk = regime.get("risk_level", "moderate")
    regime_name = regime.get("market_regime", "")
    trading_favorable = regime.get("short_term_trading_favorable", True)

    if regime_risk == "extreme":
        # 市場恐慌：把交易預算轉為現金
        extra_cash = trading_pct
        trading_pct = 0.00
        cash_pct = min(1.0, cash_pct + extra_cash)
        override_flags.append("REGIME_EXTREME: 恐慌盤 → 交易預算歸零")

    elif regime_risk == "high":
        shift = trading_pct * 0.5
        trading_pct   -= shift
        cash_pct      = min(1.0, cash_pct + shift)
        override_flags.append("REGIME_HIGH: 空頭賣壓 → 交易預算減半")

    elif not trading_favorable and regime_risk in ("elevated", "moderate"):
        shift = trading_pct * 0.3
        trading_pct   -= shift
        cash_pct      = min(1.0, cash_pct + shift)
        override_flags.append("REGIME_UNFAVORABLE: 不適合短線 → 小幅降低交易預算")

    elif regime_name in ("AI主升段",) and regime_risk == "low":
        # 最佳做多環境：增加交易預算，從現金挪移
        boost = min(0.10, cash_pct - 0.10)
        if boost > 0:
            trading_pct += boost
            cash_pct    -= boost
            override_flags.append("REGIME_BULL: AI主升段 → 提升交易預算")

    # ── Step 4: Wealth 現金壓力調整 ──────────────────────────────────────────
    if wealth_desk.get("cash_crunch_risk"):
        # 近期有大額現金需求：鎖定一部分現金，不能動
        extra_reserve = 0.10
        shift = min(extra_reserve, trading_pct * 0.5)
        trading_pct -= shift
        cash_pct    = min(1.0, cash_pct + shift)
        override_flags.append("CASH_CRUNCH: 近期有大額付款 → 增加現金準備")

    # ── Step 5: Normalize（確保加總 = 1.0）────────────────────────────────────
    trading_pct   = max(0.0, round(trading_pct, 2))
    portfolio_pct = max(0.0, round(portfolio_pct, 2))
    cash_pct      = max(0.0, round(cash_pct, 2))

    total = trading_pct + portfolio_pct + cash_pct
    if total > 0 and abs(total - 1.0) > 0.01:
        # Normalize to sum = 1.0
        trading_pct   = round(trading_pct / total, 2)
        portfolio_pct = round(portfolio_pct / total, 2)
        cash_pct      = round(1.0 - trading_pct - portfolio_pct, 2)

    # ── Step 6: Determine flow direction ─────────────────────────────────────
    if trading_pct == 0:
        flow_direction = "cash_only"
    elif trading_pct <= 0.10:
        flow_direction = "defensive"
    elif trading_pct >= 0.35 and regime_name in ("AI主升段", "趨勢多頭"):
        flow_direction = "trading_aggressive"
    elif trading_pct >= 0.25:
        flow_direction = "trading_selective"
    else:
        flow_direction = "portfolio_focus"

    # ── Step 7: Build output ──────────────────────────────────────────────────
    budget = {
        "trading":   trading_pct,
        "portfolio": portfolio_pct,
        "cash":      cash_pct,
    }

    summary = (
        f"資金預算 → 交易:{trading_pct*100:.0f}% / "
        f"配置:{portfolio_pct*100:.0f}% / "
        f"現金:{cash_pct*100:.0f}% | "
        f"流向:{flow_direction}"
    )
    if override_flags:
        summary += f" | 觸發規則:{len(override_flags)}條"

    return {
        "budget":                budget,
        "flow_direction":        flow_direction,
        "override_flags":        override_flags,
        "override_count":        len(override_flags),
        "wealth_risk_triggered": wealth_risk in ("high", "extreme") or "危險" in wealth_verdict,
        "regime_risk_triggered": regime_risk in ("high", "extreme"),
        "cash_crunch_active":    wealth_desk.get("cash_crunch_risk", False),
        "summary":               summary,
        # Legacy fields (kept for backward compat with master_agent display)
        "trading_risk_budget":          "suspended" if trading_pct == 0 else ("reduced" if trading_pct < 0.20 else "normal"),
        "portfolio_risk_budget":        "reduced" if portfolio_pct < 0.35 else "normal",
        "trading_confidence_weight":    min(1.0, trading_pct / 0.30),   # normalized to base 0.30
        "portfolio_confidence_weight":  min(1.0, portfolio_pct / 0.50), # normalized to base 0.50
    }
