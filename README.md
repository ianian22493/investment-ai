# 🏛 Yuzu Capital OS

> Yuzu Capital OS is a personal AI-operated investment intelligence system.
>
> It combines market data ingestion, multi-agent analysis, capital flow governance,
> learning feedback loops, and spatial operating interfaces into a unified decision environment.

🌐 **Live:** [ianian22493.github.io/investment-ai](https://ianian22493.github.io/investment-ai/)

---

## What It Does

A virtual hedge fund running entirely on a personal codebase:

- **14 specialized AI agents** organized into Trading Desk / Portfolio Desk / Wealth Desk, headed by a CIO synthesizer
- **Rule-based safety layer** — Regime engine, Signal Fusion, Capital Flow Engine, and Constraint Validator run as pure Python (no LLM) to keep the system's behavior auditable and bounded
- **Learning feedback loop** — Every pick is logged to SQLite; outcomes resolve into win-rate, regime stats, and signal effectiveness, which feed back into the next day's prompts
- **Isometric operating interface** — Not a tab dashboard. A 7-room "office" you walk into, designed for the emotional posture of **low-frequency, contemplative, immersive, rational** decision-making

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      ⏰ TRIGGER                                  │
│              cron-job.org · twice daily (pre + post market)      │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  📡 DATA INGEST                                  │
│  fetch_market_data.py + scanner.py                              │
│  ──────────────────────────────────────                          │
│  • Yahoo Finance  — TAIEX / S&P 500 / NASDAQ / VIX / tickers    │
│  • TWSE BFI82U    — Institutional net flow (aggregate)          │
│  • TWSE MI_INDEX  — Market breadth (advance / decline)          │
│  • TWSE MI_MARGN  — Margin balance (leverage health)            │
│  • TWSE TWT38U    — Per-stock institutional flow                │
│  • FinMind        — Per-stock technical indicators              │
│  • Frankfurter    — FX (USD/TWD, JPY/USD)                       │
│  • Google News    — TW + US headlines                            │
│  • Shioaji        — Live TW portfolio quotes                    │
│  → data/market_data.json, data/candidate_stocks.json            │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  🧠 LEARNING LAYER                               │
│  outcome_tracker.py + alpha_db.py (SQLite)                      │
│  ──────────────────────────────────────                          │
│  • resolve_pending() — settle yesterday's picks                  │
│  • get_performance_stats() — win-rate / streak / best / worst    │
│  • get_latest_reflection() — inject into next prompt             │
│  Tables: picks · reflections · system_decisions                  │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              🤖 AGENT PIPELINE (run_agents.py)                   │
│                                                                  │
│  Phase 1 — Market Context (shared)                              │
│    market_overview · news_sentiment                              │
│                                                                  │
│  Phase 2 — Specialist Agents (parallel)                         │
│    tw_short_term   ← Trading Desk (every run)                    │
│    tw_long_term    ← Portfolio Desk (cached 3-5d)                │
│    us_portfolio    ← Portfolio Desk (cached 3-5d)                │
│    fx_fund         ← Wealth Desk (cached 5-7d)                   │
│    asset_allocation ← Wealth Desk (cached 5-7d)                  │
│                                                                  │
│  Phase 3 — Devil's Advocate                                     │
│    devils_advocate — critiques all upstream agents               │
│                                                                  │
│  Phase 3.5 — Signal Fusion (pure Python, no LLM)                │
│    signal_fusion.compute() → 10-dim signal vector               │
│                                                                  │
│  Phase 4 — Post-Market (13:00+ only)                            │
│    tw_daily_pick   — tomorrow's pick (with entry/stop/target)    │
│    reflection      — yesterday's lesson                          │
│                                                                  │
│  Phase 5 — Desk Masters                                         │
│    trading_master · portfolio_master · wealth_master             │
│                                                                  │
│  Phase 6 — Capital Flow Engine (pure Python)                    │
│    Computes TRD/PRT/CSH budget + flow direction + overrides     │
│                                                                  │
│  Phase 7 — Master Agent (CIO)                                   │
│    Final verdict — synthesizes all desks                         │
│                                                                  │
│  Phase 8 — Constraint Validator (pure Python)                   │
│    Enforces hard rules · downgrades verdict if risk too high     │
│                                                                  │
│  → data/analysis.json (frontend reads this)                     │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│           📤 GitHub Actions · Commit · Pages Rebuild             │
│  workflow: .github/workflows/daily_analysis.yml                 │
│  Deploys to ianian22493.github.io/investment-ai/                │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              🎨 FRONTEND (index.html)                            │
│                                                                  │
│  ┌────────┬────────────────────────┬──────────────┐             │
│  │  LEFT  │   CENTER ISO MAP        │   RIGHT      │             │
│  │  NAV   │   (7 rooms)             │   SIDEBAR    │             │
│  ├────────┼────────────────────────┼──────────────┤             │
│  │OVERVIEW│  EXEC | LOBBY | RESCH  │ DATA HEALTH  │             │
│  │        │  RISK | LOBBY | WLTH   │ ──────────   │             │
│  │6 rooms │     TRADING |  ARCHIVE │ TODAY'S      │             │
│  │        │                        │  BRIEFING    │             │
│  │SYSTEMS │  Hybrid agents:         │ ──────────   │             │
│  │ ⚡ DLOG │  SVG silhouette         │ LIVE         │             │
│  │ 📊 SMAT│  + AI PNG overlay      │  TRIGGERS    │             │
│  │ 🧠 AFLO│                        │ ──────────   │             │
│  │ ⚙ SET │                        │ AGENT        │             │
│  └────────┴────────────────────────┴──────────────┘             │
│                                                                  │
│  • Deep links · ?room=trading · ?system=signal-matrix           │
│  • localStorage cache · 24h TTL · offline-capable               │
│  • Atmosphere · film grain · vignette · slow pulse              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Three-Desk Agent Design

```
                            ┌──────────────────────┐
                            │   MASTER / CIO       │  ← final synthesis
                            │   master_agent       │
                            └──────────┬───────────┘
         ┌───────────────────┬─────────┴─────────┬───────────────────┐
         ▼                   ▼                   ▼                   ▼
┌────────────────┐  ┌────────────────┐  ┌──────────────────┐  ┌──────────────┐
│ TRADING DESK   │  │ PORTFOLIO DESK │  │ WEALTH DESK      │  │ DEVIL'S ADV  │
│ trading_master │  │ portfolio_     │  │ wealth_master    │  │ devils_      │
│                │  │  master        │  │                  │  │  advocate    │
├────────────────┤  ├────────────────┤  ├──────────────────┤  │ (opposition) │
│ tw_short_term  │  │ tw_long_term   │  │ fx_fund          │  └──────────────┘
│ tw_daily_pick⭐│  │ us_portfolio   │  │ asset_allocation │
└────────────────┘  └────────────────┘  └──────────────────┘
         ▲                     ▲                  ▲
         └─────────────────────┴──────────────────┘
                               │
              ┌────────────────┴────────────────┐
              │  COMMON CONTEXT (upstream)      │
              │  market_overview                │
              │  news_sentiment                 │
              │  regime_engine (rule-based)     │
              │  signal_fusion (rule-based)     │
              └─────────────────────────────────┘
```

---

## Signal Vector — 10 Dimensions

Pure-Python fusion of all market data + agent outputs into a single decision vector:

| Signal | Range | Source |
|--------|-------|--------|
| `market_regime_score` | −1.0 → +1.0 | Regime name + risk level (panic ↔ AI主升段) |
| `trend_strength` | 0.0 → 1.0 | TAIEX daily Δ + market_overview verdict |
| `risk_pressure` | 0.0 → 1.0 *inverted* | VIX + tw_short_term + devils_advocate consensus |
| `volatility_risk` | 0.0 → 1.0 *inverted* | VIX (70%) + TAIEX intraday |
| `ai_sector_strength` | 0.0 → 1.0 | Inferred from AI keyword frequency in headlines |
| `scanner_momentum` | 0.0 → 1.0 | Average technical score across 10 candidates |
| `confidence_score` | 0.0 → 1.0 | Average agent confidence (penalty per ERROR) |
| `foreign_flow_strength` | 0.0 → 1.0 | TWSE BFI82U foreign net (±50B NTD normalized) |
| `breadth_score` | 0.0 → 1.0 | TWSE advance / (advance + decline), stocks only |
| `liquidity_score` | 0.0 → 1.0 *inverted* | Margin balance % change (deleveraging = healthier) |

Every value is reproducible; `_sources` field tracks the exact calculation.

---

## Rule Layer — The System's Spine

Mixed between LLM agents are four pure-Python modules. They make the system **auditable and bounded**:

| Module | Responsibility |
|--------|---------------|
| `regime_engine.py` | Classifies market state (趨勢盤 / 區間盤 / 恐慌盤 / AI主升段 / …) using VIX, TAIEX, NASDAQ, AI keyword hits |
| `signal_fusion.py` | Compresses all upstream signals into the 10-dim numeric vector above |
| `capital_flow.py` | Computes TRD/PRT/CSH budget allocation, override flags, and flow direction |
| `constraint_validator.py` | Enforces hard rules — downgrades the CIO's verdict if risk thresholds are breached |

LLMs handle creativity. Rules handle veto power.

---

## File Layout

```
investment-ai/
├── fetch_market_data.py     ← Market data ingestion
├── scanner.py               ← Cross-market scan → 15 candidates
├── run_agents.py            ← Pipeline orchestrator
├── outcome_tracker.py       ← Pick resolution (hit target/stop)
├── alpha_db.py              ← SQLite wrapper (picks / reflections / decisions)
├── agent_cache.py           ← Partial agent caching to save API calls
│
├── agents/                  ← 14 modules
│   ├── base.py              ← Gemini API wrapper
│   ├── regime_engine.py     ← (rule) Market regime classification
│   ├── signal_fusion.py     ← (rule) 10-dim signal vector
│   ├── capital_flow.py      ← (rule) Budget allocation
│   ├── constraint_validator.py ← (rule) Verdict guardrails
│   ├── market_overview.py
│   ├── news_sentiment.py
│   ├── tw_short_term.py     ← Trading desk specialist
│   ├── tw_long_term.py      ← Portfolio desk specialist
│   ├── us_portfolio.py      ← Portfolio desk specialist
│   ├── fx_fund.py           ← Wealth desk specialist
│   ├── asset_allocation.py  ← Wealth desk specialist
│   ├── devils_advocate.py
│   ├── tw_daily_pick.py     ← Post-market pick (with entry / stop / target / R:R)
│   ├── trading_master.py    ← Desk head
│   ├── portfolio_master.py  ← Desk head
│   ├── wealth_master.py     ← Desk head
│   ├── master_agent.py      ← CIO — final synthesis
│   └── reflection.py        ← Post-market self-reflection
│
├── data/                    ← Generated artifacts (committed to repo)
│   ├── analysis.json        ← Frontend reads this
│   ├── market_data.json
│   ├── candidate_stocks.json
│   ├── signal_vector.json
│   ├── agent_cache.json
│   ├── policy_config.json
│   ├── portfolio.json
│   └── alpha.db             ← SQLite: picks + reflections + system_decisions
│
├── assets/characters/       ← AI-generated character PNGs
│   ├── cio-back.png
│   ├── tm-back.png
│   ├── tw-back.png
│   ├── da-left.png
│   ├── pm-back.png
│   ├── wm-front.png
│   └── concierge-front.png
│
├── index.html               ← Single-file frontend (SVG 70 / AI 20 / lighting 10)
├── favicon.svg
├── .github/workflows/
│   └── daily_analysis.yml
└── requirements.txt
```

---

## Schedule

Both runs are triggered externally by [cron-job.org](https://cron-job.org) — GitHub's built-in cron is unreliable for time-sensitive market workflows.

| Time (Asia/Taipei) | Trigger | What Runs |
|---|---|---|
| **08:30** pre-market | cron-job.org | fetch_market_data + run_agents (Phase 4 skipped) |
| **16:30** post-market | cron-job.org | fetch_market_data + run_agents (full pipeline + tw_daily_pick + reflection) |

cron-job.org drifts 5-15 minutes after the scheduled time, so 08:30 produces results around 08:35-08:45 — early enough to read before the 09:00 market open.

Note: TWSE post-market endpoints publish at staggered times — MI_INDEX (breadth) ≈ 13:30, BFI82U (institutional) ≈ 14:30, MI_MARGN (margin) ≈ 15:30. 16:30 catches all three on the same day. `fetch_market_data.py` also has a 7-day trading-day fallback per endpoint so weekends and holidays still produce a complete signal vector.

---

## Frontend Architecture

Single-file `index.html`. No build step. Reads `data/*.json` directly.

```
index.html
├── HTML structure
├── CSS (inline)
│   ├── 3-column app grid
│   ├── Isometric office (rotateX 28° · rotateZ −12°)
│   ├── Atmosphere layer (grain · vignette · slow pulse · bloom)
│   └── Component styles
└── JS (inline)
    ├── Data load (analysis.json + candidate_stocks.json)
    ├── localStorage cache (24h TTL, offline-capable)
    ├── renderOffice()           — 7-room iso map
    ├── renderSidebar()          — Right-side 4 modules
    ├── Hybrid agent rendering   — SVG silhouette + AI PNG overlay
    ├── 6 room renderers         — Click to enter
    ├── 4 system renderers       — DECISION LOG / SIGNAL MATRIX / AGENT FLOWS / SETTINGS
    ├── URL routing              — ?room= / ?system=
    └── Atmosphere settings      — sliders for grain / vignette / saturation
```

**Emotional north stars (locked in design):**
- 低頻 (low frequency) — slow animations, generous spacing
- 理性 (rational) — monospace data, clear hierarchy
- 沉浸 (immersive) — film grain, vignette, depth
- 沈思 (contemplative) — slow transitions, no urgent reds without reason

**Anti-goal:** never feel rushed.

---

## Deployment

| Repo | Live URL | Purpose |
|------|----------|---------|
| **investment-ai** (this) | https://ianian22493.github.io/investment-ai/ | Yuzu Capital OS — the iso office |
| investment-dashboard | https://ianian22493.github.io/investment-dashboard/ | Legacy Bloomberg-tab dashboard |
| daily-brief | https://ianian22493.github.io/daily-brief/ | Daily news briefing (links here) |

---

## First-time Setup

If you're forking this for your own use, here's the full setup. Plan ~30 minutes end-to-end.

### 1. Fork & clone

```bash
# Fork on GitHub, then:
git clone https://github.com/<your-username>/investment-ai.git
cd investment-ai
```

### 2. Get API keys

| Service | What for | Where to get it |
|---|---|---|
| **Gemini API** | Powers all 14 agents | https://aistudio.google.com/apikey · Free tier ≈ 20 RPD on `gemini-2.5-flash` (enough for the 2x/day schedule) |
| **Shioaji** (optional) | Live TW portfolio quotes | https://sinotrade.com.tw · API key + secret from 永豐金證券. Skip if you don't need live positions — yfinance will be used as fallback. |

### 3. Add GitHub secrets

In your fork: `Settings → Secrets and variables → Actions → New repository secret`

```
GEMINI_API_KEY        = (required)
SHIOAJI_API_KEY       = (optional)
SHIOAJI_SECRET_KEY    = (optional)
```

### 4. Enable GitHub Pages

`Settings → Pages → Source: Deploy from a branch · Branch: main · Folder: / (root) · Save`

Wait ~1 min, your site goes live at `https://<your-username>.github.io/investment-ai/`.

### 5. Set up cron-job.org triggers (the actual scheduler)

GitHub's built-in cron drifts and skips runs. Use [cron-job.org](https://cron-job.org) instead — it triggers the workflow via API.

a. Get a GitHub PAT with `repo` + `workflow` scope: https://github.com/settings/tokens

b. Create two jobs on cron-job.org, both pointing at:
   ```
   URL:     https://api.github.com/repos/<your-username>/investment-ai/actions/workflows/daily_analysis.yml/dispatches
   Method:  POST
   Headers: Authorization: Bearer <PAT>
            Accept: application/vnd.github+json
   Body:    {"ref":"main"}
   ```

c. Schedule (Asia/Taipei):
   - **Pre-market** · 08:30 weekdays (Mon-Fri) — gives ~25 min before 09:00 market open even with cron-job.org's 5-15 min drift
   - **Post-market** · 16:30 weekdays — late enough to catch all TWSE post-market data (MI_INDEX, BFI82U, MI_MARGN publish between 13:30-15:30)

### 6. Customize your portfolio

Edit `data/portfolio.json`:
```json
{
  "tw_stocks": { "2330": { "shares": 1000, "avg_cost": 800 } },
  "us_stocks": { "NVDA": { "shares": 10, "avg_cost": 450 } }
}
```

And the watchlists in `fetch_market_data.py`:
```python
TW_CODES   = ["00692", "2330", ...]      # your TW holdings
US_TICKERS = ["AMZN", "NVDA", "TSLA", ...] # your US holdings
```

### 7. First run

Either trigger the cron-job.org job manually (button in their UI), or run locally to verify before deploying:

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...
export SHIOAJI_API_KEY=...        # optional
export SHIOAJI_SECRET_KEY=...     # optional

python fetch_market_data.py       # → data/market_data.json
python scanner.py                 # → data/candidate_stocks.json
python run_agents.py              # → data/analysis.json (this calls Gemini)

# Serve frontend
python -m http.server 8000
# → http://localhost:8000
```

If the first run succeeds, the data files commit themselves on the next GitHub Actions run.

---

## Personal Use Disclaimer

This is a **personal tool** built by [@ianian22493](https://github.com/ianian22493) for self-directed investment decisions. It is not financial advice, not a product, and not maintained for general use. The system can be wrong; the human operator remains responsible for every trade.

The architecture is shared openly because thinking-tools are most useful when the thinking is visible.
