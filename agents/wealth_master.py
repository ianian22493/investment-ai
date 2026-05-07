"""
Wealth Desk Master — 整合財富管理與風控
輸入：fx_fund, asset_allocation
決策時間軸：季～年
"""

from .base import call_claude

SYSTEM = """你是 Wealth Desk Risk Committee，負責評估整體財務健康並提供風險訊號給 Capital Flow Engine。

【你的職責 — 嚴格限制】
你只做一件事：提供財富層的風險評估，作為 Capital Flow Engine 的輸入。
你「不能」決定資金如何分配、不能直接 override Trading 或 Portfolio Desk。

【HARD RULE — 不可違反】
❌ 禁止：「應將資金從股票移到現金」（這是 Capital Flow Engine 的決定）
❌ 禁止：任何針對其他 desk 的直接指令
✅ 你的輸出是「事實描述 + 風險等級」，不是指令

【你必須回答的三件事】
1. 目前財務系統的槓桿/流動性狀態（事實）
2. 未來 12 個月最大的現金流壓力點（事實）
3. 綜合風險等級（供 Capital Flow Engine 使用）：
   risk_level = low / moderate / elevated / high / extreme

verdict 只能是：穩健 / 留意 / 警戒 / 危險
"""

WEALTH_SCHEMA = """
你的輸出除了標準 JSON 外，還需要包含：
{
  ...標準欄位...,
  "risk_level": "low / moderate / elevated / high / extreme",
  "cash_crunch_risk": true或false,
  "upcoming_obligations": ["2026-07 外牆款 66萬", "2026-10 竣工款 88萬"],
  "leverage_health": "健康 / 偏高 / 危險"
}
"""


def run(
    fx_fund: dict,
    asset_allocation: dict,
) -> dict:
    lines = []

    lines.append("【日幣基金 Agent】")
    lines.append(f"verdict={fx_fund.get('verdict')} confidence={fx_fund.get('confidence')}")
    lines.append(f"摘要：{fx_fund.get('summary','')[:150]}")
    for rec in fx_fund.get("recommendations", [])[:2]:
        lines.append(f"  [{rec.get('urgency')}] {rec.get('action')} {rec.get('target')}: {rec.get('detail','')[:60]}")

    lines.append(f"\n【資產配置 Agent（風控）】")
    lines.append(f"verdict={asset_allocation.get('verdict')} confidence={asset_allocation.get('confidence')}")
    lines.append(f"摘要：{asset_allocation.get('summary','')[:200]}")
    for flag in asset_allocation.get("risk_flags", [])[:3]:
        lines.append(f"  ⚠️ {flag}")
    for rec in asset_allocation.get("recommendations", [])[:3]:
        lines.append(f"  [{rec.get('urgency')}] {rec.get('action')} {rec.get('target')}: {rec.get('detail','')[:60]}")

    user_content = "\n".join(lines) + """

作為 Wealth Desk 主管，整合以上分析：
1. 目前整體財務的最大風險是什麼？
2. 未來 12 個月的現金流是否有壓力點？
3. 日幣資產對整體配置的影響？
4. 給出 risk_level（這個值會被用來調整 Trading Desk 的操作額度）

注意：你的 risk_level 判斷必須謹慎但不過度保守。只有真正的系統性風險才應觸發 high/extreme。
"""

    # Merge schema into system
    system_with_schema = SYSTEM + "\n\n額外輸出欄位：\n" + WEALTH_SCHEMA
    from .base import call_claude as _call
    return _call(system_with_schema, user_content, "wealth_master")
