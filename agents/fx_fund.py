"""
匯率/基金 Agent — 日幣趨勢 + 定期定額管理
Bias: 保守，優先保護匯率成本
"""

from .base import call_claude

SYSTEM = """你是一位日幣匯率與基金策略專家。

【角色偏見 — 保守謹慎】
- 日幣是長期升值資產，但不要在高點過度加碼
- 定期定額的核心是紀律，除非匯率極端，否則不輕易暫停
- 關注 BOJ（日本央行）政策動向

【分析框架】
1. 日幣現值 vs 歷史區間（150-160 偏弱，140-150 正常，<140 偏強）
2. 日幣趨勢（近期走強/走弱）
3. 基金績效趨勢
4. 定期定額是否需調整

【持倉】（最新市值與報酬率見分析資料）
- 每月定額合計：¥75,000

verdict 只能是：增加定額 / 維持定額 / 暫停定額 / 減少定額"""


def run(market_data: dict, portfolio: dict, market_overview: dict, news_sentiment: dict = {}) -> dict:
    fx = market_data.get("fx", {})
    funds = portfolio.get("funds", [])

    jpy_usd = fx.get("jpy_per_usd", 155)
    twd_per_jpy = fx.get("twd_per_jpy", 0.215)

    # 判斷日幣水位
    if jpy_usd:
        if jpy_usd < 140:
            jpy_level = "偏強（日幣升值）"
        elif jpy_usd < 150:
            jpy_level = "正常偏強"
        elif jpy_usd < 160:
            jpy_level = "正常偏弱（歷史弱勢）"
        else:
            jpy_level = "極度弱勢（近20-30年低點）"
    else:
        jpy_level = "N/A"

    lines = []
    lines.append(f"【匯率】")
    lines.append(f"USD/JPY: {jpy_usd} → 日幣水位：{jpy_level}")
    lines.append(f"JPY/TWD: {twd_per_jpy}")
    lines.append(f"\n【基金持倉】")
    for f in funds:
        monthly = f['monthly_dca_jpy']
        val_twd = f['current_value_jpy'] * (twd_per_jpy or 0.215)
        lines.append(
            f"- {f['name']}: ¥{f['current_value_jpy']:,} "
            f"(≈NT${val_twd:,.0f}) 報酬{f['return_pct']}% 月投¥{monthly:,}"
        )

    total_jpy = sum(f['current_value_jpy'] for f in funds)
    total_twd = total_jpy * (twd_per_jpy or 0.215)
    lines.append(f"\n合計: ¥{total_jpy:,} ≈ NT${total_twd:,.0f}")
    lines.append(f"每月定額: ¥75,000 ≈ NT${75000*(twd_per_jpy or 0.215):,.0f}")
    if news_sentiment.get("verdict") not in (None, "ERROR", "無資料"):
        lines.append(f"\n今日新聞情緒: {news_sentiment.get('verdict')} — {news_sentiment.get('summary','')[:100]}")

    user_content = "\n".join(lines) + """

請分析：
1. 目前日幣匯率水位如何？對台灣投資人是否有利？
2. 兩支基金績效差異如何（見上方數據）？建議如何調整定額比例？
3. 本月定期定額是否繼續？是否應增減金額？
4. 如果日幣繼續走弱（>160），策略是什麼？"""

    return call_claude(SYSTEM, user_content, "fx_fund")
