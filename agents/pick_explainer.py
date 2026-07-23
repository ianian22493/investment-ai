"""
Pick Explainer — 為 /picks/YYYY-MM-DD.html 詳細頁產出結構化分析 JSON

讀取已經跑完的 tw_daily_pick / devils_advocate / signal_fusion / regime / candidates，
產出 dashboard 上塞不下的「深度分析」內容：context paragraph、entry/stop/target/hold
四個 rationale、5-8 個 risk scenarios、5-6 步驟 execution checklist。

兩種模式：
  PICK mode  — 今日有出手，輸出完整 pick-day schema
  WATCH mode — 今日觀望，輸出 watch-day schema（why_watching / reactivation_triggers / scanner_top1 assessment）

設計原則：
- 文字「自含上下文」：3 個月後 Yuzu 自己回頭看也要看得懂
- 不要重複 dashboard 已有的 verdict / confidence 數字；只補 reasoning
- Rationale 要具體：用數字、用價位、用「為什麼是這個」而非泛詞
- Risk scenarios 用「臨床決策規則」格式：trigger → action → severity → rationale
"""

import json
import sys
import os

# Make project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .base import call_llm


PICK_SYSTEM = """你是 Yuzu Capital OS 的「Pick Explainer」—— 把今日盤後 AI 委員會的
波段決策（持有 10-22 個交易日），擴寫成一份完整的「臨床決策日誌」風投資紀錄。

讀者是 Yuzu 本人（兒科住院醫師 R3），未來會在診間夾縫 3 分鐘讀完。
她無法盯盤：進場後最多每天收盤看一次，主要靠預掛停損單管理風險。

【寫作風格】
- 第一人稱的研究員語氣（不是新聞稿、不是 chat bot）
- 用具體數字、價位、比例，不用「適度」「謹慎」「逢低」這種空話
- 像 doctor's progress note：精準、有 reasoning、可追溯
- 中文寫作，繁體

【波段視角（重要）】
- 這是 2 週-1 個月的部位，不是隔日沖。rationale 要講「一個月的故事」：
  基本面動能、催化劑時程、產業趨勢，而不是「明天的資金流」。
- 風險劇本的 trigger 要是「收盤級別」的訊號（收盤跌破某價位、週線轉弱、
  法人連 N 日賣超），不是盤中即時反應——她看不到盤中。
- 執行計畫按「進場期 → 持有期每週檢查 → 出場紀律」組織，不是按小時。

【3 個禁區】
❌ 不要重複 dashboard 已顯示的數字（verdict / confidence / 預算）— 只補解釋
❌ 不要寫「投資人應該…」這種泛論 — 寫的是 Yuzu 個人的決策日誌
❌ 不要 markdown 列點 — 用完整段落（除了 risk_scenarios / checklist 已是結構化 list）

【核心：6 段散文 + 2 個 list】
每個 rationale paragraph 約 80-150 字，給「為什麼是這個價位 / 這個時間」的具體理由。
"""

PICK_SCHEMA = """輸出格式（strict JSON，無 markdown wrapping）：

{
  "context_paragraph": "string · 200-300 字 · 月維度市場 context：指數趨勢位置 / 資金結構 / 美股中期環境，不是只講今天",

  "why_this_stock": "string · 150-200 字 · 為什麼選這檔不選 scanner 其他候選 — 必須提到基本面動能（月營收/報價/訂單）與位階優勢",

  "entry_rationale": "string · 80-150 字 · 為什麼這個進場價區（支撐位 / 均線 / 型態；可分批的話說明怎麼分）",
  "stop_rationale":  "string · 80-150 字 · 為什麼停損設這個技術位（前波低點 / MA20 / MA60），為什麼 -7~-10% 放得下日常震盪",
  "target_rationale": "string · 80-150 字 · 為什麼這個目標（前高 / 量測幅度 / 本益比回歸），大約對應 +10~20%",
  "hold_rationale":   "string · 80-150 字 · 為什麼是 10-22 個交易日：催化劑時程 / 行情展開所需時間",

  "risk_scenarios": [
    { "trigger": "若收盤跌破 128（停損位）", "action": "隔日開盤出場，不凹單", "severity": "stop|hold|go", "rationale": "為什麼" },
    ...
  ],  // 5-8 個，severity 用 stop(紅)/hold(灰)/go(綠) 三種。trigger 必須是收盤級別訊號，她看不到盤中。

  "execution_checklist": [
    { "time": "進場期(D1-D3)", "action": "在 138.5-142 區間分批掛單，成交後立即設好 128 停損單" },
    { "time": "第 1 週檢查", "action": "..." },
    { "time": "第 2 週檢查", "action": "..." },
    { "time": "第 3-4 週", "action": "..." },
    { "time": "出場紀律", "action": "..." }
  ],  // 5-6 步驟，按持有階段組織（不是按小時）——她只能每天收盤後看一次

  "devils_advocate": {
    "verdict": "string · 直接抄 DA agent 的 verdict",
    "summary": "string · 100-150 字 · 抄 DA 但改寫成 prose（不抄原文）",
    "counter_arguments": ["...", "...", "..."]  // 3 條具體反駁
  }
}

絕對不要輸出 markdown code fence、不要解釋、純 JSON。
字串值內也禁用任何 markdown 記號（**粗體**、`反引號`、# 標題）——頁面用
純文字渲染，這些記號會原樣顯示給讀者。要強調就用中文標點或「引號」。
"""


WATCH_SYSTEM = """你是 Yuzu Capital OS 的「Pick Explainer」（觀望日模式，波段版）。

今天系統選擇不開新倉。這是波段策略（持有 10-22 個交易日、最多 3 檔在倉），
觀望的理由可能是：無夠好的 setup、倉位已滿、或系統性風險。
讀者是 Yuzu，3 個月後她回頭看這頁，要明白：
1. 今天為什麼不開新倉（不是壞了，是有理由）
2. 什麼條件會讓系統重新出手
3. Scanner 雷達最強的標的是誰，為什麼系統還是沒選它

【波段視角】
- 不要用「今日盤勢震盪」當觀望理由——波段不怕日內震盪，怕的是
  沒有好買點、位階太高、或基本面故事不完整。理由要落在這個層次。
- 若倉位已滿，直接說明：重點是管好在倉部位，不是找新標的。

【風格】
- 「耐心不是沒事做，是在等一個更好的價格」這種定調
- 用具體訊號數據解釋（位階 / 買點 / 基本面 / 籌碼連續性）
- 不要 apologetic（「不好意思今天沒推薦」）
- 不要過度說教（「投資需要紀律」）
- 像研究員寫的 daily note，平實有重量
"""

WATCH_SCHEMA = """輸出格式（strict JSON）：

{
  "context_paragraph": "string · 100-150 字 · 今日市場 context (regime / 量能 / 主要事件)",

  "why_watching": "string · 250-400 字 · 為什麼系統選擇今日不出手。
                    具體列出 2-4 個觀察點：例如『量能不足』『風險報酬比不夠』
                    『美股數據前 / VIX 高』『最強候選股位階偏高』等。",

  "reactivation_triggers": [
    { "cond": "外資連續 2 日買超", "note": "且單日金額 > 50 億" },
    { "cond": "...", "note": "..." }
  ],  // 3-5 個重新出手的條件，cond 是主條件、note 是附帶說明

  "scanner_top1": {
    "code": "string · scanner top1 的代號（從輸入抄）",
    "name": "string · 名稱",
    "score": int,
    "what_scanner_sees": "string · 100-150 字 · scanner 為什麼看上它（技術 / 籌碼 / 題材）",
    "what_we_dont_like": "string · 100-150 字 · 系統還是不買的原因（風險報酬比、位階、量能、context）"
  }
}

如果輸入沒有 scanner candidates，scanner_top1 全部填空字串。
絕對不要輸出 markdown code fence、不要解釋、純 JSON。
字串值內也禁用任何 markdown 記號（**粗體**、`反引號`、# 標題）——頁面用
純文字渲染，這些記號會原樣顯示給讀者。要強調就用中文標點或「引號」。
"""


def run(
    analysis: dict,
    market_data: dict,
    candidates: list,
) -> dict:
    """產生 pick 頁面所需的擴寫 JSON。

    analysis: data/analysis.json 內容
    market_data: data/market_data.json 內容
    candidates: data/candidate_stocks.json 的 candidates list
    """
    agents = analysis.get("agents", {})
    regime = analysis.get("regime", {})
    cf = analysis.get("capital_flow", {})
    pick_agent = agents.get("tw_daily_pick") or {}
    pick = (pick_agent.get("pick") or {})

    # Mode detection
    has_pick = pick.get("code") and pick.get("code") not in ("—", "NONE", "")

    if has_pick:
        return _explain_pick(
            pick=pick,
            pick_agent=pick_agent,
            regime=regime,
            cf=cf,
            agents=agents,
            market_data=market_data,
            candidates=candidates,
        )
    else:
        return _explain_watch(
            pick_agent=pick_agent,
            regime=regime,
            cf=cf,
            agents=agents,
            market_data=market_data,
            candidates=candidates,
        )


def _explain_pick(pick, pick_agent, regime, cf, agents, market_data, candidates):
    """Pick mode — generate full explanation for selected stock."""
    da = agents.get("devils_advocate") or {}
    sf = agents.get("signal_fusion") or {}
    mo = agents.get("market_overview") or {}
    indices = market_data.get("indices", {})
    breadth = market_data.get("breadth", {})
    inst = market_data.get("institutional_market", {})

    # Build the input context
    lines = [
        f"【今日盤後系統決策】",
        f"  pick: {pick.get('code')} {pick.get('name','')}",
        f"  verdict: {pick_agent.get('verdict','')} (conf {pick_agent.get('confidence','')})",
        f"  entry: {pick.get('entry_zone')} · stop: {pick.get('stop_loss')} · target: {pick.get('target')} · hold: {pick.get('hold_days')}",
        f"  ref_close: {pick.get('ref_close')} · risk_reward: {pick.get('risk_reward')}",
        f"  tw_daily_pick summary: {pick_agent.get('summary','')[:300]}",
        "",
        f"【今日市場 context】",
        f"  regime: {regime.get('market_regime')} ({regime.get('risk_level')})",
        f"  TAIEX: {indices.get('taiex',{}).get('close')} ({indices.get('taiex',{}).get('change_pct')}%)",
        f"  S&P500: {indices.get('sp500',{}).get('close')} ({indices.get('sp500',{}).get('change_pct')}%)",
        f"  VIX: {indices.get('vix',{}).get('close')}",
        f"  外資: {inst.get('foreign_net_amount',0)/1e8 if inst.get('foreign_net_amount') else '?'}億",
        f"  上漲家數/下跌家數: {breadth.get('advance')}/{breadth.get('decline')}",
        f"  trading_budget: {cf.get('budget',{}).get('trading',0)*100:.0f}%",
        f"  market_overview: {mo.get('summary','')[:200]}",
        "",
        f"【signal_fusion 指標】",
        f"  market_regime_score: {sf.get('market_regime_score')}",
        f"  trend_strength: {sf.get('trend_strength')}",
        f"  risk_pressure: {sf.get('risk_pressure')}",
        f"  scanner_momentum: {sf.get('scanner_momentum')}",
        f"  breadth_score: {sf.get('breadth_score')}",
        f"  foreign_flow_strength: {sf.get('foreign_flow_strength')}",
        "",
        f"【其他 scanner top 5 候選（這些不是被選的）】",
    ]
    for c in (candidates or [])[:5]:
        sigs = [k for k in ("trend_up", "above_ma60", "pullback_buy", "base_breakout", "vol_accumulate", "rsi_swing", "rs_20d_strong") if c.get(k)]
        chosen = " ← 被選中" if c.get("code") == pick.get("code") else ""
        lines.append(f"  {c.get('code')} {c.get('name')} score={c.get('score')} signals={sigs}{chosen}")

    lines.append("")
    lines.append("【Devil's Advocate 的反方論述】")
    lines.append(f"  verdict: {da.get('verdict','')}")
    lines.append(f"  summary: {da.get('summary','')[:300]}")
    for arg in (da.get('counter_argument') or da.get('counter_arguments') or [])[:3]:
        lines.append(f"  · {arg}")
    for r in (da.get('risk_flags') or [])[:3]:
        lines.append(f"  ⚠ {r}")

    user_content = "\n".join(lines) + """

請按 JSON schema 產出 pick 頁所需的擴寫內容。
記住：寫得像 Yuzu 自己研究後寫的決策日誌，不是 AI 助理輸出。
"""

    system_with_schema = PICK_SYSTEM + "\n\n" + PICK_SCHEMA
    return call_llm(system_with_schema, user_content, "pick_explainer", custom_schema=True)


def _explain_watch(pick_agent, regime, cf, agents, market_data, candidates):
    """Watch mode — explain why no pick + what to watch for."""
    sf = agents.get("signal_fusion") or {}
    mo = agents.get("market_overview") or {}
    da = agents.get("devils_advocate") or {}
    indices = market_data.get("indices", {})
    breadth = market_data.get("breadth", {})
    inst = market_data.get("institutional_market", {})

    top = (candidates or [{}])[0] if candidates else {}
    SIG_KEYS = ("trend_up", "above_ma60", "pullback_buy", "base_breakout", "vol_accumulate", "rsi_swing", "rs_20d_strong")
    top_signals = [k for k in SIG_KEYS if top.get(k)]

    lines = [
        f"【今日盤後系統決策：觀望】",
        f"  verdict: {pick_agent.get('verdict', '空手觀望')} (conf {pick_agent.get('confidence','')})",
        f"  tw_daily_pick summary: {pick_agent.get('summary','')[:400]}",
        "",
        f"【今日市場 context】",
        f"  regime: {regime.get('market_regime')} ({regime.get('risk_level')})",
        f"  TAIEX: {indices.get('taiex',{}).get('close')} ({indices.get('taiex',{}).get('change_pct')}%)",
        f"  S&P500: {indices.get('sp500',{}).get('close')} ({indices.get('sp500',{}).get('change_pct')}%)",
        f"  VIX: {indices.get('vix',{}).get('close')}",
        f"  外資: {inst.get('foreign_net_amount',0)/1e8 if inst.get('foreign_net_amount') else '?'}億",
        f"  上漲家數/下跌家數: {breadth.get('advance')}/{breadth.get('decline')}",
        f"  trading_budget: {cf.get('budget',{}).get('trading',0)*100:.0f}%",
        f"  market_overview: {mo.get('summary','')[:200]}",
        "",
        f"【signal_fusion 指標】",
        f"  trend_strength: {sf.get('trend_strength')}",
        f"  risk_pressure: {sf.get('risk_pressure')}",
        f"  scanner_momentum: {sf.get('scanner_momentum')}",
        f"  breadth_score: {sf.get('breadth_score')}",
        f"  foreign_flow_strength: {sf.get('foreign_flow_strength')}",
        f"  volatility_risk: {sf.get('volatility_risk')}",
        "",
    ]

    if top.get("code"):
        lines += [
            f"【Scanner top 1 候選（雖然沒被選）】",
            f"  {top.get('code')} {top.get('name','')} score={top.get('score','')} close={top.get('close','')}",
            f"  signals: {top_signals}",
        ]
    else:
        lines.append("【Scanner 今日無候選】")

    lines.append("")
    lines.append("【DA / capital_flow override 線索】")
    lines.append(f"  flow_direction: {cf.get('flow_direction')}")
    for f in cf.get("override_flags", [])[:5]:
        lines.append(f"  · {f}")
    if da.get("summary"):
        lines.append(f"  DA: {da.get('summary','')[:200]}")

    user_content = "\n".join(lines) + """

請按 watch-day JSON schema 產出內容。
"""

    system_with_schema = WATCH_SYSTEM + "\n\n" + WATCH_SCHEMA
    return call_llm(system_with_schema, user_content, "pick_explainer", custom_schema=True)
