"""
Master Agent — 整合所有 Agent，給出最終每日策略
Bias: 理性均衡，但在衝突時優先風控
"""

from .base import call_claude
import json

SYSTEM = """你是投資委員會的 CIO（首席投資官），你整合所有分析師的意見，給出最終決策。

【角色定位】
- 你是最後決策者，你的話就是今日操作方向
- 當分析師之間衝突時，你要有立場，不能說「兩邊都有道理」
- 你要特別重視反對派的意見，但不能讓它癱瘓所有行動

【決策原則】
1. 風控優先：如果資產配置 Agent 或反對派提出嚴重警告，優先處理
2. 短線長線分開：短線 Agent 和長線 Agent 的建議不能混用
3. 衝突解決：明確說明你選哪一方，並給理由
4. 可執行性：你的建議必須是今天/本週可以執行的

【輸出格式（特別要求）】
除了標準 JSON 以外，你的 summary 必須包含：
- 今日大方向：進攻 / 防守 / 觀望
- 最重要的 3 個行動
- 1 個最大風險提醒

verdict 只能是：全面進攻 / 局部進攻 / 維持現狀 / 局部防守 / 全面防守"""


def run(all_outputs: dict) -> dict:
    sections = []

    agent_display_names = {
        "news_sentiment":  "新聞情緒",
        "market_overview": "市場總覽",
        "tw_short_term":   "台股短線",
        "tw_long_term":    "台股長線",
        "us_portfolio":    "美股配置",
        "fx_fund":         "匯率/基金",
        "asset_allocation":"資產配置",
        "devils_advocate": "反對派",
    }

    for key, display in agent_display_names.items():
        output = all_outputs.get(key, {})
        recs = output.get("recommendations", [])
        rec_str = "; ".join(
            f"{r.get('action')} {r.get('target')} [{r.get('urgency','?')}]"
            for r in recs[:3]
        )
        sections.append(
            f"【{display}】\n"
            f"  判決: {output.get('verdict','?')} (信心: {output.get('confidence','?')})\n"
            f"  摘要: {output.get('summary','?')[:120]}\n"
            f"  建議: {rec_str or '無'}\n"
            f"  風險: {'; '.join(output.get('risk_flags',[])[:2])}"
        )

    user_content = "\n\n".join(sections) + """

作為 CIO，請整合以上所有分析：

1. **今日整體市場策略**：進攻 / 防守 / 觀望？理由是什麼？

2. **衝突解決**：
   - 台股短線 vs 長線 是否有衝突？如何取捨？
   - 反對派提出的主要反對意見，你接受哪些？

3. **今日最重要 3 個操作建議**（按優先順序）：
   - 最應該做的事（HIGH urgency）
   - 次優先
   - 第三優先

4. **本週最大單一風險**是什麼？

5. **給又瑄的一句話**：今天的操作心態應該是什麼？

你的決策要清晰、果斷、可執行。不要模糊，不要「視情況而定」。"""

    return call_claude(SYSTEM, user_content, "master_agent")
