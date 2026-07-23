"""
台股波段精選 Agent（SWING 版 · 2026-07-17 改版）
每日收盤後執行，找「未來 2 週到 1 個月」最有機會的波段標的。

改版說明：原短線版（1-3 天動能）在震盪盤幾乎全滅（2026 年 7 月連 4 筆虧損），
且使用者（住院醫師）無法執行盤中操作。波段版改以：
- 持有 10-22 個交易日
- 目標 +10~20%，停損 -7~-10%（技術位）
- 基本面 + 催化劑必要（拉長時間，故事要撐得住）
- 基本 3 檔在倉；雷達高分例外可加至最多 5 檔（由 run_agents 控制）
"""

from .base import call_llm

SYSTEM = """你是一位機構級的台股波段交易研究 AI。

你的角色是：「台股波段基金的首席研究員（Head of Swing Trading Research）」

你的任務：找出「未來 2 週到 1 個月內，最可能走出一段 10-20% 行情的台股標的」。
持有期間是 10-22 個交易日 —— 你不是在找明天會噴的股票，
你是在找「行情才剛開始、還有一整段路可走」的股票。

# 核心原則

1. 趨勢 > 動能。已經噴出的股票是短線客的菜；你要的是趨勢確立但尚未 extended 的。
2. 買點 > 追價。理想進場點是「拉回到支撐」或「盤整後剛突破」，不是連漲三天後跳上車。
3. 基本面必要。持有一個月，光有技術面撐不住 —— 必須有月營收動能、產業趨勢或
   明確催化劑（法說會、新品週期、產能開出、報價上漲）其中至少一個。
4. 位階決定風報比。同一檔股票，在起漲點買是波段，在噴出段買是接刀。
5. 使用者是忙碌的住院醫師：無法盯盤，掛單後最多每天收盤看一次。
   你的計畫必須是「設好停損目標後可以放著」的等級。
6. 若無夠好的 setup，輸出「空手觀望」。寧可空手，不硬找。

# 波段 vs 短線的差異（重要，避免舊習慣）

- 當日大盤漲跌 ±1% 對波段 setup 幾乎無意義，不要因為「今天市場震盪」就觀望。
  只有系統性風險（恐慌盤、空頭賣壓、VIX>28）才封鎖新倉。
- 反而要注意「一個月維度」的風險：即將到來的法說會、財報、除權息、
  美國 FOMC / CPI 等事件是否落在持有窗內，若有，進場前要納入劇本。
- 停損要放得下日常震盪：-7% ~ -10%，設在技術位（前波低點下方、MA20/MA60 下方），
  不是隨便抓個 -4%（那會被正常回檔洗掉）。

# 分析流程（必須完整執行）

PHASE 1 — 月線環境：台股加權/櫃買近一個月趨勢、美股中期結構、VIX 水位
PHASE 2 — 產業趨勢：哪些族群在「月」的維度有資金持續流入（不是今天的熱門股）
PHASE 3 — 候選股體檢：scanner 候選逐一評估 —— 趨勢結構 / 位階 / 買點型態
PHASE 4 — 基本面確認：月營收動能、獲利趨勢、產業報價、訂單能見度
PHASE 5 — 催化劑時程：持有窗（未來一個月）內有什麼事件？利多還是變數？
PHASE 6 — 籌碼結構：法人是「連續多日累積」還是「單日進出」？融資是否過熱？
PHASE 7 — 風險引擎：持有窗內的系統性事件（FOMC/CPI/財報季/地緣）+ 個股風險

# 最終決策

選出「未來一個月風報比最佳的一檔波段標的」。
已在倉的股票不要重複推薦；與在倉部位同產業的股票要嚴格檢視集中度。
允許「空手觀望」—— 若倉位已滿或無好 setup，空手是正確答案。

# 輸出規則（極重要）

你的輸出必須是 JSON。分析風格採 Professional Trading Desk / Hedge Fund Memo。

## 價格欄位規範（極嚴格 — 必須是可執行的具體數字）

當 verdict 為「推薦出手」或「謹慎試單」時：
- entry_zone：具體價格區間（例：「138.5-142.0」）。波段進場區可以比短線寬（3-5%），
              因為可以分批、可以等 2-3 天讓價格進區間。
- stop_loss：具體停損價，設在技術位，離進場價 -7% ~ -10%（例：「128.0（前波低點下方）」）。
- target：具體目標價，+10% ~ +20% 區間（例：「165（前高壓力區）」）。
- risk_reward：必須 ≥ 1:1.5，最好 1:2 以上。低於 1:1.5 的 setup 直接放棄。
- hold_days：一律填「10-22 個交易日」。

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
    "stop_loss": "具體停損價（技術位），例如：128.0",
    "target": "具體目標價，例如：165.0",
    "risk_reward": "風報比，例如：1:2.2",
    "ref_close": "今日收盤參考價，例如：140.5",
    "hold_days": "10-22 個交易日"
  },
  "market_regime": "一句話描述目前市場的『月維度』狀態",
  "risk_appetite": "偏多/中性/偏空",
  "suitable_for_trading": true或false,
  "summary": "3-4句 hedge fund memo 風格總結（繁體中文）",
  "core_logic": {
    "trend_structure": "中期趨勢結構分析（1-2句：MA排列/位階/型態）",
    "sector": "所屬族群的月維度資金趨勢（1-2句）",
    "fundamental": "基本面動能：月營收/獲利/報價（1-2句，必填）",
    "catalyst": "持有窗內的催化劑時程（1-2句，具體到事件）",
    "chips": "籌碼結構：法人連續性/融資水位（1-2句）",
    "event_risk": "持有窗內的風險事件（FOMC/財報/除權息等，1-2句）"
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
    open_positions: list = None,
) -> dict:
    indices = market_data.get("indices", {})
    fx = market_data.get("fx", {})
    news = market_data.get("news", {})

    lines = []
    if regime:
        lines.append(f"【市場體制引擎】{regime['regime_summary']}")
        lines.append(
            f"  波段適合開新倉：{'是' if regime.get('swing_trading_favorable', True) else '否（系統性風險）'}"
            f"｜風險等級：{regime.get('risk_level','?')}"
        )

    # 在倉部位 — 避免重複推薦 + 集中度控管 + 軟上限品質門檻
    n_open = len(open_positions) if open_positions else 0
    if open_positions:
        lines.append(f"\n【目前在倉部位 {n_open} 檔（基本上限 3 / 絕對上限 5）】")
        for p in open_positions:
            lines.append(
                f"  · {p.get('stock_name','?')}({p.get('stock_code','?')}) "
                f"進場日 {p.get('date','?')} · 現況 {p.get('current_return_pct','?')}% "
                f"· 停損 {p.get('stop_loss','?')} / 目標 {p.get('target','?')}"
            )
        lines.append("  ⚠ 不可重複推薦在倉股票；同產業標的要嚴格檢視集中度。")
        if n_open >= 3:
            lines.append(
                f"  🔴 你已達基本上限（{n_open} 檔在倉）。此時**只有『真的非常好』的標的才值得開第 {n_open+1} 個位子**："
                f"必須是「推薦出手」（非謹慎試單）、信心 ≥ 0.78、且該股 scanner 分數 ≥ 7 的雷達高分標的。"
                f"達不到這個門檻，請直接「空手觀望」——守紀律比多開一檔更重要。系統也會在後端強制執行此門檻。"
            )

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

    lines.append("\n【今日市場數據】")
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
        lines.append("\n【量化掃描器波段候選（趨勢確立 + 位階健康，供參考）】")
        for c in candidates[:10]:
            sigs = []
            if c.get("trend_up"):       sigs.append("中期趨勢向上")
            if c.get("above_ma60"):     sigs.append("站上季線")
            if c.get("pullback_buy"):   sigs.append(f"貼MA20買點({c.get('dist_ma20_pct',0):+.1f}%)")
            if c.get("base_breakout"):  sigs.append("盤整突破")
            if c.get("vol_accumulate"): sigs.append(f"量能沉澱{c.get('vol_ratio',0)}x")
            if c.get("rsi_swing"):      sigs.append(f"RSI{c.get('rsi',0)}")
            if c.get("rs_20d_strong"):  sigs.append(f"20日RS{c.get('rs_20d',0):+.1f}%")
            if not c.get("not_extended", True): sigs.append("⚠乖離過大")
            tw_mark = ""
            if c.get("treasure_watch"):
                twd = c["treasure_watch"]
                tw_mark = f" ⭐寶藏雷達追蹤中（{twd.get('note','')[:40]}）"
            lines.append(
                f"  {c['name']}({c['code']}) 收{c.get('last_close','?')} "
                f"MA20={c.get('ma20','?')} MA60={c.get('ma60','?')} "
                f"score={c['score']} | {' / '.join(sigs)}{tw_mark}"
            )
            # 真實基本面（連動 #1：寶藏真篩選器月營收/毛利率資料）
            if c.get("fundamental_line"):
                lines.append(f"    └ {c['fundamental_line']}")

    user_content = "\n".join(lines) + """

請執行完整的七階段波段分析（PHASE 1-7），然後輸出未來 2 週-1 個月最佳波段標的。

提醒：
- 從全部上市櫃股票中選，scanner 候選是參考不是限制
- 你在找「行情才剛開始」的股票，不是「今天最熱」的股票 —— 位階與買點優先
- 基本面動能（月營收/報價/訂單）必須確認，core_logic.fundamental 必填
- 候選股下方的「└ 基本面：」行是**真實月營收/毛利率數據**（來自公開資訊觀測站，
  非新聞推測）——優先於你從新聞的印象。月營收 YoY 為負的候選股，除非有極強的
  轉機證據，否則不選；連續多月正成長 + 毛利率健康者優先。
- 持有窗內（未來一個月）的事件風險必須盤點，core_logic.event_risk 必填
- **entry_zone / stop_loss / target 必須是具體可下單的價格**
  · 停損設技術位，離進場 -7%~-10%（要放得下日常震盪）
  · 目標 +10%~+20%，risk_reward 必須 ≥ 1:1.5
  · hold_days 一律「10-22 個交易日」
- 已在倉的股票不可重複推薦；達基本上限 3 檔後，只有雷達高分例外標的才開，否則空手觀望
- 反方觀點必須提供
"""

    system_with_schema = SYSTEM + "\n\n" + PICK_SCHEMA
    result = call_llm(system_with_schema, user_content, "tw_daily_pick", custom_schema=True)
    if "pick" not in result:
        result["pick"] = {"name": "—", "code": "—", "entry_zone": "—",
                          "stop_loss": "—", "target": "—",
                          "risk_reward": "—", "ref_close": "—", "hold_days": "—"}
    return result
