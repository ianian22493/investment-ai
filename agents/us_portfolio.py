"""
美股配置 Agent — 長線持倉管理 + 加碼時機
Bias: 長線樂觀，耐心等待加碼機會
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
3. 個股回撤幅度：回撤 >15% 可考慮加碼
4. 各類別平衡：AI/科技、能源、資安、消費

【特別關注】
- NVDA、MSFT、GOOGL：核心持倉，高品質，回撤加碼
- CELH、ONDS、SOUN：高波動投機部位，控制比重
- TSLA：Tesla 高波動，需特別監控
- ZS、RBRK、S：資安類，注意整體資安板塊趨勢

verdict 只能是：積極加碼 / 小幅加碼 / 持倉觀察 / 暫緩加碼 / 減碼部分高風險"""


def run(market_data: dict, portfolio: dict, market_overview: dict, news_sentiment: dict = {}) -> dict:
    indices = market_data.get("indices", {})
    us_prices = market_data.get("us_stocks", {})
    fx = market_data.get("fx", {})

    lines = []
    lines.append("【美股指數】")
    lines.append(f"S&P500: {indices.get('sp500',{}).get('close','?')} ({indices.get('sp500',{}).get('change_pct','?')}%)")
    lines.append(f"NASDAQ: {indices.get('nasdaq',{}).get('close','?')} ({indices.get('nasdaq',{}).get('change_pct','?')}%)")
    lines.append(f"VIX: {indices.get('vix',{}).get('close','?')}")
    lines.append(f"USD/TWD: {fx.get('usd_twd','?')}")

    lines.append("\n【美股持倉今日表現】")
    for s in portfolio.get("us_stocks", []):
        ticker = s["ticker"]
        p = us_prices.get(ticker, {})
        lines.append(
            f"{ticker}({s['name']}): 收${p.get('close','?')} "
            f"漲跌{p.get('change_pct','?')}%"
        )

    pv = market_data.get("portfolio_value", {})
    lines.append(f"\n美股現值(TWD): {pv.get('us_stocks_twd','?'):,}")
    lines.append(f"美股現值(USD): ${pv.get('us_stocks_usd','?'):,}")
    lines.append(f"\n市場總覽: {market_overview.get('verdict')}")
    if news_sentiment.get("verdict") not in (None, "ERROR", "無資料"):
        lines.append(f"今日新聞情緒: {news_sentiment.get('verdict')} — {news_sentiment.get('summary','')[:100]}")
        for insight in news_sentiment.get("key_insights", [])[:2]:
            lines.append(f"  · {insight}")

    user_content = "\n".join(lines) + """

請分析：
1. 目前美股大盤位置（高/中/低），是否適合加碼？
2. 哪些個股出現較大回撤，是加碼機會？
3. 哪些個股近期過熱，建議暫緩加碼？
4. 本週建議：加碼 / 暫緩 / 等待更好機會？

注意：加碼建議要說明加碼比例（小/中/大）和原因。"""

    return call_claude(SYSTEM, user_content, "us_portfolio")
