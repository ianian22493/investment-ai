"""
Market Regime Engine — 純 Python 規則引擎，無 LLM
判斷市場體制：AI主升段 / 趨勢多頭 / 資金輪動 / 區間盤 / 高檔震盪 / 空頭賣壓 / 恐慌盤
"""

AI_KEYWORDS = [
    "AI", "人工智慧", "NVIDIA", "輝達", "GB200", "NVL",
    "AI伺服器", "AI晶片", "CoWoS", "HBM", "算力",
    "AI基礎設施", "AI PC", "生成式AI", "大模型",
    "ASIC", "TPU", "AI應用", "封裝", "散熱模組",
    "Blackwell", "Hopper", "機器人", "自動駕駛",
]


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val or default)
    except (TypeError, ValueError):
        return default


def determine_regime(market_data: dict) -> dict:
    """
    Returns:
    {
      "market_regime": str,
      "risk_level": "low" | "moderate" | "elevated" | "high" | "extreme",
      "short_term_trading_favorable": bool,
      "confidence": float,
      "regime_factors": dict,
      "regime_summary": str   # 一行摘要，供 agent prompt 注入
    }
    """
    indices = market_data.get("indices", {})
    news = market_data.get("news", {})

    vix = _safe_float(indices.get("vix", {}).get("close"), 20.0)
    taiex_chg = _safe_float(indices.get("taiex", {}).get("change_pct"), 0.0)
    nasdaq_chg = _safe_float(indices.get("nasdaq", {}).get("change_pct"), 0.0)
    sp500_chg = _safe_float(indices.get("sp500", {}).get("change_pct"), 0.0)

    # Count AI-related headlines
    all_headlines = news.get("tw", []) + news.get("us", [])
    all_text = " ".join(all_headlines).upper()
    ai_hits = sum(1 for kw in AI_KEYWORDS if kw.upper() in all_text)

    regime_factors = {
        "vix": vix,
        "taiex_chg_pct": taiex_chg,
        "nasdaq_chg_pct": nasdaq_chg,
        "sp500_chg_pct": sp500_chg,
        "ai_keyword_hits": ai_hits,
    }

    # ── Priority classification (highest risk first) ─────────────────────────
    if vix >= 30:
        regime = "恐慌盤"
        risk_level = "extreme"
        trading_ok = False
        confidence = 0.92

    elif vix >= 25 and (taiex_chg <= -1.5 or nasdaq_chg <= -1.5):
        regime = "空頭賣壓"
        risk_level = "high"
        trading_ok = False
        confidence = 0.82

    elif vix >= 20 and (taiex_chg <= -0.5 or nasdaq_chg <= -0.5):
        regime = "高檔震盪"
        risk_level = "elevated"
        trading_ok = False
        confidence = 0.72

    elif ai_hits >= 5 and taiex_chg >= 0.5 and nasdaq_chg >= 0.5 and vix < 20:
        regime = "AI主升段"
        risk_level = "low"
        trading_ok = True
        confidence = 0.85

    elif taiex_chg >= 0.5 and nasdaq_chg >= 0.3 and vix < 18:
        regime = "趨勢多頭"
        risk_level = "low"
        trading_ok = True
        confidence = 0.80

    elif abs(taiex_chg) < 0.5 and vix < 20 and (nasdaq_chg >= 0.3 or sp500_chg >= 0.3):
        regime = "資金輪動"
        risk_level = "moderate"
        trading_ok = True   # selective plays
        confidence = 0.65

    else:
        regime = "區間盤"
        risk_level = "moderate"
        trading_ok = False
        confidence = 0.60

    regime_summary = (
        f"市場體制：{regime}｜"
        f"VIX={vix:.1f}｜台股{taiex_chg:+.2f}%｜NASDAQ{nasdaq_chg:+.2f}%｜"
        f"AI話題度={ai_hits}｜短線適合交易：{'✓' if trading_ok else '✗'}"
    )

    return {
        "market_regime": regime,
        "risk_level": risk_level,
        "short_term_trading_favorable": trading_ok,
        "confidence": confidence,
        "regime_factors": regime_factors,
        "regime_summary": regime_summary,
    }
