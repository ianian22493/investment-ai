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

pick 欄位：若空手觀望，name/code 填 "—"，entry_zone/stop_loss/target 填 "不適合進場"。
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
    "entry_zone": "建議觀察進場區間",
    "stop_loss": "停損條件",
    "target": "目標報酬空間",
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


def run(market_data: dict, portfolio: dict, market_overview: dict, news_sentiment: dict = {}) -> dict:
    indices = market_data.get("indices", {})
    fx = market_data.get("fx", {})
    news = market_data.get("news", {})

    lines = []
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

    user_content = "\n".join(lines) + """

請執行完整的七階段分析（PHASE 1-7），然後輸出今日台股隔日最佳短線候選股。

提醒：
- 從全部上市櫃股票中選，不限於特定持股
- 若今日市場環境不適合短線交易，請直接輸出「空手觀望」
- 交易策略要具體：進場區、停損位、目標
- 反方觀點必須提供
"""

    import json, re
    system_with_schema = SYSTEM + "\n\n" + PICK_SCHEMA
    from .base import client, MODEL
    for attempt in range(4):
        try:
            from google.genai import types
            resp = client.models.generate_content(
                model=MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_with_schema,
                ),
            )
            raw = resp.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = re.sub(r'\[\d+\]', '', raw)
            result = json.loads(raw)
            # Ensure pick key exists
            if "pick" not in result:
                result["pick"] = {"name": "—", "code": "—", "entry_zone": "—", "stop_loss": "—", "target": "—", "hold_days": "—"}
            return result
        except json.JSONDecodeError as e:
            return {
                "verdict": "ERROR", "confidence": 0,
                "summary": f"tw_daily_pick JSON parse error: {e}",
                "pick": {"name": "—", "code": "—", "entry_zone": "—", "stop_loss": "—", "target": "—", "hold_days": "—"},
                "risk_flags": ["解析失敗"], "agent_note": "parse error",
            }
        except Exception as e:
            import time
            err = str(e)
            if "429" in err and attempt < 3:
                wait = 15 * (attempt + 1)
                print(f"  [tw_daily_pick] 429 quota hit, waiting {wait}s (attempt {attempt+1}/4)...")
                time.sleep(wait)
                continue
            return {
                "verdict": "ERROR", "confidence": 0,
                "summary": f"tw_daily_pick error: {e}",
                "pick": {"name": "—", "code": "—", "entry_zone": "—", "stop_loss": "—", "target": "—", "hold_days": "—"},
                "risk_flags": [err], "agent_note": "API call failed",
            }
