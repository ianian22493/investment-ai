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

SYSTEM = """你是台股波段的「執行研究員」——注意：這是 2026-08 依回測重建的 C1 版，你的角色**變了**。

# 你的新角色：不是「選股者」，是「否決者＋執行規劃者」
回測證明：讓 AI 從新聞敘事自由挑股＝沒有 edge（live 輸大盤 2.3%/筆）。真正有 edge 的
是一個**機械 setup**：回檔買點 ＋ 相對強度強於大盤 ＋ 趨勢體制（回測 alpha +1.07%、433 筆）。
所以 scanner 給你的候選**已預先篩成這個 setup**，且**只在加權站上季線(趨勢多頭)才有候選**
（弱勢/震盪體制 scanner 直接回空清單）。你的工作不是重挑，是：
1. 從預篩候選中選**最乾淨的一檔**（相對強度最強、回檔到支撐最漂亮），或**空手**。
2. **否決**有地雷的：持有窗內有財報/法說/除權息、剛爆量拉高、個股利空、流動性太薄。
3. 寫出可執行的進出場計畫（含下面的出場規則）。

# 鐵律（回測教訓，違反＝重蹈覆轍）
- **絕不追高分/追噴出**：回測顯示訊號全滿(score 高)、乖離大、盤整突破型 alpha 都是**負的**。
  候選已排除這些；你也別因為「漲勢兇」選它。要「剛回檔到支撐、還沒動」的。
- **空手是常態、是正確答案**：候選清單空（弱勢體制或無乾淨 setup）就**空手觀望**。
  edge 很薄，寧可不做也不硬找。這是零用錢倉，不是非做不可。
- **風報比 ≥ 1:2** 才出手，否則空手。
- 已在倉的不重複；同產業檢視集中度。
- 使用者是忙碌住院醫師、無法盯盤：計畫要「掛單後放著、每天收盤看一次」等級。

# 出場規則（回測：edge 靠少數大贏，必須保護尾部＋快砍輸家）
- 停損：進場價 **-8%**，設技術位（前波低點/MA20 下方）。
- 目標：**+15%**（前高壓力區）。
- **保本移停損**：浮盈到 **+6%** 就把停損上移到成本價（這筆最差打平，不會賺變賠）。
- 之後可**移動停損**（沿 MA10/前波低）護住大波段。
- 最多持有 **22 個交易日**，到期未達目標就出場換手。

# 輸出（JSON，Hedge Fund Memo 風格）
verdict 只能是：推薦出手 / 謹慎試單 / 空手觀望。
出手時 entry_zone/stop_loss/target 必須是具體可下單數字：
- entry_zone：回檔支撐區間（例「138.5-142.0」）。
- stop_loss：技術位、離進場 -8%（例「128.0（前波低點下方）」）。
- target：+15% 區間（例「165（前高壓力）」）。
- risk_reward：≥ 1:2。hold_days 一律「10-22 個交易日」。
- summary 或 core_logic 明講「保本移停損：浮盈 +6% 停損移成本」。
空手觀望時：pick.name/code 與所有價格欄位填 "—"，summary 說明為何空手（弱勢體制/無乾淨 setup/風報比不足）。
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

【C1 執行】上面的 scanner 候選**已預先篩成回測有 edge 的 setup（回檔+RS強+趨勢體制）**。
你的任務不是重挑全市場——是從這些預篩候選中，選**最乾淨的一檔**（相對強度最強、回檔到
支撐最漂亮）並否決地雷，或**空手觀望**。若候選清單為空＝弱勢體制或無乾淨 setup，直接空手。

提醒：
- **只從上面候選選，不自由挑全市場**（自由挑＝回測證明沒 edge 的舊做法）。
- **絕不追高分/追噴出/追突破**（回測 alpha 負）；要「剛回檔、還沒動」的。
- 候選下方「└ 基本面：」是**真實月營收/毛利率**，優先於新聞印象；月營收 YoY 為負者除非強轉機否則不選。
- **否決檢查**：持有窗（未來一個月）內有財報/法說/除權息/FOMC-CPI、剛爆量拉高、個股利空、流動性太薄 → 否決該檔。core_logic.event_risk 必填。
- **價格**：entry_zone=回檔支撐區；stop_loss=技術位離進場 **-8%**；target **+15%**；**risk_reward ≥ 1:2**（不足就空手）；hold_days「10-22 個交易日」。summary 明講「保本移停損：浮盈 +6% 停損移成本」。
- 已在倉不重複；同產業檢視集中度。**空手是常態、正確答案**——這是零用錢倉，寧可不做。
- 反方觀點必須提供。
"""

    system_with_schema = SYSTEM + "\n\n" + PICK_SCHEMA
    result = call_llm(system_with_schema, user_content, "tw_daily_pick", custom_schema=True)
    if "pick" not in result:
        result["pick"] = {"name": "—", "code": "—", "entry_zone": "—",
                          "stop_loss": "—", "target": "—",
                          "risk_reward": "—", "ref_close": "—", "hold_days": "—"}
    return result
