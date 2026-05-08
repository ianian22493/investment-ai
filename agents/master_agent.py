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
    """
    CIO 降噪版本：只接受 signal_vector + desk master 摘要 + capital flow + scanner top3。
    禁止輸入：原始新聞、完整 agent summary、scanner reasoning 文字。
    """
    signal_vec   = all_outputs.get("signal_fusion", {})
    capital_flow = all_outputs.get("capital_flow", {})
    trading_desk = all_outputs.get("trading_master", {})
    portfolio_desk = all_outputs.get("portfolio_master", {})
    wealth_desk  = all_outputs.get("wealth_master", {})
    candidates   = all_outputs.get("_candidates", [])   # scanner top 3

    budget = capital_flow.get("budget", {})
    sections = []

    # ── Signal Vector（主要決策輸入）─────────────────────────────────────────
    if signal_vec and signal_vec.get("market_regime_score") is not None:
        sv = signal_vec
        sections.append(
            "【Signal Vector — 量化市場信號】\n"
            f"  market_regime_score : {sv.get('market_regime_score', 0):+.2f}  "
            f"trend_strength : {sv.get('trend_strength', 0.5):.2f}  "
            f"risk_pressure : {sv.get('risk_pressure', 0.5):.2f}\n"
            f"  volatility_risk     : {sv.get('volatility_risk', 0.5):.2f}  "
            f"confidence_score : {sv.get('confidence_score', 0.5):.2f}  "
            f"scanner_momentum : {sv.get('scanner_momentum', 0.5):.2f}\n"
            f"  ai_sector_strength  : {sv.get('ai_sector_strength', 0.5):.2f}  "
            f"（foreign_flow/breadth/liquidity: 待 batch2 資料）"
        )

    # ── Capital Flow — 硬性約束（不可更改）──────────────────────────────────
    flags_str = ", ".join(capital_flow.get("override_flags", [])[:3]) or "無"
    sections.append(
        "【Capital Flow Engine — 硬性資金預算】\n"
        f"  交易:{budget.get('trading',0)*100:.0f}%  "
        f"配置:{budget.get('portfolio',0)*100:.0f}%  "
        f"現金:{budget.get('cash',0)*100:.0f}%\n"
        f"  流向:{capital_flow.get('flow_direction','?')} | 觸發規則:{flags_str}"
    )

    # ── 三個 Desk Master 摘要（只取 verdict + confidence + summary[:80] + top2 recs）
    for icon, label, desk in [
        ("📈", "Trading Desk", trading_desk),
        ("📊", "Portfolio Desk", portfolio_desk),
        ("🏦", "Wealth Desk", wealth_desk),
    ]:
        recs = "; ".join(
            f"{r.get('action','')} {r.get('target','')}"
            for r in desk.get("recommendations", [])[:2]
        )
        liq = f" | liquidity={desk.get('liquidity_risk','?')}" if label == "Wealth Desk" else ""
        sections.append(
            f"【{icon} {label}】"
            f"verdict={desk.get('verdict','?')} conf={desk.get('confidence','?')}{liq}\n"
            f"  摘要: {desk.get('summary','')[:80]}\n"
            f"  建議: {recs or '無'}"
        )

    # ── Scanner Top 3（只顯示代碼 + score + 觸發信號，無文字 reasoning）────
    if candidates:
        cand_lines = []
        for c in candidates[:3]:
            sigs = [k for k in ("breakout_ma20", "vol_surge", "rsi_zone", "ma_aligned") if c.get(k)]
            cand_lines.append(
                f"  {c.get('name','?')}({c.get('code','?')}) "
                f"score={c.get('score',0)} signals={sigs}"
            )
        sections.append("【Scanner Top 3】\n" + "\n".join(cand_lines))

    user_content = "\n\n".join(sections) + """

作為 CIO，根據 Signal Vector 和 Desk 摘要做最終決策：

1. 在 Capital Flow 預算內，今日 3 個最優先可執行行動（具體股票/動作/urgency）
2. 今日最大單一風險
3. 一句話心態：今天怎麼面對市場

要求：果斷具體，按時間軸分開，短線與長線不混談。
"""

    return call_claude(SYSTEM, user_content, "master_agent")
