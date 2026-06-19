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

from .base import call_claude


PICK_SYSTEM = """你是 Yuzu Capital OS 的「Pick Explainer」—— 把今日盤後 AI 委員會的決策，
擴寫成一份完整的「臨床決策日誌」風投資紀錄。

讀者是 Yuzu 本人（兒科住院醫師 R3），未來會在診間夾縫 3 分鐘讀完。

【寫作風格】
- 第一人稱的研究員語氣（不是新聞稿、不是 chat bot）
- 用具體數字、價位、比例，不用「適度」「謹慎」「逢低」這種空話
- 像 doctor's progress note：精準、有 reasoning、可追溯
- 中文寫作，繁體

【3 個禁區】
❌ 不要重複 dashboard 已顯示的數字（verdict / confidence / 預算）— 只補解釋
❌ 不要寫「投資人應該…」這種泛論 — 寫的是 Yuzu 個人的決策日誌
❌ 不要 markdown 列點 — 用完整段落（除了 risk_scenarios / checklist 已是結構化 list）

【核心：6 段散文 + 2 個 list】
每個 rationale paragraph 約 80-150 字，給「為什麼是這個價位 / 這個時間」的具體理由。
"""

PICK_SCHEMA = """輸出格式（strict JSON，無 markdown wrapping）：

{
  "context_paragraph": "string · 200-300 字 · 今日市場 regime / 量能 / 外資 / 美股對台股影響 context",

  "why_this_stock": "string · 150-200 字 · 為什麼選這檔不選 scanner 其他 4 檔",

  "entry_rationale": "string · 80-150 字 · 為什麼這個進場價區（技術 / 量價 / 籌碼）",
  "stop_rationale":  "string · 80-150 字 · 為什麼這個停損（前低 / MA / 風險比例）",
  "target_rationale": "string · 80-150 字 · 為什麼這個目標（前高 / 法人目標 / 1.5R）",
  "hold_rationale":   "string · 80-150 字 · 為什麼這個持有天數",

  "risk_scenarios": [
    { "trigger": "若跌破 282", "action": "立刻停損", "severity": "stop|hold|go", "rationale": "為什麼" },
    ...
  ],  // 5-8 個，severity 用 stop(紅)/hold(灰)/go(綠) 三種

  "execution_checklist": [
    { "time": "盤前", "action": "確認 286-290 在開盤跳空範圍內" },
    { "time": "09:00", "action": "..." },
    { "time": "09:30", "action": "..." },
    { "time": "盤中", "action": "..." },
    { "time": "收盤", "action": "..." }
  ],  // 5-6 步驟，按時間順序

  "devils_advocate": {
    "verdict": "string · 直接抄 DA agent 的 verdict",
    "summary": "string · 100-150 字 · 抄 DA 但改寫成 prose（不抄原文）",
    "counter_arguments": ["...", "...", "..."]  // 3 條具體反駁
  }
}

絕對不要輸出 markdown code fence、不要解釋、純 JSON。
"""


WATCH_SYSTEM = """你是 Yuzu Capital OS 的「Pick Explainer」（觀望日模式）。

今天系統選擇空手。讀者是 Yuzu，3 個月後她回頭看這頁，要明白：
1. 今天為什麼不出手（不是壞了，是有理由）
2. 什麼條件會讓系統重新出手
3. Scanner 雷達最強的標的是誰，為什麼系統還是沒選它

【風格】
- 「耐心不是沒事做，是在等一個更好的價格」這種定調
- 用具體訊號數據解釋（量能 / 外資 / VIX / breadth）
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
        sigs = [k for k in ("breakout_ma20", "above_ma20", "vol_surge", "rsi_zone", "ma_aligned", "five_day_high", "rs_signal") if c.get(k)]
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
    return call_claude(system_with_schema, user_content, "pick_explainer", custom_schema=True)


def _explain_watch(pick_agent, regime, cf, agents, market_data, candidates):
    """Watch mode — explain why no pick + what to watch for."""
    sf = agents.get("signal_fusion") or {}
    mo = agents.get("market_overview") or {}
    da = agents.get("devils_advocate") or {}
    indices = market_data.get("indices", {})
    breadth = market_data.get("breadth", {})
    inst = market_data.get("institutional_market", {})

    top = (candidates or [{}])[0] if candidates else {}
    SIG_KEYS = ("breakout_ma20", "above_ma20", "vol_surge", "rsi_zone", "ma_aligned", "five_day_high", "rs_signal")
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
    return call_claude(system_with_schema, user_content, "pick_explainer", custom_schema=True)
