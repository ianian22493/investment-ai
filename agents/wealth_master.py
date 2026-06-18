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

【三個獨立維度（必須分開評估）】
1. structural_risk（結構性風險）= 房產佔比、槓桿比例
   → 這是長期結構，不影響短線交易。low/moderate/high
   → 預售屋是已決定的事實，不要因此提高 liquidity_risk

2. liquidity_risk（流動性風險）= 現金+薪資 能否覆蓋近期義務
   → 這才是 Capital Flow Engine 使用的主要輸入
   → low/moderate/elevated/high/extreme
   → 只有當薪資+存款真的無法覆蓋義務時，才給 high/extreme

3. cashflow_stability（現金流穩定度）= 薪資收入穩定性、缺口大小
   → stable（無缺口）/ adequate（小缺口但可管理）/ tight（需動用部分投資）/ critical（嚴重缺口）

verdict 只能是：穩健 / 留意 / 警戒 / 危險
"""

WEALTH_SCHEMA = """
你的輸出除了標準 JSON 外，還需要包含：
{
  ...標準欄位...,
  "structural_risk": "low / moderate / high",
  "liquidity_risk": "low / moderate / elevated / high / extreme",
  "cashflow_stability": "stable / adequate / tight / critical",
  "risk_level": "等於 liquidity_risk 的值（向後相容）",
  "cash_crunch_risk": true或false（流動性是否真的緊繃）,
  "upcoming_obligations": ["2026-07 外牆款 66萬", "2026-10 竣工款 88萬"],
  "leverage_health": "健康 / 偏高 / 危險"
}

重要：
- structural_risk 高（房子佔比大）不代表 liquidity_risk 高
- risk_level 必須等於 liquidity_risk（Capital Flow Engine 用這個欄位）
- 只有現金流真的缺口才給 liquidity_risk=high/extreme

【cash_crunch_risk 嚴格定義 — 不要憑直覺】
這個欄位由 capital_flow.py 用來判斷是否要鎖死 trading 預算。判斷錯
會讓系統連續 N 天空手且錯失機會。請按以下硬性規則：

  cash_gap = 未來12個月需求 - (現金 + 12個月薪資)

  cash_gap <= 0       → cash_crunch_risk = false（薪資+現金可覆蓋）
  cash_gap > 0 但 <= 30%×可用資金 → cash_crunch_risk = false（小缺口，可動用部分投資不算 crunch）
  cash_gap > 30%×可用資金 → cash_crunch_risk = true（真正壓力）

注意：cash_gap 是上方輸入區塊已經算好的數字。若是負數，**禁止**因為「房產款 1.83M
看起來很大」就給 cash_crunch_risk=true。看 cash_gap 的正負，不看絕對金額大小。
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
    personal_share = re.get("personal_share_pct", 1.0)  # 夫妻共同負擔時 = 0.5
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    next_12m_obligations = sum(
        p["amount"] * personal_share for p in payment_schedule
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

    # Compute cash_crunch_risk deterministically — don't let LLM guess
    # this. The 30% threshold means "needs to liquidate < 30% of liquid
    # investments to plug the gap" = not a crunch.
    investable_estimate = cash_available_12m  # cash + 1y salary as base
    if cash_gap <= 0:
        cash_crunch_status = "false（cash_gap <= 0，薪資+存款已可覆蓋全部）"
    elif cash_gap <= 0.30 * investable_estimate:
        cash_crunch_status = f"false（cash_gap ${cash_gap:,} 僅 {cash_gap/investable_estimate*100:.1f}% 可用資金，<30% 閾值）"
    else:
        cash_crunch_status = f"true（cash_gap ${cash_gap:,} 超過 30% 可用資金）"

    user_content = "\n".join(lines) + f"""

【cash_crunch_risk 必須這樣判（規則已預算好，請照填）】
本期 cash_gap = NT${cash_gap:,}
本期 可用資金 = NT${investable_estimate:,}
依規則 → cash_crunch_risk 應該 = {cash_crunch_status}

作為 Wealth Desk 主管，根據以上原始數字評估：
1. 未來12個月現金流：薪資+存款 NT${cash_available_12m:,} vs 需求 NT${next_12m_obligations:,}，實際壓力如何？
2. 可投資流動資產（台股+美股+基金）的健康度如何？
3. 日幣資產的匯率風險？
4. 給出 risk_level（基於流動性和現金流，而非房產占比）
5. cash_crunch_risk 必須照上方規則計算，不要憑直覺判斷

注意：房產占比高是預售屋購置的結構性現象，不是系統性財務危機。
只有當現金流真正無法覆蓋義務、或投資資產有系統性問題時，才給 high/extreme。
"""

    # Merge schema into system
    system_with_schema = SYSTEM + "\n\n額外輸出欄位：\n" + WEALTH_SCHEMA
    from .base import call_claude as _call
    result = _call(system_with_schema, user_content, "wealth_master")

    # ── HARD POST-PROCESS: override cash_crunch_risk with deterministic
    # rule. LLM was repeatedly giving cash_crunch_risk=True even with
    # cash_gap = -555K (clear surplus) because 1.83M of obligations
    # "looks scary". Math is math — bypass the LLM on this field entirely.
    if cash_gap <= 0:
        rule_says = False
    elif cash_gap <= 0.30 * cash_available_12m:
        rule_says = False  # small gap, manageable by partial liquidation
    else:
        rule_says = True

    llm_said = result.get("cash_crunch_risk")
    if llm_said != rule_says:
        print(f"  [wealth_master] cash_crunch_risk override: "
              f"LLM said {llm_said}, rule says {rule_says} "
              f"(cash_gap={cash_gap:,} vs {cash_available_12m:,} available)")
        result["cash_crunch_risk"] = rule_says
        # Append a note so the audit trail is visible in agent_note
        existing = result.get("agent_note", "") or ""
        result["agent_note"] = (
            f"[cash_crunch_risk overridden to {rule_says} by deterministic rule] "
            + existing
        )
    return result
