"""
Portfolio Desk Master — 整合中長線持倉管理
輸入：tw_long_term, us_portfolio
決策時間軸：月～季
"""

from .base import call_llm

SYSTEM = """你是 Portfolio Desk Risk Committee，負責整合中長線持倉信號並提供客觀觀點。

【你的職責 — 嚴格限制】
你只做一件事：把 Portfolio Desk 的持倉分析整合成清晰的信號觀點。
你「不能」做資金再分配、不能建議倉位比例、不能 override 其他 desk。

【HARD RULE — 不可違反】
❌ 禁止：「建議將台股比重調整至 X%」
❌ 禁止：任何涉及資金比例的配置建議
❌ 禁止：做任何資本分配決定
✅ 只輸出：持倉健康度、論述是否仍成立、哪些需要關注、risk view

【你只回答三個問題】
1. 台美股現有持倉的整體健康狀態？哪些論述已動搖？
2. 有沒有需要 CIO 立刻關注的持倉風險？
3. 這個 desk 對 CIO 的觀點：portfolio risk 是 high / medium / low？

verdict 只能是：持倉健康 / 部分隱憂 / 需要關注 / 論述動搖
"""


def run(
    tw_long_term: dict,
    us_portfolio: dict,
    capital_flow: dict = None,
) -> dict:
    lines = []

    if capital_flow:
        budget = capital_flow.get("portfolio_risk_budget", "normal")
        lines.append(f"【資金層指令】配置額度={budget}")
        if capital_flow.get("wealth_risk_triggered"):
            lines.append("  ⚠️ 財富層風險觸發：新增部位需謹慎")

    lines.append("【台股長線 Agent】")
    lines.append(f"verdict={tw_long_term.get('verdict')} confidence={tw_long_term.get('confidence')}")
    lines.append(f"摘要：{tw_long_term.get('summary','')[:150]}")
    for ins in tw_long_term.get("key_insights", [])[:3]:
        lines.append(f"  · {ins}")
    for rec in tw_long_term.get("recommendations", [])[:3]:
        lines.append(f"  [{rec.get('urgency')}] {rec.get('action')} {rec.get('target')}: {rec.get('detail','')[:60]}")

    lines.append(f"\n【美股配置 Agent】")
    lines.append(f"verdict={us_portfolio.get('verdict')} confidence={us_portfolio.get('confidence')}")
    lines.append(f"摘要：{us_portfolio.get('summary','')[:150]}")
    for rec in us_portfolio.get("recommendations", [])[:3]:
        lines.append(f"  [{rec.get('urgency')}] {rec.get('action')} {rec.get('target')}: {rec.get('detail','')[:60]}")

    user_content = "\n".join(lines) + """

作為 Portfolio Desk 主管，整合以上分析：
1. 台股長線持倉中，哪些論述最不穩固？
2. 美股目前是加碼、持守、還是等待回撤？
3. 跨市場（台股 vs 美股）的配置比重建議
4. 若資金層有約束，優先保留哪些部位？
"""

    return call_llm(SYSTEM, user_content, "portfolio_master")
