"""
資產配置 Agent — 總體風險、集中度、槓桿分析
Bias: 超悲觀，專找過度集中和槓桿風險
"""

from .base import call_llm
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

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
5. 現金時間軸：未來 24 個月每筆現金需求清單

verdict 只能是：安全 / 稍微過度集中 / 明顯過度集中 / 高風險 / 危險"""


def run(market_data: dict, portfolio: dict, market_overview: dict, news_sentiment: dict = {}, regime: dict = None) -> dict:
    pv  = market_data.get("portfolio_value", {})
    re  = portfolio.get("real_estate", {})
    fx  = market_data.get("fx", {})
    now = datetime.now(TZ)

    tw_val   = pv.get("tw_stocks_twd", 3344205)
    us_val   = pv.get("us_stocks_twd", 0)
    fund_val = pv.get("funds_twd", 128233)
    re_val   = re.get("total_price", 22000000)
    loan     = re.get("loan_amount", 15400000)
    total    = tw_val + us_val + fund_val + re_val
    net      = total - loan
    alloc    = pv.get("allocation_pct", {})

    # ── Personal finance (cash savings + income) ─────────────────────────────
    pf       = portfolio.get("personal_finance", {})
    cash_savings    = pf.get("cash_savings_twd", 0)
    monthly_income  = pf.get("monthly_income_twd", 0)
    liquid_assets   = tw_val + us_val + fund_val  # 不含房產
    income_12m      = monthly_income * 12
    total_available_12m = cash_savings + income_12m  # 未來12個月可動用資金（不含股票）

    # ── Dynamic payment schedule (reads from portfolio.json) ─────────────────
    payment_schedule = re.get("payment_schedule", [])
    personal_share = re.get("personal_share_pct", 1.0)  # 夫妻各付一半時 = 0.5
    upcoming_total = sum(p["amount"] * personal_share for p in payment_schedule)
    next_12m_total = sum(
        p["amount"] * personal_share for p in payment_schedule
        if p.get("date", "9999") <= (now.replace(year=now.year + 1)).strftime("%Y-%m-%d")
    )

    lines = []
    if regime:
        lines.append(f"【市場體制】{regime['regime_summary']}")

    lines += [
        "【總資產快照（估算）】",
        f"房產（市值）: NT${re_val:,} ({alloc.get('real_estate','?')}%)  ← 最大部位",
        f"台股: NT${tw_val:,} ({alloc.get('tw_stocks','?')}%)",
        f"美股: NT${us_val:,} ({alloc.get('us_stocks','?')}%)",
        f"日幣基金: NT${fund_val:,} ({alloc.get('funds','?')}%)",
        f"總資產（毛額）: NT${total:,}",
        f"房貸負債: NT${loan:,}",
        f"淨資產: NT${net:,}",
        f"\n【槓桿分析】",
        f"房貸/淨資產比: {loan/net*100:.1f}%",
        f"房貸/總資產比: {loan/total*100:.1f}%",
        f"\n【現金需求時間軸（從 portfolio.json 動態讀取）】",
    ]

    for p in payment_schedule:
        months_away = ""
        try:
            pay_date = datetime.strptime(p["date"], "%Y-%m-%d").replace(tzinfo=TZ)
            diff = (pay_date - now).days
            months_away = f"（{diff//30}個月後）"
        except Exception:
            pass
        my_share = p["amount"] * personal_share
        lines.append(f"  {p['date']} {p['name']}: NT${my_share:,.0f} {months_away} [{p.get('category','?')}]（個人份額{personal_share*100:.0f}%）")

    lines += [
        f"  ──────────────────────────────",
        f"  未來12個月合計: NT${next_12m_total:,}",
        f"  全部現金需求合計: NT${upcoming_total:,}",
        f"\n【個人財務】",
        f"銀行現金存款: NT${cash_savings:,}",
        f"月收入（薪資）: NT${monthly_income:,}",
        f"未來12個月薪資合計: NT${income_12m:,}",
        f"12個月可用資金（存款+薪資）: NT${total_available_12m:,}",
        f"\n【流動性分析】",
        f"可快速變現投資資產（台股+美股+基金）: NT${liquid_assets:,}",
        f"12個月現金需求: NT${next_12m_total:,}",
        f"12個月資金缺口（需求 - 可用）: NT${next_12m_total - total_available_12m:,}"
        + (" ⚠️ 缺口！" if next_12m_total > total_available_12m else " ✅ 無缺口"),
        f"\n【壓力測試情境】",
        f"台股-40%後: NT${tw_val*0.6:,.0f}",
        f"美股-30%後: NT${us_val*0.7:,.0f}",
        f"房價-20%後: NT${re_val*0.8:,.0f}",
        f"壓力測試後淨資產: NT${tw_val*0.6 + us_val*0.7 + fund_val*0.9 + re_val*0.8 - loan:,.0f}",
        f"\n市場總覽: {market_overview.get('verdict')}",
    ]

    if news_sentiment.get("verdict") not in (None, "ERROR", "無資料"):
        lines.append(f"今日新聞情緒: {news_sentiment.get('verdict')} — {news_sentiment.get('summary','')[:100]}")

    tw_losers = [s for s in portfolio.get("tw_stocks", []) if s.get("pnl_pct", 0) < -10]
    if tw_losers:
        lines.append(f"\n【台股虧損部位】")
        for s in tw_losers:
            lines.append(f"  {s['name']}({s['code']}): {s['pnl_pct']:+.1f}% (NT${s['pnl']:+,})")

    user_content = "\n".join(lines) + f"""

請以偏執風控專家角色分析：
1. 房產是最大部位（{re_val/total*100:.0f}%），槓桿 {loan/net*100:.0f}%，這個風險水位如何？
2. 未來 12 個月現金需求 NT${next_12m_total:,} vs 可變現資產，流動性是否充足？
3. 裝潢與家具費用（NT${re.get('renovation_budget',0)+re.get('furniture_budget',0):,}）是否已納入計劃？
4. 台股三檔持續虧損是否影響整體配置健康度？
5. 壓力測試結果：最壞情況下淨資產還剩多少？能否承受？

注意：你的工作是找問題，不是給安慰。"""

    return call_llm(SYSTEM, user_content, "asset_allocation")
