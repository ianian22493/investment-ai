"""
資產配置 Agent — 總體風險、集中度、槓桿分析
Bias: 超悲觀，專找過度集中和槓桿風險
"""

from .base import call_claude

SYSTEM = """你是一位偏執的資產配置風控專家，你的工作就是找出資產組合的潛在危機。

【角色偏見 — 超悲觀風控】
- 假設最壞情況：台股跌40%、美股跌30%、房價跌20% 會怎樣？
- 對槓桿（房貸）極度警惕
- 認為集中度超過50%就是風險
- 對「未實現收益」非常不信任

【分析框架】
1. 資產分布：房產/台股/美股/基金/現金各佔多少？
2. 槓桿分析：房貸對比淨資產比例
3. 壓力測試：若各類資產同時下跌，淨資產剩多少？
4. 流動性：緊急時能快速變現的資產有多少？
5. 集中度：有沒有單一標的過大？

【已知持倉】
- 房產：久泰宸品 2200萬（含貸款1540萬），2026-12 交屋
  → 有外牆款 66萬（2026-07），竣工款 88萬（2026-10）
- 台股：334萬（11檔，宏普/欣陸/玖鼎虧損中）
- 美股：14檔
- 基金：12.8萬（日幣）

verdict 只能是：安全 / 稍微過度集中 / 明顯過度集中 / 高風險 / 危險"""


def run(market_data: dict, portfolio: dict, market_overview: dict) -> dict:
    pv = market_data.get("portfolio_value", {})
    re = portfolio.get("real_estate", {})
    fx = market_data.get("fx", {})

    usd_twd = fx.get("usd_twd", 32)

    tw_val = pv.get("tw_stocks_twd", 3344205)
    us_val = pv.get("us_stocks_twd", 0)
    fund_val = pv.get("funds_twd", 128233)
    re_val = re.get("total_price", 22000000)
    loan = re.get("loan_amount", 15400000)
    total = tw_val + us_val + fund_val + re_val
    net = total - loan

    alloc = pv.get("allocation_pct", {})

    # 計算近期現金需求
    next_payment = re.get("next_payment", {})
    coming_payments = re.get("down_payment_total", 0) - re.get("down_payment_paid", 0)

    lines = [
        "【總資產快照（估算）】",
        f"台股: NT${tw_val:,} ({alloc.get('tw_stocks','?')}%)",
        f"美股: NT${us_val:,} ({alloc.get('us_stocks','?')}%)",
        f"日幣基金: NT${fund_val:,} ({alloc.get('funds','?')}%)",
        f"房產（市值）: NT${re_val:,} ({alloc.get('real_estate','?')}%)",
        f"總資產（毛額）: NT${total:,}",
        f"房貸負債: NT${loan:,}",
        f"淨資產: NT${net:,}",
        f"\n【槓桿分析】",
        f"房貸/淨資產比: {loan/net*100:.1f}%",
        f"房貸/總資產比: {loan/total*100:.1f}%",
        f"\n【即將現金需求】",
        f"外牆款 2026-07: NT$660,000",
        f"竣工款 2026-10: NT$880,000",
        f"合計: NT$1,540,000",
        f"\n【壓力測試情境】",
        f"台股-40%: {tw_val*0.6:,.0f}",
        f"美股-30%: {us_val*0.7:,.0f}",
        f"壓力測試後淨資產: {tw_val*0.6 + us_val*0.7 + fund_val*0.9 + re_val*0.8 - loan:,.0f}",
        f"\n市場總覽: {market_overview.get('verdict')}",
    ]

    user_content = "\n".join(lines) + """

請以偏執風控專家角色分析：
1. 目前資產配置最大的風險是什麼？
2. 房產槓桿（貸款 1540 萬）對整體財務的影響？
3. 近期現金需求（146 萬）是否造成流動性壓力？
4. 台股三檔虧損（-19%~-42%）是否影響整體風險？
5. 整體而言，目前風險水位是否在可接受範圍？

注意：你的工作是找問題，不是給安慰。"""

    return call_claude(SYSTEM, user_content, "asset_allocation")
