"""
美股配置 Agent — 長線持倉管理 + 加碼時機
Bias: 長線樂觀，耐心等待加碼機會

Position tiering:
  Core (≥$500 current value): full individual analysis
  Tracking (<$500): compact table, brief mention only
"""

from .base import call_claude

SYSTEM = """你是一位專注美股的長線成長型投資人。

【角色偏見 — 耐心樂觀】
- 預設持倉不動，除非出現明顯加碼機會（回撤 >10%）
- 對 AI/科技/潔淨能源題材長期看多
- 不因短期波動減碼，但對高估值個股保持警惕

【分析框架】
1. 指數位置：S&P500 相對高/中/低
2. VIX 水位：>25 = 機會區間，>35 = 加速佈局
3. 核心持倉回撤幅度：回撤 >15% 可考慮加碼
4. 各類別平衡：AI/科技、能源、資安、消費

【持倉分級】
- 核心持倉（≥$500 現值）：逐一分析，給出個股建議
- 追蹤持倉（<$500 現值）：整體觀察即可，不需逐一深析

verdict 只能是：積極加碼 / 小幅加碼 / 持倉觀察 / 暫緩加碼 / 減碼部分高風險"""

# Threshold (USD) for core vs tracking position
CORE_THRESHOLD_USD = 500.0


def run(market_data: dict, portfolio: dict, market_overview: dict, news_sentiment: dict = {}, regime: dict = None) -> dict:
    indices  = market_data.get("indices", {})
    us_prices = market_data.get("us_stocks", {})
    fx       = market_data.get("fx", {})
    usd_twd  = float(fx.get("usd_twd") or 32.0)

    # ── Tier holdings ─────────────────────────────────────────────────────────
    core_positions    = []
    tracking_positions = []

    for s in portfolio.get("us_stocks", []):
        ticker = s["ticker"]
        p      = us_prices.get(ticker, {})
        close  = p.get("close")

        try:
            current_value_usd = float(close) * float(s["shares"])
        except (TypeError, ValueError):
            # Fall back to cost basis if live price unavailable
            current_value_usd = float(s.get("avg_cost_usd", 0)) * float(s.get("shares", 0))

        s = {**s, "_current_value_usd": current_value_usd, "_price_data": p}

        if current_value_usd >= CORE_THRESHOLD_USD:
            core_positions.append(s)
        else:
            tracking_positions.append(s)

    # Sort core by value descending
    core_positions.sort(key=lambda x: x["_current_value_usd"], reverse=True)

    # ── Build prompt ───────────────────────────────────────────────────────────
    lines = []
    if regime:
        lines.append(f"【市場體制】{regime['regime_summary']}")

    lines += [
        "【美股指數】",
        f"S&P500: {indices.get('sp500',{}).get('close','?')} ({indices.get('sp500',{}).get('change_pct','?')}%)",
        f"NASDAQ: {indices.get('nasdaq',{}).get('close','?')} ({indices.get('nasdaq',{}).get('change_pct','?')}%)",
        f"VIX: {indices.get('vix',{}).get('close','?')}",
        f"USD/TWD: {usd_twd}",
    ]

    # ── Core positions: full detail ────────────────────────────────────────────
    lines.append(f"\n【核心持倉（現值 ≥ $500 USD）共 {len(core_positions)} 檔】")
    for s in core_positions:
        ticker = s["ticker"]
        p      = s["_price_data"]
        val    = s["_current_value_usd"]
        avg    = float(s.get("avg_cost_usd", 0))
        close_val = p.get("close")

        try:
            pnl_pct = (float(close_val) / avg - 1) * 100 if avg > 0 else 0.0
            pnl_str = f"{pnl_pct:+.1f}%"
        except (TypeError, ValueError):
            pnl_str = "?"

        lines.append(
            f"  {ticker}({s['name']}): "
            f"收${close_val or '?'} 漲跌{p.get('change_pct','?')}% | "
            f"現值~${val:,.0f} | 成本${avg:.2f} | 報酬{pnl_str}"
        )

    # ── Tracking positions: compact table ─────────────────────────────────────
    if tracking_positions:
        lines.append(f"\n【追蹤持倉（現值 < $500 USD）共 {len(tracking_positions)} 檔 — 不需逐一深析】")
        for s in tracking_positions:
            ticker = s["ticker"]
            p      = s["_price_data"]
            val    = s["_current_value_usd"]
            avg    = float(s.get("avg_cost_usd", 0))
            close_val = p.get("close")
            try:
                pnl_pct = (float(close_val) / avg - 1) * 100 if avg > 0 else 0.0
                pnl_str = f"{pnl_pct:+.1f}%"
            except (TypeError, ValueError):
                pnl_str = "?"
            lines.append(
                f"  {ticker}({s['name']}): 收${close_val or '?'} | ~${val:.0f} | 報酬{pnl_str}"
            )

    pv = market_data.get("portfolio_value", {})
    total_usd = pv.get("us_stocks_usd", "?")
    total_twd = pv.get("us_stocks_twd", "?")
    try:
        total_twd_fmt = f"NT${int(total_twd):,}"
    except (TypeError, ValueError):
        total_twd_fmt = str(total_twd)

    lines += [
        f"\n美股總現值: ${total_usd:,} USD / {total_twd_fmt}",
        f"\n市場總覽: {market_overview.get('verdict')}",
    ]

    if news_sentiment.get("verdict") not in (None, "ERROR", "無資料"):
        lines.append(f"今日新聞情緒: {news_sentiment.get('verdict')} — {news_sentiment.get('summary','')[:100]}")
        for insight in news_sentiment.get("key_insights", [])[:2]:
            lines.append(f"  · {insight}")

    user_content = "\n".join(lines) + f"""

請分析（聚焦核心持倉，追蹤持倉整體帶過即可）：
1. 目前美股大盤位置（高/中/低），是否適合加碼？
2. 核心持倉中，哪些出現較大回撤，是加碼機會？哪些過熱需暫緩？
3. 追蹤持倉整體風險評估（一句話即可）
4. 本週建議：加碼 / 暫緩 / 等待更好機會？

注意：加碼建議請說明哪一檔、加碼比例（小/中/大）和理由。"""

    return call_claude(SYSTEM, user_content, "us_portfolio")
