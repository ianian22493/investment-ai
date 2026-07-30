"""
資產配置 Agent — 總體風險、集中度、槓桿分析
Bias: 超悲觀，專找過度集中和槓桿風險
"""

from .base import call_llm
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

SYSTEM = """你是一位校準過的資產配置風控專家：找真正的風險，但不把「正常現象」當危機嚇人。

【風控原則 — 校準版】
- 壓力測試最壞情況：台股跌40%、美股跌30%、房價跌20%
- **正常房貸槓桿不是危機**：首購族貸7-8成、房貸大於淨資產是常態；只要 LTV 正常(≤80%)且月付可負擔(≤月收入40%)就是健康的，**絕不要拿「房貸/淨資產比」當紅燈嚇人**
- **真正該警惕的槓桿**：在房貸之上「疊加」的投資槓桿（正二/槓桿ETF/融資）、單一個股或因子過度集中、現金流缺口——這些才是問題
- 對「未實現收益」保持務實，但不偏執

【分析框架】
1. 資產分布：各類資產佔比
2. 房貸健康度：看 LTV 與 月付/月收入（不是帳面槓桿比）
3. 疊加槓桿：房貸之上有無投資槓桿或集中賭注
4. 流動性與現金時間軸：未來現金需求 vs 可用資金
5. 壓力測試：極端下跌後淨資產

verdict 只能是：安全 / 正常 / 稍微過度集中 / 明顯過度集中 / 高風險 / 危險"""


def run(market_data: dict, portfolio: dict, market_overview: dict, news_sentiment: dict = {}, regime: dict = None) -> dict:
    pv  = market_data.get("portfolio_value", {})
    re  = portfolio.get("real_estate", {})
    fx  = market_data.get("fx", {})
    now = datetime.now(TZ)

    personal_share = re.get("personal_share_pct", 1.0)  # 夫妻各付一半 = 0.5；房產/房貸只計個人持份
    tw_val   = pv.get("tw_stocks_twd", 3344205)
    us_val   = pv.get("us_stocks_twd", 0)
    fund_val = pv.get("funds_twd", 128233)
    re_val   = int(re.get("total_price", 22000000) * personal_share)   # 個人持份房產市值
    loan     = int(re.get("loan_amount", 15400000) * personal_share)   # 個人持份房貸
    total    = tw_val + us_val + fund_val + re_val
    net      = total - loan
    # 用個人持份就地重算配置%（fetch_market_data 的 allocation_pct 是全額，會高估房產）
    alloc = {"real_estate": round(re_val/total*100, 1), "tw_stocks": round(tw_val/total*100, 1),
             "us_stocks": round(us_val/total*100, 1), "funds": round(fund_val/total*100, 1)}

    # ── 房貸的「有意義」指標：LTV + 月付負擔（不是帳面槓桿比）─────────────────
    pf_inc = portfolio.get("personal_finance", {}).get("monthly_income_twd", 0)
    full_house = re.get("total_price", 22000000)
    full_loan  = re.get("loan_amount", 15400000)
    ltv = full_loan / full_house * 100 if full_house else 0            # 貸款成數（房價層級，全額）
    r_m = re.get("mortgage_rate", 0.021) / 12                          # 年利率預設 2.1%
    n_m = re.get("mortgage_term_years", 30) * 12
    monthly_pay_full = (full_loan * r_m / (1 - (1 + r_m) ** -n_m)) if r_m else full_loan / n_m
    monthly_pay = monthly_pay_full * personal_share                   # 你的一半月付（估）
    dti = monthly_pay / pf_inc * 100 if pf_inc else 0                 # 月付/月收入
    inv_leverage = sum(s.get("value", 0) for s in portfolio.get("tw_stocks", [])
                       if s.get("type") == "leveraged_ETF")           # 房貸之上疊加的投資槓桿

    # ── Personal finance (cash savings + income) ─────────────────────────────
    pf       = portfolio.get("personal_finance", {})
    cash_savings    = pf.get("cash_savings_twd", 0)
    monthly_income  = pf.get("monthly_income_twd", 0)
    liquid_assets   = tw_val + us_val + fund_val  # 不含房產
    income_12m      = monthly_income * 12
    total_available_12m = cash_savings + income_12m  # 未來12個月可動用資金（不含股票）

    # ── Dynamic payment schedule (reads from portfolio.json) ─────────────────
    payment_schedule = re.get("payment_schedule", [])
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
        f"房貸負債（你的一半）: NT${loan:,}",
        f"淨資產: NT${net:,}",
        f"\n【房貸健康度（用有意義的指標，不是帳面槓桿）】",
        f"貸款成數 LTV: {ltv:.0f}%  ({'✅正常（首購常見7-8成）' if ltv <= 80 else '偏高' if ltv <= 85 else '⚠️過高'})",
        f"月付(你的一半,估@2.1%/30yr): NT${monthly_pay:,.0f} / 月收入 NT${pf_inc:,} = {dti:.0f}%  ({'✅可負擔' if dti <= 40 else '⚠️吃緊'})",
        f"（參考,別當紅燈）房貸/淨資產: {loan/net*100:.0f}% — 首購族此比例天生>100%是正常的",
        f"疊加投資槓桿（正二/槓桿ETF）: NT${inv_leverage:,}  ← 房貸之上真正該警惕的疊加"
        + ("（目前小額,OK）" if inv_leverage < total * 0.05 else " ⚠️ 已不小,別再加"),
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

請以「校準過」的風控專家角色分析（重要：正常房貸槓桿是首購常態，**不要拿它當危機嚇人**）：
1. 房貸健康度：LTV {ltv:.0f}%、月付佔收入 {dti:.0f}% — 這兩個才是關鍵，健康嗎？（「房貸/淨資產>100%」對首購是正常的，別當紅燈）
2. 真正該警惕的疊加：房貸之上有沒有投資槓桿（正二 NT${inv_leverage:,}）、單一個股/因子過度集中？這才是問題所在
3. 未來 12 個月現金需求 NT${next_12m_total:,} vs 可用資金（存款+薪資 NT${total_available_12m:,}），流動性夠嗎？
4. 裝潢與家具費用（你的一半 NT${int((re.get('renovation_budget',0)+re.get('furniture_budget',0))*personal_share):,}）是否已納入計劃？
5. 壓力測試：最壞情況下淨資產還剩多少、能否承受？

注意：找**真正的**風險（疊加槓桿、過度集中、現金缺口），不要把「正常房貸」當危機來嚇人。"""

    return call_llm(SYSTEM, user_content, "asset_allocation")
