"""
Trading Desk Master — 整合台股交易信號（波段版）
輸入：tw_short_term（技術面觀點）, tw_daily_pick（盤後波段精選）, scanner candidates
決策時間軸：10-22 個交易日（2026-07-17 起波段策略）
"""

from .base import call_claude

SYSTEM = """你是 Trading Desk Risk Committee，負責整合波段交易信號並提供客觀的風險觀點。
（系統為 10-22 個交易日的波段策略，不是隔日沖——評估信號時用「未來一個月」的尺度。）

【你的職責 — 嚴格限制】
你只做一件事：把 Trading Desk 的多個信號整合成一個清晰的觀點。
你「不能」做資金分配、不能說「應該投入幾%」、不能 override 其他 desk。

【HARD RULE — 不可違反】
❌ 禁止：「建議將交易部位調整至 X%」
❌ 禁止：「應減碼至...」「加碼到...」任何比例建議
❌ 禁止：做任何資本配置決定
✅ 只輸出：信號強度、方向、風險觀點、建議行動方向（BUY/SELL/HOLD/WAIT）

【你只回答三個問題】
1. 波段信號是否一致？一致性高低？
2. 今日有沒有具體的進出場機會？（股票名稱、方向、理由）
3. 這個 desk 對 CIO 的建議：trading risk 是 high / medium / low？

verdict 只能是：信號強烈 / 信號偏多 / 信號中性 / 信號偏空 / 信號混亂
"""


def run(
    tw_short_term: dict,
    tw_daily_pick: dict = None,
    regime: dict = None,
    capital_flow: dict = None,
) -> dict:
    lines = []

    if regime:
        lines.append(f"【體制】{regime.get('regime_summary', '')}")

    # Capital flow constraint
    if capital_flow:
        budget = capital_flow.get("trading_risk_budget", "normal")
        weight = capital_flow.get("trading_confidence_weight", 1.0)
        lines.append(f"【資金層指令】交易額度={budget} 信號權重={weight:.1f}x")
        flags = capital_flow.get("override_flags", [])
        if flags:
            lines.append(f"  覆蓋標記：{', '.join(flags)}")

    lines.append("\n【台股短線 Agent】")
    lines.append(f"verdict={tw_short_term.get('verdict')} confidence={tw_short_term.get('confidence')}")
    lines.append(f"摘要：{tw_short_term.get('summary', '')[:150]}")
    for rec in tw_short_term.get("recommendations", [])[:3]:
        lines.append(f"  [{rec.get('urgency')}] {rec.get('action')} {rec.get('target')}: {rec.get('detail','')[:60]}")

    if tw_daily_pick and tw_daily_pick.get("verdict") not in (None, "ERROR"):
        pick = tw_daily_pick.get("pick", {})
        lines.append(f"\n【每日精選 Agent（盤後）】")
        lines.append(f"verdict={tw_daily_pick.get('verdict')} confidence={tw_daily_pick.get('confidence')}")
        lines.append(f"推薦：{pick.get('name','?')}({pick.get('code','?')}) "
                     f"進場={pick.get('entry_zone','?')} 停損={pick.get('stop_loss','?')}")
        lines.append(f"體制：{tw_daily_pick.get('market_regime','?')} | {tw_daily_pick.get('summary','')[:100]}")

    user_content = "\n".join(lines) + """

作為 Trading Desk 主管，整合以上信號：
1. 兩個 agent 方向是否一致？
2. 今日波段操作的整體建議是什麼？
3. 最優先的 1-2 個操作機會（若有）
4. 若資金層有降權指令，如何調整部位大小？
"""

    return call_claude(SYSTEM, user_content, "trading_master")
