"""
Investment AI Orchestrator
Runs all agents in sequence and writes analysis.json for the frontend.
"""

import json, os, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo

RATE_LIMIT_SLEEP = 15  # Gemini 2.5 Flash free tier: 5 RPM → 1 call per 12s, use 15s for safety

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

from agents import (
    market_overview, news_sentiment, tw_short_term, tw_long_term,
    us_portfolio, fx_fund, asset_allocation,
    devils_advocate, master_agent, tw_daily_pick,
)


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save_json(data: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_all():
    t0 = datetime.now(TZ)
    print(f"\n{'='*60}")
    print(f"Investment AI — {t0.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"{'='*60}\n")

    # Load data
    market_data_path = os.path.join(DATA_DIR, "market_data.json")
    portfolio_path = os.path.join(DATA_DIR, "portfolio.json")

    if not os.path.exists(market_data_path):
        print("ERROR: market_data.json not found. Run fetch_market_data.py first.")
        sys.exit(1)

    market_data = load_json(market_data_path)
    portfolio = load_json(portfolio_path)

    # Build live portfolio: replace static TW values with today's computed prices
    pv = market_data.get("portfolio_value", {})
    portfolio_live = {**portfolio}
    if pv.get("tw_stocks_live"):
        portfolio_live["tw_stocks"] = pv["tw_stocks_live"]
    if pv.get("tw_summary_live"):
        portfolio_live["tw_summary"] = pv["tw_summary_live"]

    outputs = {}

    # Phase 1: Independent agents
    phase1 = [
        ("market_overview",  lambda: market_overview.run(market_data, portfolio_live)),
        ("news_sentiment",   lambda: news_sentiment.run(market_data, portfolio_live)),
    ]
    for name, fn in phase1:
        print(f"  Running: {name}...")
        outputs[name] = fn()
        print(f"    → {outputs[name].get('verdict')} (confidence: {outputs[name].get('confidence')})")
        print(f"    ⏳ rate-limit sleep {RATE_LIMIT_SLEEP}s...")
        time.sleep(RATE_LIMIT_SLEEP)

    # Phase 2: Agents that depend on market_overview + news_sentiment
    news = outputs.get("news_sentiment", {})
    phase2 = [
        ("tw_short_term",   lambda: tw_short_term.run(market_data, portfolio_live, outputs["market_overview"], news)),
        ("tw_long_term",    lambda: tw_long_term.run(market_data, portfolio_live, outputs["market_overview"], news)),
        ("us_portfolio",    lambda: us_portfolio.run(market_data, portfolio_live, outputs["market_overview"], news)),
        ("fx_fund",         lambda: fx_fund.run(market_data, portfolio_live, outputs["market_overview"], news)),
        ("asset_allocation",lambda: asset_allocation.run(market_data, portfolio_live, outputs["market_overview"], news)),
    ]
    for name, fn in phase2:
        print(f"  Running: {name}...")
        outputs[name] = fn()
        print(f"    → {outputs[name].get('verdict')} (confidence: {outputs[name].get('confidence')})")
        print(f"    ⏳ rate-limit sleep {RATE_LIMIT_SLEEP}s...")
        time.sleep(RATE_LIMIT_SLEEP)

    # Phase 3: Devil's Advocate sees all Phase 1+2 outputs
    print(f"  Running: devils_advocate...")
    outputs["devils_advocate"] = devils_advocate.run(outputs)
    print(f"    → {outputs['devils_advocate'].get('verdict')}")
    print(f"    ⏳ rate-limit sleep {RATE_LIMIT_SLEEP}s...")
    time.sleep(RATE_LIMIT_SLEEP)

    # Phase 4: 盤後精選（只在收盤後那次執行，台灣時間 13:00 後）
    if t0.hour >= 13:
        print(f"  Running: tw_daily_pick (盤後精選)...")
        outputs["tw_daily_pick"] = tw_daily_pick.run(
            market_data, portfolio_live, outputs["market_overview"], outputs.get("news_sentiment", {})
        )
        pick = outputs["tw_daily_pick"]
        print(f"    → {pick.get('verdict')} | 推薦: {pick.get('pick',{}).get('name','?')} ({pick.get('pick',{}).get('code','?')})")
        print(f"    ⏳ rate-limit sleep {RATE_LIMIT_SLEEP}s...")
        time.sleep(RATE_LIMIT_SLEEP)
    else:
        print(f"  Skipping: tw_daily_pick (開盤前不執行，僅盤後使用)")

    # Phase 5: Master Agent integrates everything
    print(f"  Running: master_agent...")
    outputs["master"] = master_agent.run(outputs)
    print(f"    → FINAL: {outputs['master'].get('verdict')}")

    # Build final analysis.json
    analysis = {
        "generated_at": t0.isoformat(),
        "market_snapshot": {
            "taiex": market_data["indices"].get("taiex", {}),
            "sp500": market_data["indices"].get("sp500", {}),
            "vix": market_data["indices"].get("vix", {}),
            "usd_twd": market_data["fx"].get("usd_twd"),
            "jpy_per_usd": market_data["fx"].get("jpy_per_usd"),
            "twd_per_jpy": market_data["fx"].get("twd_per_jpy"),
        },
        "portfolio_value": market_data.get("portfolio_value", {}),
        "agents": outputs,
    }

    out_path = os.path.join(DATA_DIR, "analysis.json")
    save_json(analysis, out_path)

    elapsed = (datetime.now(TZ) - t0).total_seconds()
    print(f"\n{'='*60}")
    print(f"Complete in {elapsed:.1f}s → data/analysis.json")
    print(f"Master verdict: {outputs['master'].get('verdict')}")
    print(f"{'='*60}\n")

    # Print master summary to stdout for GitHub Actions log
    master = outputs.get("master", {})
    print("MASTER SUMMARY:")
    print(master.get("summary", ""))
    for i, rec in enumerate(master.get("recommendations", [])[:3], 1):
        print(f"  {i}. [{rec.get('urgency')}] {rec.get('action')} {rec.get('target')}: {rec.get('detail','')}")


if __name__ == "__main__":
    run_all()
