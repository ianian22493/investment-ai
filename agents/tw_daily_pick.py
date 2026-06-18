"""
台股每日盤後短線精選 Agent
每日收盤後執行，從全台股中找出隔日最佳短線機會。
"""

from .base import call_claude

SYSTEM = """你是一位機構級（Institutional Grade）的台股短線交易研究 AI。

你的角色是：「台股短線交易基金的首席研究員（Head of Tactical Trading Intelligence）」

你的任務：透過消息面、技術面、籌碼面、資金流、市場結構、風險環境與跨市場連動，
找出「隔日最可能獲得資金持續流入、具備超額報酬機率的台股標的。」

# 核心原則

1. 資金流優先於消息。市場不是看誰有好消息，而是誰能吸引明天的資金。
2. 市場主流優先。不要逆勢操作。若主流在 AI/PCB/散熱/ASIC，不要推薦弱勢族群。
3. 市場環境決定勝率。判斷目前是：趨勢盤/區間盤/高檔震盪/主升段/空頭反彈/資金輪動/恐慌盤。
4. 不是預言家。不保證上漲，只提高勝率與風報比。
5. 若市場不利短線，直接輸出「空手觀望」，不強迫推薦。

# 分析流程（必須完整執行）

PHASE 1 — 美股環境：NASDAQ、SOXX、NVIDIA、TSM ADR、VIX、美債、DXY
PHASE 2 — 台股市場 Regime：加權指數、成交量、漲跌家數、強弱結構、市場是否過熱
PHASE 3 — 族群輪動：AI Server/散熱/ASIC/CPO/PCB/半導體/重電/軍工/機器人/生技/航運
PHASE 4 — 消息催化：公司新聞、法說、財報、產業催化劑（重要：市場會不會買單，而非新聞本身）
PHASE 5 — 技術結構：趨勢、量價、Momentum（RSI/KD/MACD）、明天是否還有追價動能
PHASE 6 — 籌碼面：外資/投信/自營、融資、當沖比、籌碼沉澱 vs 高檔出貨
PHASE 7 — 風險引擎（最高優先）：若風險過高則降低信心或放棄推薦

# 最終決策

只選出「隔日風報比最佳的一檔股票」，不是最熱門也不是漲最多，
而是「隔日最可能出現資金延續性的股票」。

允許「空手觀望」——空手也是策略，不要每天硬推薦。

# 輸出規則（極重要）

你的輸出必須是 JSON。分析風格採 Professional Trading Desk / Hedge Fund Memo，
不要像新聞稿、投顧老師或散戶論壇。

## 價格欄位規範（極嚴格 — 必須是可執行的具體數字）

當 verdict 為「推薦出手」或「謹慎試單」時，下列三個欄位必須是**明確、可執行的價格數字**，
不能是文字敘述。使用者會直接拿去下單。

- entry_zone：必須給**具體價格區間**（例：「138.5-142.0」或「破144 突破追進」），
              不能寫「逢低買進」「適度試單」這種模糊話術。
- stop_loss：必須給**具體停損價**（例：「135.0」或「跌破134.5」），
             不能寫「嚴設停損」「適度停損」。
- target：必須給**具體目標價**（例：「152」或「+8% (~150)」），
          不能寫「逢高停利」「擇機獲利」。
- risk_reward：用 entry / stop_loss / target 算出風報比（例：「1:2.5」「1:3」），
               若無法計算填 "—"。

當 verdict 為「空手觀望」：entry_zone/stop_loss/target/risk_reward 全填 "—"。

pick 欄位：若空手觀望，name/code 填 "—"。
verdict 只能是：推薦出手 / 謹慎試單 / 空手觀望
"""

PICK_SCHEMA = """
輸出格式（strict JSON）：
{
  "verdict": "推薦出手 / 謹慎試單 / 空手觀望",
  "confidence": 0.0-1.0,
  "pick": {
    "name": "股票名稱",
    "code": "股票代號（4-5碼）",
    "entry_zone": "具體進場價格區間，例如：138.5-142.0",
    "stop_loss": "具體停損價，例如：135.0",
    "target": "具體目標價，例如：152.0",
    "risk_reward": "風報比，例如：1:2.5",
    "ref_close": "今日收盤參考價，例如：140.5",
    "hold_days": "預計持有1-3天"
  },
  "market_regime": "一句話描述目前市場狀態",
  "risk_appetite": "偏多/中性/偏空",
  "suitable_for_trading": true或false,
  "summary": "3-4句 hedge fund memo 風格總結（繁體中文）",
  "core_logic": {
    "capital_flow": "資金流分析（1-2句）",
    "sector": "今日主流族群（1-2句）",
    "catalyst": "消息催化（1-2句）",
    "technical": "技術結構（1-2句）",
    "chips": "籌碼結構（1-2句）",
    "us_linkage": "美股連動（1-2句）"
  },
  "risk_flags": ["主要風險1", "主要風險2", "失敗情境"],
  "counter_argument": "為什麼這筆交易可能失敗（1-2句）",
  "agent_note": "bias disclosure"
}
"""


def run(
    market_data: dict,
    portfolio: dict,
    market_overview: dict,
    news_sentiment: dict = {},
    regime: dict = None,
    candidates: list = None,
) -> dict:
    indices = market_data.get("indices", {})
    fx = market_data.get("fx", {})
    news = market_data.get("news", {})

    lines = []
    if regime:
        lines.append(f"【市場體制引擎】{regime['regime_summary']}")
        lines.append(f"  短線適合交易：{'是' if regime.get('short_term_trading_favorable') else '否'}｜風險等級：{regime.get('risk_level','?')}")

    # Inject historical performance context
    perf_context = market_data.get("performance_context", "")
    if perf_context:
        lines.append(f"\n{perf_context}")

    # Inject latest reflection
    latest_reflection = market_data.get("latest_reflection")
    if latest_reflection:
        findings = []
        try:
            import json as _json
            findings = _json.loads(latest_reflection.get("key_findings", "[]"))
        except Exception:
            pass
        if findings:
            lines.append(f"\n【前次反思重點】")
            for f in findings[:3]:
                lines.append(f"  · {f}")

    lines.append("【今日市場數據】")
    lines.append(f"台股加權指數: {indices.get('taiex',{}).get('close','?')} ({indices.get('taiex',{}).get('change_pct','?')}%)")
    lines.append(f"NASDAQ: {indices.get('nasdaq',{}).get('close','?')} ({indices.get('nasdaq',{}).get('change_pct','?')}%)")
    lines.append(f"S&P500: {indices.get('sp500',{}).get('close','?')} ({indices.get('sp500',{}).get('change_pct','?')}%)")
    lines.append(f"VIX: {indices.get('vix',{}).get('close','?')}")
    lines.append(f"USD/TWD: {fx.get('usd_twd','?')}")

    lines.append("\n【台股今日新聞（前12則）】")
    for h in news.get("tw", [])[:12]:
        lines.append(f"  · {h}")

    lines.append("\n【美股今日新聞（前8則）】")
    for h in news.get("us", [])[:8]:
        lines.append(f"  · {h}")

    lines.append(f"\n【大盤總覽 Agent 判斷】{market_overview.get('verdict','?')} — {market_overview.get('summary','')[:120]}")
    if news_sentiment.get("verdict") not in (None, "ERROR"):
        lines.append(f"【新聞情緒 Agent】{news_sentiment.get('verdict','?')} — {news_sentiment.get('summary','')[:100]}")
        for ins in news_sentiment.get("key_insights", [])[:3]:
            lines.append(f"  · {ins}")

    if candidates:
        lines.append("\n【量化掃描器精選（今日技術強勢股，供參考）】")
        for c in candidates[:10]:
            sigs = []
            if c.get("breakout_ma20"): sigs.append("MA20突破")
            if c.get("vol_surge"):     sigs.append(f"量爆{c.get('vol_ratio',0)}x")
            if c.get("rsi_zone"):      sigs.append(f"RSI{c.get('rsi',0)}")
            if c.get("ma_aligned"):    sigs.append("均線多排")
            if c.get("five_day_high"): sigs.append("5日高")
            lines.append(
                f"  {c['name']}({c['code']}) 收{c.get('last_close','?')} "
                f"score={c['score']} | {' / '.join(sigs)}"
            )

    user_content = "\n".join(lines) + """

請執行完整的七階段分析（PHASE 1-7），然後輸出今日台股隔日最佳短線候選股。

提醒：
- 從全部上市櫃股票中選，不限於特定持股
- 若今日市場環境不適合短線交易，請直接輸出「空手觀望」
- 交易策略要具體：**entry_zone / stop_loss / target 必須是具體可下單的價格**（例：138.5-142、135、152）
  · 不接受模糊敘述（「逢低買進」「適度停損」「逢高停利」一律不行）
  · 必須給今日 ref_close（收盤價）作為參考錨點
  · 必須算出 risk_reward（用 entry/stop/target 反推）
- 反方觀點必須提供
"""

    # Delegate to call_claude — inherits multi-key rotation, prompt cache,
    # error log, and quota-degradation fallback. custom_schema=True tells
    # base.py the schema is already embedded in system_with_schema below.
    system_with_schema = SYSTEM + "\n\n" + PICK_SCHEMA
    result = call_claude(system_with_schema, user_content, "tw_daily_pick", custom_schema=True)
    # Ensure pick key exists even on degraded/error paths
    if "pick" not in result:
        result["pick"] = {"name": "—", "code": "—", "entry_zone": "—",
                          "stop_loss": "—", "target": "—",
                          "risk_reward": "—", "ref_close": "—", "hold_days": "—"}
    return result
