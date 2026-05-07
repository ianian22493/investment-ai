"""
台股短線 Agent — 技術面 + 籌碼，激進偏見
Bias: 激進，追強勢股，果斷給出進出場點位
禁止: 不看基本面（EPS、本益比、ROE 等）
"""

from .base import call_claude
import json

SYSTEM = """你是一位激進的台股短線交易員，每天從技術面和籌碼面找操作機會。

【角色偏見 — 激進】
- 有機會就進場，寧可小賠停損，不要錯過強勢行情
- 不怕波動，只怕踏空
- 果斷：每個建議都要有具體價位，不給模糊答案

【絕對禁止】
- ❌ 不看基本面（EPS、本益比、ROE、財報、護城河）
- ❌ 不給「長期」建議
- ❌ 不說「請自行判斷」這類廢話

【分析框架】
1. 均線排列（MA5 > MA10 > MA20 = 多頭排列）
2. 成交量（量增價漲 = 有效突破）
3. 籌碼（三大法人合計買超 = 主力進場）
4. 強弱排名（今日漲跌幅排名）

【輸出格式要求】
recommendations 必須包含：
- 進場點位（entry price 或 entry zone）
- 停損點位（stop loss）
- 目標點位（target）
- 持倉期間（1-5天）

verdict 只能是：強力進攻 / 進攻 / 小幅進場 / 觀望 / 輕倉"""


def run(
    market_data: dict,
    portfolio: dict,
    market_overview: dict,
    news_sentiment: dict = {},
    regime: dict = None,
    candidates: list = None,
) -> dict:
    tw = market_data.get("tw_stocks", {})
    taiex = market_data.get("indices", {}).get("taiex", {})

    lines = []
    if regime:
        lines.append(f"【市場體制】{regime['regime_summary']}")
    lines.append(f"TAIEX今日: {taiex.get('close')} ({taiex.get('change_pct')}%)")
    lines.append("\n【持倉個股今日表現 + 籌碼 + 技術】")

    tw_holdings = portfolio.get("tw_stocks", [])
    for s in tw_holdings:
        code = s["code"]
        d = tw.get(code, {})
        inst = d.get("institutional", {})
        tech = d.get("technical", {})
        lines.append(
            f"\n{s['name']}({code}): 收{d.get('close','?')} "
            f"漲跌{d.get('change_pct','?')}% "
            f"量比{d.get('volume_ratio','?')}x | "
            f"外資{inst.get('foreign_net','?')} 投信{inst.get('trust_net','?')} 三大法人{inst.get('total_net','?')} | "
            f"MA5={tech.get('ma5','?')} MA20={tech.get('ma20','?')} 站上MA20:{tech.get('above_ma20','?')}"
        )

    lines.append(f"\n【市場總覽 Agent 方向】{market_overview.get('verdict')} — {market_overview.get('summary', '')[:80]}")
    if news_sentiment.get("verdict") not in (None, "ERROR", "無資料"):
        lines.append(f"【今日新聞情緒】{news_sentiment.get('verdict')} — {news_sentiment.get('summary','')[:100]}")
        for insight in news_sentiment.get("key_insights", [])[:2]:
            lines.append(f"  · {insight}")

    if candidates:
        lines.append("\n【量化掃描器精選（今日市場強勢候選股）】")
        for c in candidates[:8]:
            sigs = []
            if c.get("breakout_ma20"): sigs.append("MA20突破")
            if c.get("vol_surge"):     sigs.append(f"量爆{c.get('vol_ratio',0)}x")
            if c.get("rsi_zone"):      sigs.append(f"RSI{c.get('rsi',0)}")
            if c.get("ma_aligned"):    sigs.append("均線多排")
            if c.get("five_day_high"): sigs.append("5日高")
            lines.append(f"  {c['name']}({c['code']}) score={c['score']} | {' / '.join(sigs)}")

    user_content = "\n".join(lines) + """

請以短線技術交易員角色，分析以上資料：
1. 哪些標的今日技術面最強（均線、量價、籌碼最佳）？
2. 可以進場的標的是哪些？給出明確進場區間、停損、目標
3. 需要立即停損的是哪些？
4. 今日整體台股短線策略是什麼？

記住：你只看技術面和籌碼，不看基本面。給出具體數字，不要模糊。"""

    return call_claude(SYSTEM, user_content, "tw_short_term")
