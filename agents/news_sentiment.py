"""新聞情緒 Agent — 分析台股/美股消息面，提供情緒訊號。"""

from .base import call_llm

SYSTEM_PROMPT = """你是一位專業的市場消息面分析師，擅長從新聞標題中提取對台灣和美國股市的情緒訊號。

你的風格：
- 客觀中立，不預設立場
- 聚焦「這些新聞對目前持倉有何直接影響」
- 識別可能造成短期波動的事件風險
- 區分真正重要的新聞 vs 噪音

持倉重點：
- 台股：台積電(2330)、00692、00915、2834、3293、3703、4588、4707等
- 美股：NVDA、TSLA、GOOGL、AMZN、MSFT為核心部位

你被禁止：
- 不做技術分析、不看K線均線
- 不給具體進出場價位（那是其他agent的工作）
- 不過度解讀單一標題，需綜合判斷
"""


def run(market_data: dict, portfolio: dict) -> dict:
    news = market_data.get("news", {})
    tw_headlines = news.get("tw", [])
    us_headlines = news.get("us", [])

    if not tw_headlines and not us_headlines:
        return {
            "verdict": "無資料",
            "confidence": 0,
            "summary": "今日新聞資料無法取得，可能是網路問題。",
            "key_insights": [],
            "recommendations": [],
            "risk_flags": ["新聞源連線失敗"],
            "agent_note": "news fetch returned empty",
        }

    user_content = f"""請分析以下今日新聞標題，判斷整體市場情緒及對持倉的影響：

【台灣市場新聞（共{len(tw_headlines)}則）】
{chr(10).join(f"- {h}" for h in tw_headlines) if tw_headlines else "（無資料）"}

【美國市場新聞（共{len(us_headlines)}則）】
{chr(10).join(f"- {h}" for h in us_headlines) if us_headlines else "（無資料）"}

分析重點：
1. 整體情緒偏向（正面/負面/中性）
2. 最值得注意的 2-3 個主題或事件
3. 對持倉有直接影響的新聞（點名具體個股）
4. 有無需要立即警戒的風險事件
5. 建議其他 agent 特別留意的背景資訊

verdict 請填整體情緒：「正面」、「偏正面」、「中性」、「偏負面」、「負面」
"""

    return call_llm(SYSTEM_PROMPT, user_content, "news_sentiment")
