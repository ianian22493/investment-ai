"""
Master Agent — CIO 層，整合三個 Desk + Capital Flow，給出最終每日策略
"""

from .base import call_claude

SYSTEM = """你是投資委員會的 CIO（首席投資官）。你是系統中唯一有權做最終決策的角色。

【你收到的輸入是什麼】
- 三個 Desk Risk Committee 的「信號和風險觀點」（不是決策）
- Capital Flow Engine 的「資金預算分配」（已由規則引擎決定，你不能更改數字）
- 反對派的質疑

【HARD RULE — 你的決策邊界】
✅ 你的工作：在 Capital Flow 設定的預算內，做最優的跨 Desk 決策
❌ 你不能：重新分配資金比例（那是 Capital Flow Engine 的工作）
❌ 你不能：被某個 Desk 的「建議」直接帶走，Desk 提供觀點，你做決定

【Capital Flow 預算是硬約束，不是建議】
例如：Capital Flow 說 trading_budget=0.05
→ 即使 Trading Desk 信號強烈，你的交易建議也只能在 5% 預算內
→ 不能說「但信號很好所以應該多做一點」

【衝突解決規則】
1. Desk 之間的觀點衝突 → 你仲裁
2. Desk 觀點 vs Capital Flow 預算衝突 → Capital Flow 優先
3. 短線 vs 長線 → 分開處理，不互相影響

【輸出格式】
- summary 分三段：短線 / 持倉 / 財富，各一句話
- 整體 verdict：全面進攻 / 局部進攻 / 維持現狀 / 局部防守 / 全面防守
"""


def run(all_outputs: dict) -> dict:
    capital_flow = all_outputs.get("capital_flow", {})
    trading_desk = all_outputs.get("trading_master", {})
    portfolio_desk = all_outputs.get("portfolio_master", {})
    wealth_desk = all_outputs.get("wealth_master", {})
    devils = all_outputs.get("devils_advocate", {})
    reflection = all_outputs.get("reflection", {})

    sections = []

    # Capital Flow (最高優先，硬約束)
    if capital_flow:
        budget = capital_flow.get("budget", {})
        flags = capital_flow.get("override_flags", [])
        sections.append(
            f"【⚡ Capital Flow Engine — 硬性資金預算（不可更改）】\n"
            f"  交易預算：{budget.get('trading', 0)*100:.0f}%  "
            f"配置預算：{budget.get('portfolio', 0)*100:.0f}%  "
            f"現金：{budget.get('cash', 0)*100:.0f}%\n"
            f"  流向：{capital_flow.get('flow_direction','?')}\n"
            f"  觸發規則：\n"
            + ("\n".join(f"    ⚡ {f}" for f in flags) if flags else "    （無）")
        )

    # Trading Desk（信號觀點，非決策）
    budget = capital_flow.get("budget", {}) if capital_flow else {}
    sections.append(
        f"【📈 Trading Desk 信號（可用預算 {budget.get('trading',0)*100:.0f}%）】\n"
        f"  信號：{trading_desk.get('verdict','?')} | confidence={trading_desk.get('confidence','?')}\n"
        f"  {trading_desk.get('summary','')[:180]}\n"
        f"  具體機會：{'; '.join(r.get('action','?')+' '+r.get('target','?') for r in trading_desk.get('recommendations',[])[:2])}"
    )

    # Portfolio Desk（信號觀點，非決策）
    sections.append(
        f"【📊 Portfolio Desk 信號（可用預算 {budget.get('portfolio',0)*100:.0f}%）】\n"
        f"  觀點：{portfolio_desk.get('verdict','?')} | confidence={portfolio_desk.get('confidence','?')}\n"
        f"  {portfolio_desk.get('summary','')[:180]}\n"
        f"  關注點：{'; '.join(r.get('action','?')+' '+r.get('target','?') for r in portfolio_desk.get('recommendations',[])[:2])}"
    )

    # Wealth Desk（風險事實，非決策）
    wealth_risk = wealth_desk.get("risk_level", "?")
    sections.append(
        f"【🏦 Wealth Desk 風險事實（觸發 Capital Flow: {'是' if capital_flow and capital_flow.get('wealth_risk_triggered') else '否'}）】\n"
        f"  財務狀態：{wealth_desk.get('verdict','?')} | risk_level={wealth_risk}\n"
        f"  {wealth_desk.get('summary','')[:180]}\n"
        f"  現金壓力：{'⚠️ 是' if wealth_desk.get('cash_crunch_risk') else '無'} | "
        f"槓桿：{wealth_desk.get('leverage_health','?')}"
    )

    # Devil's Advocate
    sections.append(
        f"【🔴 反對派】\n"
        f"  verdict={devils.get('verdict','?')}\n"
        f"  {devils.get('summary','')[:150]}"
    )

    # Recent reflection if available
    if reflection and reflection.get("verdict") not in (None, "ERROR"):
        sections.append(
            f"【🔄 系統反思】\n"
            f"  {reflection.get('verdict','?')} — {reflection.get('summary','')[:100]}"
        )

    user_content = "\n\n".join(sections) + """

作為 CIO，給出今日最終決策：

1. 三個 Desk 各自結論（短線 / 持倉 / 財富）
2. Capital Flow 指令是否影響了今日操作重點？
3. 今日最優先的 3 個可執行行動
4. 本週最大單一風險
5. 一句話給又瑄：今天的心態

要求：清晰、果斷、按時間軸分開。不要把短線建議和長線建議混在一起說。
"""

    return call_claude(SYSTEM, user_content, "master_agent")
