"""
台股長線 Agent — 基本面 + 產業趨勢，保守偏見
Bias: 保守，護本優先，不輕易換股
禁止: 不給短期買賣點、不看技術面
"""

from .base import call_claude

SYSTEM = """你是一位保守型的台股長線投資人，專注基本面和產業護城河。

【角色偏見 — 保守】
- 持倉不動是預設選項，換股需要強力理由
- 寧可錯過反彈，不要賺了又賠回去
- 對虧損部位特別嚴格：-30% 以上要有非常充分的理由才能繼續持有

【絕對禁止】
- ❌ 不給短期買賣點（明日進場、週線支撐這類說法）
- ❌ 不看技術指標（MA、KD、MACD 等）
- ❌ 不因為短期漲跌建議換股

【分析框架】
1. 產業趨勢（這個產業 3-5 年還有沒有前景？）
2. 公司護城河（為什麼客戶不轉單？）
3. 估值（相對歷史 P/B、P/E 高估還是低估？）
4. 虧損部位特別審查（論述是否仍然成立？）

【對虧損部位的特別規則】
- 宏普 2536、欣陸 3703、玖鼎電力 4588 為主要虧損部位（實際損益見分析資料）
- 每檔都必須判斷持有論述是否仍成立，不能只因「跌了就該等回來」而續抱

verdict 只能是：續抱 / 加碼 / 減碼 / 停損出場 / 觀察"""


def run(market_data: dict, portfolio: dict, market_overview: dict, news_sentiment: dict = {}) -> dict:
    tw_stocks = portfolio.get("tw_stocks", [])

    lines = []
    lines.append("【台股長線持倉狀況】")
    for s in tw_stocks:
        lines.append(
            f"{s['name']}({s['code']}): 現值{s['value']:,} 成本{s['cost']:,} "
            f"損益{s['pnl']:+,}({s['pnl_pct']:+.1f}%) "
            f"{'⚠️ 需審查' if s.get('watch') else ''}"
        )

    lines.append(f"\n台股總市值: {portfolio['tw_summary']['total_value']:,}")
    lines.append(f"整體報酬: +{portfolio['tw_summary']['total_pnl_pct']}%")
    lines.append(f"\n市場總覽: {market_overview.get('verdict')} — {market_overview.get('summary','')[:80]}")
    if news_sentiment.get("verdict") not in (None, "ERROR", "無資料"):
        lines.append(f"今日新聞情緒: {news_sentiment.get('verdict')} — {news_sentiment.get('summary','')[:100]}")

    user_content = "\n".join(lines) + """

請以保守長線投資人角色，針對以上持倉：

1. **虧損三檔審查**（宏普2536、欣陸3703、玖鼎電力4588）：
   - 各自的持有論述是什麼？現在是否仍然成立？
   - 若論述已破壞，明確建議停損出場

2. **盈利持倉評估**：
   - 是否有哪檔已過度高估，應該減碼？
   - 是否有低估的標的值得加碼？

3. **整體長線配置建議**

記住：你不看技術面，不給短期價位，只看基本面和產業邏輯。"""

    return call_claude(SYSTEM, user_content, "tw_long_term")
