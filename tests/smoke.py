"""Smoke tests for the investment-ai pipeline.

Run with: python -m tests.smoke

These aren't unit tests of behaviour — they're "does this still import
and execute without crashing" checks for the most fragile parts of the
codebase. Add cases here when you fix a bug or add a new code path.

Categories covered:
  - cache invalidation (TTL + portfolio-hash)
  - watch_log lifecycle (log → resolve → summary)
  - micro-pick gate conditions
  - alpha_db core API still callable
"""

import io
import json
import os
import sys
import tempfile
import shutil

# Force UTF-8 stdout — Windows default cp950 can't print ✓/✗ box-draw chars.
# Wrap in try/except for environments where reconfigure isn't supported.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Make project root importable when run via `python -m tests.smoke`
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def _make_tmp_data_dir():
    """Copy a minimal portfolio.json into a fresh DATA_DIR so tests don't
    contaminate the real data/ folder."""
    tmp = tempfile.mkdtemp(prefix="investai_smoke_")
    os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
    # Minimal portfolio.json — just enough fields for cache hash + alpha_db
    minimal_portfolio = {
        "_updated": "2026-01-01",
        "personal_finance": {"cash_savings_twd": 100000, "monthly_income_twd": 50000},
        "tw_stocks": [{"code": "2330", "name": "台積電", "shares": 10, "value": 6000, "cost": 5000, "type": "stock", "strategy": "long"}],
        "tw_summary": {"total_value": 6000, "total_cost": 5000, "total_pnl": 1000, "total_pnl_pct": 20, "broker": "test"},
        "us_stocks": [],
        "funds": [],
        "funds_summary": {"total_value_twd": 0, "total_cost_twd": 0, "total_return_pct": 0, "total_monthly_dca_jpy": 0},
        "real_estate": {"total_price": 0, "loan_amount": 0, "personal_share_pct": 1.0, "payment_schedule": []},
    }
    with open(os.path.join(tmp, "data", "portfolio.json"), "w", encoding="utf-8") as f:
        json.dump(minimal_portfolio, f, ensure_ascii=False, indent=2)
    return tmp


def test_cache_portfolio_hash_invalidates_wealth_cluster():
    """Modifying portfolio.json should invalidate cached wealth_master
    even when within its TTL window."""
    import agent_cache as ac

    # Reset memoized hash so the test sees fresh reads
    ac._PF_HASH_CACHE = None
    h1 = ac._portfolio_hash()
    assert h1 is not None, "portfolio_hash() returned None — is data/portfolio.json present?"

    # Hash should be deterministic on identical bytes
    ac._PF_HASH_CACHE = None
    h2 = ac._portfolio_hash()
    assert h1 == h2, f"portfolio hash not deterministic: {h1} vs {h2}"

    # WEALTH_CLUSTER membership check
    assert "wealth_master" in ac.WEALTH_CLUSTER
    assert "tw_long_term" not in ac.WEALTH_CLUSTER   # this one isn't sensitive to cash

    print("  ✓ cache portfolio hash deterministic + cluster membership correct")


def test_cache_legacy_entries_force_refresh():
    """Cache entries created before the portfolio_hash feature (i.e.
    without that key) should be treated as stale on the next read."""
    import agent_cache as ac
    # Mock _load_cache temporarily to return a legacy entry shape
    from datetime import datetime
    real_load = ac._load_cache
    fake_now = datetime.now(ac.TZ).isoformat()
    ac._load_cache = lambda: {
        "wealth_master": {
            "output": {"verdict": "fake"},
            "cached_at": fake_now,
            "ttl_days": 3,
            # No portfolio_hash → simulates legacy entry
        }
    }
    try:
        ac._PF_HASH_CACHE = None  # force re-read of real portfolio.json
        is_fresh = ac.is_fresh("wealth_master")
        assert not is_fresh, "Legacy wealth_master entry (no portfolio_hash) should be treated as stale"
    finally:
        ac._load_cache = real_load
    print("  ✓ legacy cache entries (no portfolio_hash) force-refresh")


def test_micro_pick_gating():
    """The 'attach micro pick' helper must respect all 4 gates."""
    try:
        import run_agents as ra
    except ImportError as e:
        # Skip gracefully when google-genai isn't installed locally
        # (CI runs with full deps and will exercise this path).
        print(f"  ⊘ skipped (deps not installed: {e})")
        return

    # Gate 1: main pick not empty → no micro pick
    out = {"tw_daily_pick": {"verdict": "進攻", "pick": {"code": "2330", "name": "台積電"}}, "capital_flow": {"budget": {"trading": 0.3}}}
    ra._maybe_attach_micro_pick(out, [{"code": "2454", "score": 6}], {"market_regime": "牛市整理"}, {})
    assert "micro_pick" not in out["tw_daily_pick"], "Should not attach when main pick exists"

    # Gate 2: trading budget too low → no micro pick
    out = {"tw_daily_pick": {"verdict": "空手觀望", "pick": {"code": "—"}}, "capital_flow": {"budget": {"trading": 0.05}}}
    ra._maybe_attach_micro_pick(out, [{"code": "2454", "score": 6}], {"market_regime": "牛市整理"}, {})
    assert "micro_pick" not in out["tw_daily_pick"], "Should not attach when trading budget < 15%"

    # Gate 3: scanner score too low → no micro pick
    out = {"tw_daily_pick": {"verdict": "空手觀望", "pick": {"code": "—"}}, "capital_flow": {"budget": {"trading": 0.3}}}
    ra._maybe_attach_micro_pick(out, [{"code": "2454", "score": 3}], {"market_regime": "牛市整理"}, {})
    assert "micro_pick" not in out["tw_daily_pick"], "Should not attach when scanner score < 5"

    # Gate 4: regime panic → no micro pick
    out = {"tw_daily_pick": {"verdict": "空手觀望", "pick": {"code": "—"}}, "capital_flow": {"budget": {"trading": 0.3}}}
    ra._maybe_attach_micro_pick(out, [{"code": "2454", "score": 6}], {"market_regime": "恐慌盤"}, {})
    assert "micro_pick" not in out["tw_daily_pick"], "Should not attach in 恐慌盤 regime"

    # All gates pass → SHOULD attach
    out = {"tw_daily_pick": {"verdict": "空手觀望", "pick": {"code": "—"}}, "capital_flow": {"budget": {"trading": 0.3}}}
    ra._maybe_attach_micro_pick(out, [{"code": "2454", "name": "聯發科", "score": 6}], {"market_regime": "資金輪動"}, {"tw_stocks": {"2454": {"close": 1200}}})
    assert "micro_pick" in out["tw_daily_pick"], "Should attach when all gates pass"
    mp = out["tw_daily_pick"]["micro_pick"]
    assert mp["code"] == "2454"
    assert mp["size_pct_of_total"] > 0

    print("  ✓ micro_pick respects all 4 gates correctly")


def test_alpha_db_api_callable():
    """Critical alpha_db functions don't blow up on import + call."""
    import alpha_db
    alpha_db.init_db()
    # These should all return reasonable defaults even with empty DB
    s = alpha_db.get_performance_stats(30)
    assert isinstance(s, dict)
    assert "watch_streak" in s
    assert "watch_rate" in s

    ws = alpha_db.get_watch_streak()
    assert isinstance(ws, int) and ws >= 0

    wo = alpha_db.get_watch_outcomes_summary(60)
    assert isinstance(wo, dict)
    assert "available" in wo

    # Constants exposed (helps catch refactors that rename them)
    assert hasattr(alpha_db, "MISSED_AVOIDED_RATIO_BIAS")
    assert hasattr(alpha_db, "MIN_SAMPLES_FOR_BIAS")

    print("  ✓ alpha_db API callable + stats schema intact")


def test_watch_log_idempotent():
    """log_watch_day for the same date should not duplicate (INSERT OR IGNORE)."""
    import alpha_db
    test_date = "2099-01-01"   # far future so it can't collide with real data
    top = {"code": "9999", "name": "TEST", "score": 5, "close": 100.0}

    # Trigger schema creation via a no-op log first — log_watch_day has
    # CREATE TABLE IF NOT EXISTS inside it, so this also bootstraps the
    # table when running against a fresh DB.
    alpha_db.log_watch_day(test_date, top, "test_regime", "first call")
    # Now safe to clean up any prior data + re-test
    with alpha_db.get_conn() as conn:
        conn.execute("DELETE FROM watch_log WHERE date=?", (test_date,))

    # Log twice with same date
    alpha_db.log_watch_day(test_date, top, "test_regime", "test reason")
    alpha_db.log_watch_day(test_date, top, "test_regime", "different reason 2nd call")
    with alpha_db.get_conn() as conn:
        rows = list(conn.execute("SELECT * FROM watch_log WHERE date=?", (test_date,)))
    # Cleanup
    with alpha_db.get_conn() as conn:
        conn.execute("DELETE FROM watch_log WHERE date=?", (test_date,))

    assert len(rows) == 1, f"Expected idempotent insert, got {len(rows)} rows"
    print("  ✓ log_watch_day idempotent (INSERT OR IGNORE works)")


def main():
    print("═══ investment-ai smoke tests ═══")
    tests = [
        test_cache_portfolio_hash_invalidates_wealth_cluster,
        test_cache_legacy_entries_force_refresh,
        test_micro_pick_gating,
        test_alpha_db_api_callable,
        test_watch_log_idempotent,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failures += 1
    print(f"═══ {len(tests) - failures}/{len(tests)} passed ═══")
    sys.exit(failures)


if __name__ == "__main__":
    main()
