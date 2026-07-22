# Yuzu Capital OS — 系統架構與交接文件

> 最後更新：2026-07-22。給接手的 AI（或未來的自己）：**動手改任何東西前先讀完本文件**，
> 特別是 §6 的日期語義與 §11 的踩雷清單——那些坑每個都真實付過學費。

---

## 0. 一句話總覽

台股**波段選股系統**（2 週-1 個月持有），每日盤後由 GitHub Actions 跑多 agent
LLM pipeline（Gemini 2.5 Flash），產出 dashboard + 每日決策日誌頁，
發佈到 GitHub Pages。使用者是又瑄（兒科住院醫師 R3，無法盯盤）。

- 線上：https://ianian22493.github.io/investment-ai/
- Repo：`ianian22493/investment-ai` · 本機 `C:\tmp\investment-ai\`
- 主入口 hub：`ianian22493/ianian22493.github.io`（第 7 張卡連到每日 pick）

## 1. 策略現況（⚡ 2026-07-17 起 = 波段時代）

| 參數 | 值 |
|---|---|
| 持有期 | 10-22 個交易日 |
| 目標 / 停損 | +10~20% / -7~-10%（技術位，放得下日常震盪） |
| 風報比門檻 | ≥ 1:1.5 |
| 同時在倉上限 | 3 檔（`run_agents.MAX_OPEN_POSITIONS`） |
| 進場窗 | pick 後 3 個交易日內須觸及 entry_zone，否則 `not_filled` |
| 基本面 | 必要——月營收動能 + 催化劑（真實數據注入，見 §9） |

**時代分界**：`alpha_db.SWING_ERA_START = "2026-07-17"`。之前是短線 1-3 日動能策略
（實測 13 筆、勝率 46.2%、**avg alpha -1.72% = 輸給 0050**，故改版）。
舊 picks 依原條款結算，統計分 era 呈現。

## 2. 每日 Pipeline（16:30 盤後 cron 的完整流程）

```
cron-job.org 觸發 workflow_dispatch
→ Skip check（週六 / 週日AM / 週五PM / 台股假日 → 整個跳過）
→ Smoke tests（soft gate，失敗不擋）
→ fetch_market_data.py       指數/匯率/新聞/持股即時價
→ scanner.py                 全市場 200 大 → 8 維波段信號 → 前 15 候選
→ run_agents.py
    ├─ resolve_pending()     結算 / 更新在倉部位（進場窗+停損目標+時間窗）
    ├─ regime engine         市場體制（純規則，無 LLM）
    ├─ panic SOP + 冷卻期    恐慌判定 → panic_log.json
    ├─ 候選股 enrich         寶藏標記 ⭐ + 真實基本面行 └
    ├─ Phase 1-3 agents      市場總覽/新聞情緒/短線/長線/美股/匯率/配置（有 cache）
    ├─ Phase 4（hour≥13）    倉滿 gate → 冷卻 gate → tw_daily_pick（LLM）
    │                        → 重複推薦攔截 → correlation → treasure 徽章
    │                        → save_today_pick → micro-pick → watch log
    ├─ Phase 5 desk masters  trading/portfolio/wealth master + CIO + constraint
    └─ 輸出                  analysis.json / signal_vector / swing_scorecard.json
→ picks/generate.py          pick_explainer（LLM）→ 隔日交易日頁 + 月曆 + latest.json
→ Commit & push（3 次 retry with rebase）→ Pages rebuild
```

盤前 cron（08:30，Mon-Fri）只更新市場資料與 dashboard：**不產 pick 頁**
（`generate.py` 以 `run_dt.hour < 13` 擋），並保留昨日 `tw_daily_pick`
（`run_agents` 尾端 preserve 邏輯，避免 hub 的 latest 斷鏈）。

## 3. 排程（正本）

| 時間（Asia/Taipei） | 誰觸發 | 做什麼 |
|---|---|---|
| Mon-Fri 08:30 | cron-job.org | 盤前刷新（無 pick） |
| Sun-Thu 16:30 | cron-job.org | 盤後完整 pipeline → 產「隔日交易日」pick 頁 |
| Fri PM / Sat / Sun AM / 台股假日 | — | workflow「Skip check」step 自動跳過 |
| 每月 11 日 | GH cron（備援）；cron-job.org 主力**待設** | `screener/monthly_screener.py` 全市場月營收掃描 |

假日正本：`data/tw_holidays.json`（holidays + special_trading_days 補班日）。
使用者鐵律：**準時觸發一律 cron-job.org**，GitHub 內建 cron 只當備援。

## 4. 開倉決策閘門鏈（依序，任一擋下即空手）

1. **Workflow skip**（週末/假日）— `.github/workflows/daily_analysis.yml`
2. **恐慌冷卻期**（恐慌日後 3 個交易日）— `run_agents._panic_cooldown_status`
3. **Regime swing gate**（恐慌盤/空頭賣壓才封）— `agents/regime_engine.py` `swing_trading_favorable`
4. **倉滿 gate**（3/3 在倉）— rule-based 空手，不呼叫 LLM
5. **Agent 自身判斷**（無好 setup → 空手觀望）
6. **重複推薦攔截**（agent 推了在倉股票 → 強制空手）
7. **entry sanity check**（AI 報價幻覺 >30% 偏離 → 拒存）— `outcome_tracker`
8. **constraint_validator**（預算不足時壓 verdict）

注意 2/3 的差異：短線 gate（`short_term_trading_favorable`）對日內震盪過敏，
**波段預算與開倉一律看 swing gate**（capital_flow 已改）。

## 5. 模組地圖

| 檔案 | 職責 |
|---|---|
| `run_agents.py` | 總指揮。Phase 1-5、閘門鏈、寶藏連動、輸出 analysis.json |
| `scanner.py` | 波段候選：trend_up/above_ma60/pullback_buy/not_extended/base_breakout/vol_accumulate/rsi_swing/rs_20d_strong（8 維，6mo 歷史，盤中執行剔除當日不完整 K 棒） |
| `agents/tw_daily_pick.py` | 波段選股 agent（prompt 含在倉部位、基本面行、寶藏標記） |
| `agents/regime_engine.py` | 純規則市場體制 + 短線/波段雙 gate |
| `agents/capital_flow.py` | 資金配置（看 swing gate 調預算） |
| `agents/holdings_correlation.py` | pick vs 持股產業重疊警告（manual+auto sector map） |
| `agents/pick_explainer.py` | pick 頁深度內容（LLM，PICK/WATCH 雙模式，波段照護計畫） |
| `agents/base.py` | Gemini 多 key 輪替（GEMINI_API_KEY + _2..._4）、429 降級、stale cache fallback |
| `outcome_tracker.py` | 結算引擎：進場窗觸價 → 停損/目標/時間窗；watch log 10 日結算 |
| `alpha_db.py` | SQLite（data/alpha.db）：picks/watch_log/reflections + scorecard builder |
| `fundamental_feed.py` | 寶藏 screener 月營收/毛利率 → 候選股 prompt 注入 |
| `tw_sector_lookup.py` | FinMind 全市場產業對照 → `data/sector_map_auto.json`（7 天 TTL） |
| `agent_cache.py` / `prompt_cache.py` | 兩層快取（agent TTL 天 / prompt sha256 小時）；wealth cluster 綁 portfolio hash |
| `picks/generate.py` | pick 頁 templater + 月曆 manifest + latest.json；`_next_trading_day` 假日感知 |
| `screener/` | 寶藏真篩選器（月營收全市場）+ watchlist_watcher（價格觸發哨兵） |
| `tests/smoke.py` | 5 條 smoke（CI soft gate；本地跑要 `GEMINI_API_KEY=smoke-test-stub`） |

## 6. 資料檔地圖（⚠ 日期語義是本系統最大陷阱）

**alpha.db `picks` 表的兩個日期：**
- `date` = **決策日**（cron 執行日，如週日）
- `target_date` = **目標交易日**（隔一交易日 = pick 頁檔名，如週一）

pick 頁檔名、月曆 join、hub latest 全都用 target_date 語義；
DB 查詢、era 統計用 date。**混用會讓月曆勝負永遠 pending**（7/22 修過一次，v4 migration）。

| 檔案 | 內容 / 消費者 |
|---|---|
| `data/analysis.json` | 每日總輸出（agents/regime/panic_sop/open_positions）→ dashboard + hub |
| `data/alpha.db` | picks（v5：entry_fill_date/price）/watch_log/reflections（**git 追蹤**，會有 merge 衝突——binary 以 cron 版為準） |
| `data/swing_scorecard.json` | era/季度聚合成績單 → 寶藏審判日抓取 |
| `data/panic_log.json` | 恐慌日紀錄 → 冷卻期 gate |
| `data/watchlist.json` | 寶藏觀察名單機器鏡像（**價格觸發要跟 觀察名單.md 人工同步**）|
| `data/sector_map.json` / `_auto.json` | 產業對照（手動 132 檔優先 > FinMind 自動 3011 檔） |
| `data/screener/history/rev_YYYMM.json` | 全市場月營收 13 個月（fundamental_feed 的源） |
| `data/tw_holidays.json` | 台股假日正本（workflow gate + next_trading_day 共用） |
| `picks/latest.json` | 最新一篇日誌指標 → hub card 直達 |
| `picks/YYYY-MM-DD.html` | 每日決策日誌（**檔名 = 目標交易日**；生成後不可變） |

**localStorage keys（前端）**：`yuzu-pick-actioned`（v2 {state,entry}，月曆toggle ↔ dashboard ARCHIVE 同步）、`yuzu-counsel-history`、`yuzu-settings`、`yuzu-check-{date}-{code}`。

## 7. 結算引擎規則（outcome_tracker v5）

1. **進場窗**：pick 後 3 個交易日（`ENTRY_WINDOW_DAYS`），當日 [low,high] 與 entry_zone 有交集即成交（成交價 = zone 中點 clip 當日區間）。未觸價 → `not_filled`（結案、不計勝率）。窗未完 → `pending_fill`（前端「待觸價」）。
2. **持有窗**：自成交日起 hold_days_max（波段=22）個交易日。
3. 同日觸停損+目標 → **保守假設先停損**。
4. AI stop/target 數量級幻覺 → `_sanitize_stop_target` 剃掉，退化成時間結算。
5. 損益基準 = `entry_fill_price`（有值時），alpha 基準 = 0050 pick 日（1-3 日誤差已接受）。
6. 觀望日 watch_log：10 個交易日後結算 scanner top1 的 max/min → reflection 學「該出手沒出手」的成本。

## 8. 前端

- `index.html`（5,900+ 行單檔 dashboard，暗色）：EXEC FLOOR / TRADING FLOOR 有 SWING BOOK 在倉卡 + 寶藏徽章 + correlation 警告 + panic banner；glossary tooltip 自動掛（MutationObserver）。
- `picks/` 子站（暖奶油紙「臨床決策日誌」風，**風格刻意與 dashboard 不同**）：
  `template.html`（pick 日，8 段）/ `template-watch.html`（觀望日）/ `index.html`（月曆+清單+7/30/all 勝率 toggle）/ `glossary.js` / `picks.css`。
- TradingView 免費 embed **整類擋台股**——用 CTA 卡連出去 `tradingview.com/symbols/TWSE-{code}/`，別再試 iframe。
- Hub card 讀 `picks/latest.json`（no-store + query bust），fallback 月曆。

## 9. 與寶藏股研究室的四項連動（2026-07-22 上線）

寶藏股研究室 = 使用者的**長波系統**（方法論在 `Cowork\寶藏股研究室\`，程式寄居本 repo）。

| # | 方向 | 機制 |
|---|---|---|
| 1 | 寶藏→波段 | `fundamental_feed.py`：screener 月營收/毛利率 → 候選股 prompt（YoY 為負原則不選） |
| 2 | 寶藏→波段 | pick 命中 `watchlist.json` → ⭐ 收斂徽章（頁面+dashboard+prompt） |
| 3 | 互相 | 恐慌日（VIX>25 或 -2.5%，正本=watchlist.json panic 區塊）→ banner：swing 停手 = 長波買點窗口 |
| 4 | 波段→寶藏 | `swing_scorecard.json` → 季度審判日抓取（分 era 統計） |

## 10. LLM 配置

- **Gemini 2.5 Flash**（`gemini-2.5-flash`，2.0 已下線勿回退）
- 免費額度**實測 20 RPD / key**；`GEMINI_API_KEY` + `_2` 雙 key 輪替 = 40 RPD，日用量 ~12-15 次
- 快取：agent_cache（TTL 天；wealth cluster 3 天 + portfolio hash 失效）+ prompt_cache（sha256，小時）
- 省 quota 設計：倉滿/冷卻 gate 直接 rule-based 空手不呼叫 LLM；pick_explainer 永不 agent-cache

## 11. 踩雷清單（每條都真實發生過）

1. **cp950**：Windows 本地 print ✓✗◯◇ 等符號會炸 → 一律 ASCII 標記（[OK]/[WARN]/[HOLD]）。
2. **workflow 檔 push 需要 token 有 `workflow` scope**：沒有時走 GitHub 網頁編輯（給使用者步驟）。
3. **git push race**：cron 每天 push，本地改動前先 `git pull --rebase`；alpha.db 是 binary，衝突時 cron 版為準（`git checkout --theirs`）。
4. **yfinance 盤中 K 棒不完整**：scanner 已防護；其他新模組要抓價也要注意。
5. **月曆 join 斷裂**：任何動到「日期語義」的改動，先想 §6。
6. **AI 幻覺**：股名（tw_stock_lookup 校正）、報價（sanity check）、stop/target 數量級（sanitize）都有防護——新增欄位時記得跟上。
7. **Gemini JSON 會帶引用標記 [1][2]** → base.py 已清，別繞過 call_claude 自己 parse。
8. **GitHub Pages 偶發 "Deployment failed, try again later"** → re-run failed jobs 即可，非 code 問題。
9. **hub/PWA cache 頑固**：資料 fetch 一律 no-store + query bust；手機問題先叫使用者刪 icon 重加。
10. **memory 規則**：工作區 MEMORY.md 只在使用者要求時寫；LESSONS 類寫制度正本 `C:\Users\USER\.agents\institution\LESSONS.md`。

## 12. 驗證方法

- `GEMINI_API_KEY=smoke-test-stub python -m tests.smoke`（5 條）
- `python scanner.py`（收盤後）→ 看 top5 是否為「貼支撐+基本面健康」型
- `python -X utf8 outcome_tracker.py` → 手動結算 + 績效摘要
- `python fundamental_feed.py` → 基本面覆蓋率
- `python tw_sector_lookup.py --refresh` → sector map 更新
- 本地 preview：`.claude/launch.json` 的 `investment-ai`（port 3009，含 MIME map）

## 13. Roadmap（未做，按價值排序）

1. 寶藏側：monthly screener 的 cron-job.org 觸發**待使用者設定**（每月 11 日）
2. Discord 推播：`DISCORD_WEBHOOK_URL` secret 未確認是否已設
3. watchlist.json ↔ 觀察名單.md 一致性檢查器（防手動同步飄移）
4. pick 事後回顧頁（結案 7 天後自動變「預測 vs 實際」對照）
5. 主題重疊偵測（AI 資料中心等跨產業主題，correlation 目前只看產業）
6. 美股持倉 × 台股 pick 跨市場 correlation
7. 自製 SVG sparkline 取代 TradingView CTA
8. swing era 累積 ≥10 筆後第一次正式審判（預估 2026-09~10）
