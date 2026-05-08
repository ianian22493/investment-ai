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
    portfolio: dict = None,
) -> dict:
    lines = []

    # ── 直接注入現金和薪資數字（不依賴 LLM agent 的詮釋）────────────────────
    pf = (portfolio or {}).get("personal_finance", {})
    cash_savings   = pf.get("cash_savings_twd", 0)
    monthly_income = pf.get("monthly_income_twd", 0)
    income_12m     = monthly_income * 12

    re = (portfolio or {}).get("real_estate", {})
    payment_schedule = re.get("payment_schedule", [])
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    next_12m_obligations = sum(
        p["amount"] for p in payment_schedule
        if p.get("date", "9999") <= (now.replace(year=now.year + 1)).strftime("%Y-%m-%d")
    )
    cash_available_12m = cash_savings + income_12m
    cash_gap = next_12m_obligations - cash_available_12m

    lines += [
        "【個人財務（原始數字）】",
        f"銀行存款: NT${cash_savings:,}",
        f"月薪: NT${monthly_income:,} → 年薪資合計: NT${income_12m:,}",
        f"未來12個月現金需求（裝修/購屋款）: NT${next_12m_obligations:,}",
        f"12個月可用資金（存款+薪資）: NT${cash_available_12m:,}",
        f"資金缺口: NT${cash_gap:,} " + ("⚠️ 需動用投資資產" if cash_gap > 0 else "✅ 薪資可覆蓋"),
        "",
        "【重要背景】",
        "房地產為預售屋（自住用途），2026-12 交屋。",
        "房產占總資產比例高是結構性因素，不代表立即的流動性危機。",
        "評估 risk_level 時，請以「可投資流動資產」和「現金流壓力」為主要依據，",
        "不要因為房產占比高就自動給 high/extreme。",
    ]

    lines.append("\n【日幣基金 Agent】")
    lines.append(f"verdict={fx_fund.get('verdict')} confidence={fx_fund.get('confidence')}")
    lines.append(f"摘要：{fx_fund.get('summary','')[:150]}")

    lines.append(f"\n【資產配置 Agent（風控參考）】")
    lines.append(f"verdict={asset_allocation.get('verdict')} confidence={asset_allocation.get('confidence')}")
    lines.append(f"摘要：{asset_allocation.get('summary','')[:200]}")
    for flag in asset_allocation.get("risk_flags", [])[:3]:
        lines.append(f"  ⚠️ {flag}")

    user_content = "\n".join(lines) + f"""

作為 Wealth Desk 主管，根據以上原始數字評估：
1. 未來12個月現金流：薪資+存款 NT${cash_available_12m:,} vs 需求 NT${next_12m_obligations:,}，實際壓力如何？
2. 可投資流動資產（台股+美股+基金）的健康度如何？
3. 日幣資產的匯率風險？
4. 給出 risk_level（基於流動性和現金流，而非房產占比）

注意：房產占比高是預售屋購置的結構性現象，不是系統性財務危機。
只有當現金流真正無法覆蓋義務、或投資資產有系統性問題時，才給 high/extreme。
"""

    # Merge schema into system
    system_with_schema = SYSTEM + "\n\n額外輸出欄位：\n" + WEALTH_SCHEMA
    from .base import call_claude as _call
    return _call(system_with_schema, user_content, "wealth_master")
