"""
Alpha Database — SQLite 持久化層
儲存每次推薦記錄、結果、以及每日反思
"""

import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "alpha.db")


def get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS picks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT NOT NULL,          -- 推薦日期 YYYY-MM-DD
            stock_code      TEXT NOT NULL,          -- 股票代號，空手=NONE
            stock_name      TEXT,
            entry_zone      TEXT,
            stop_loss       TEXT,
            target          TEXT,
            hold_days       TEXT,
            regime          TEXT,                   -- 推薦當日體制
            risk_level      TEXT,
            signals         TEXT,                   -- JSON list of signal names
            verdict         TEXT,                   -- 推薦出手 / 謹慎試單 / 空手觀望
            confidence      REAL,
            ref_close       REAL,                   -- 推薦當日收盤（參考基準）
            close_next_day  REAL,                   -- 次日收盤（結算後填入）
            return_pct      REAL,                   -- (close_next_day - ref_close) / ref_close
            hit_target      INTEGER,                -- 1=達目標 0=未達 NULL=未結算
            hit_stop        INTEGER,                -- 1=觸停損
            success         INTEGER,                -- 1=成功(>0) 0=失敗 NULL=未結算
            resolved        INTEGER DEFAULT 0,
            resolved_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS reflections (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT NOT NULL,
            regime          TEXT,
            win_rate_7d     REAL,
            win_rate_30d    REAL,
            total_picks_30d INTEGER,
            summary         TEXT,
            key_findings    TEXT,                   -- JSON list
            regime_stats    TEXT,                   -- JSON dict {regime: {wins, total}}
            signal_stats    TEXT,                   -- JSON dict {signal: {wins, total}}
            created_at      TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_picks_date      ON picks(date);
        CREATE INDEX IF NOT EXISTS idx_picks_resolved  ON picks(resolved);
        CREATE INDEX IF NOT EXISTS idx_reflections_date ON reflections(date);
        """)


# ── Picks ─────────────────────────────────────────────────────────────────────

def save_pick(
    date: str,
    pick: dict,
    regime: dict,
    ref_close: float = None,
) -> int:
    """Insert a new pick record. Returns row id."""
    init_db()
    code = pick.get("code", "NONE")
    if not code or code == "—":
        code = "NONE"

    # Extract signal names from pick's core_logic keys that are truthy
    signals = []
    core = pick.get("core_logic", {})
    if core.get("capital_flow"):  signals.append("capital_flow")
    if core.get("sector"):        signals.append("ai_sector")
    if core.get("technical"):     signals.append("technical_breakout")
    if core.get("chips"):         signals.append("institutional_buy")

    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO picks
              (date, stock_code, stock_name, entry_zone, stop_loss, target,
               hold_days, regime, risk_level, signals, verdict, confidence, ref_close)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            date,
            code,
            pick.get("name", ""),
            pick.get("entry_zone", ""),
            pick.get("stop_loss", ""),
            pick.get("target", ""),
            pick.get("hold_days", ""),
            regime.get("market_regime", ""),
            regime.get("risk_level", ""),
            json.dumps(signals, ensure_ascii=False),
            pick.get("verdict", ""),
            float(pick.get("confidence", 0) or 0),
            ref_close,
        ))
        return cur.lastrowid


def resolve_pick(pick_id: int, close_next_day: float):
    """Fill in outcome for a pick after next-day close is known."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ref_close FROM picks WHERE id=?", (pick_id,)
        ).fetchone()
        if not row:
            return

        ref = row["ref_close"]
        ret_pct = None
        success = None
        if ref and ref > 0:
            ret_pct = round((close_next_day - ref) / ref * 100, 3)
            success = 1 if ret_pct > 0 else 0

        conn.execute("""
            UPDATE picks
            SET close_next_day=?, return_pct=?, success=?,
                resolved=1, resolved_at=?
            WHERE id=?
        """, (
            close_next_day, ret_pct, success,
            datetime.now(TZ).isoformat(), pick_id,
        ))


def get_unresolved_picks() -> list[dict]:
    """Return all picks where verdict != 空手觀望 and not yet resolved."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM picks
            WHERE resolved=0 AND stock_code != 'NONE'
            ORDER BY date ASC
        """).fetchall()
        return [dict(r) for r in rows]


def get_recent_picks(days: int = 30) -> list[dict]:
    """Return resolved picks from the last N days."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM picks
            WHERE resolved=1 AND stock_code != 'NONE'
            ORDER BY date DESC
            LIMIT ?
        """, (days,)).fetchall()
        return [dict(r) for r in rows]


# ── Performance stats ─────────────────────────────────────────────────────────

def get_performance_stats(days: int = 30) -> dict:
    """
    Compute win rates by regime and by signal for the last N resolved picks.
    Returns a dict ready for prompt injection.
    """
    init_db()
    picks = get_recent_picks(days)
    if not picks:
        return {"available": False, "total": 0}

    total = len(picks)
    wins = sum(1 for p in picks if p["success"] == 1)
    overall_rate = round(wins / total * 100, 1) if total else 0

    # Win rate by regime
    regime_stats: dict[str, dict] = {}
    for p in picks:
        r = p.get("regime") or "未知"
        if r not in regime_stats:
            regime_stats[r] = {"wins": 0, "total": 0}
        regime_stats[r]["total"] += 1
        if p["success"] == 1:
            regime_stats[r]["wins"] += 1

    # Win rate by signal
    signal_stats: dict[str, dict] = {}
    for p in picks:
        try:
            sigs = json.loads(p.get("signals") or "[]")
        except Exception:
            sigs = []
        for s in sigs:
            if s not in signal_stats:
                signal_stats[s] = {"wins": 0, "total": 0}
            signal_stats[s]["total"] += 1
            if p["success"] == 1:
                signal_stats[s]["wins"] += 1

    # Recent streak (last 7)
    recent7 = picks[:7]
    streak = "".join("✓" if p["success"] == 1 else "✗" for p in recent7)

    return {
        "available": True,
        "total": total,
        "wins": wins,
        "overall_rate": overall_rate,
        "regime_stats": regime_stats,
        "signal_stats": signal_stats,
        "recent_streak": streak,
    }


def format_stats_for_prompt(stats: dict) -> str:
    """Format performance stats into a compact string for agent prompts."""
    if not stats.get("available"):
        return ""

    lines = [
        f"【歷史績效（近{stats['total']}次推薦）】",
        f"整體勝率：{stats['wins']}/{stats['total']} = {stats['overall_rate']}%",
        f"近期走勢：{stats.get('recent_streak', 'N/A')}",
    ]

    regime_stats = stats.get("regime_stats", {})
    if regime_stats:
        lines.append("各體制勝率：")
        for regime, s in sorted(
            regime_stats.items(),
            key=lambda x: x[1]["wins"] / max(x[1]["total"], 1),
            reverse=True,
        ):
            rate = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0
            tag = "✓ 可積極" if rate >= 60 else ("△ 謹慎" if rate >= 45 else "✗ 避開")
            lines.append(f"  {regime}：{s['wins']}/{s['total']} = {rate}%  {tag}")

    signal_stats = stats.get("signal_stats", {})
    if signal_stats:
        lines.append("信號效力：")
        for sig, s in sorted(
            signal_stats.items(),
            key=lambda x: x[1]["wins"] / max(x[1]["total"], 1),
            reverse=True,
        ):
            rate = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0
            lines.append(f"  {sig}：{rate}% ({s['total']}次)")

    return "\n".join(lines)


# ── Reflections ───────────────────────────────────────────────────────────────

def log_system_decision(
    date: str,
    capital_flow: dict,
    master_verdict_before: str,
    master_verdict_after: str,
    constraint_violations: list,
    regime: str,
):
    """每次執行後記錄系統層決策，供未來 policy review 使用。"""
    init_db()
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_decisions (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                date                  TEXT NOT NULL,
                regime                TEXT,
                trading_budget_pct    REAL,
                portfolio_budget_pct  REAL,
                cash_budget_pct       REAL,
                flow_direction        TEXT,
                cf_override_count     INTEGER,
                master_verdict_raw    TEXT,
                master_verdict_final  TEXT,
                verdict_changed       INTEGER,
                constraint_count      INTEGER,
                constraint_details    TEXT,
                created_at            TEXT
            )
        """)
        budget = capital_flow.get("budget", {})
        conn.execute("""
            INSERT INTO system_decisions
              (date, regime, trading_budget_pct, portfolio_budget_pct, cash_budget_pct,
               flow_direction, cf_override_count,
               master_verdict_raw, master_verdict_final, verdict_changed,
               constraint_count, constraint_details, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            date, regime,
            budget.get("trading", 0),
            budget.get("portfolio", 0),
            budget.get("cash", 0),
            capital_flow.get("flow_direction", ""),
            capital_flow.get("override_count", 0),
            master_verdict_before,
            master_verdict_after,
            1 if master_verdict_before != master_verdict_after else 0,
            len(constraint_violations),
            json.dumps(constraint_violations, ensure_ascii=False),
            datetime.now(TZ).isoformat(),
        ))


def get_policy_drift_report(days: int = 30) -> dict:
    """
    分析近 N 天的系統決策模式。
    供人工定期審查，判斷是否需要調整 policy_config.json。
    """
    init_db()
    with get_conn() as conn:
        # Check table exists
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='system_decisions'"
        ).fetchone()
        if not exists:
            return {"available": False}

        rows = conn.execute("""
            SELECT * FROM system_decisions
            ORDER BY date DESC LIMIT ?
        """, (days,)).fetchall()

    if not rows:
        return {"available": False}

    rows = [dict(r) for r in rows]
    total = len(rows)

    # Constraint trigger frequency
    total_violations = sum(r["constraint_count"] for r in rows)
    verdict_changed = sum(r["verdict_changed"] for r in rows)

    # Capital flow distribution
    avg_trading  = sum(r["trading_budget_pct"] for r in rows) / total
    avg_cash     = sum(r["cash_budget_pct"] for r in rows) / total

    # Most common override reasons
    all_details = []
    for r in rows:
        try:
            all_details.extend(json.loads(r["constraint_details"] or "[]"))
        except Exception:
            pass

    reason_counts: dict[str, int] = {}
    for d in all_details:
        key = d.split(":")[0] if ":" in d else d
        reason_counts[key] = reason_counts.get(key, 0) + 1

    return {
        "available": True,
        "period_days": total,
        "total_violations": total_violations,
        "verdict_override_rate": round(verdict_changed / total * 100, 1),
        "avg_trading_budget_pct": round(avg_trading * 100, 1),
        "avg_cash_pct": round(avg_cash * 100, 1),
        "top_override_reasons": sorted(reason_counts.items(), key=lambda x: -x[1])[:5],
        "signal": _interpret_policy_drift(total_violations, total, avg_trading, verdict_changed),
    }


def _interpret_policy_drift(violations: int, total: int, avg_trading: float, verdict_changed: int) -> str:
    """給人工審查用的信號，不自動修改任何參數。"""
    signals = []
    violation_rate = violations / max(total, 1)
    override_rate  = verdict_changed / max(total, 1)

    if override_rate > 0.5:
        signals.append("⚠️ CIO verdict 超過 50% 被修正 → constraints 可能過嚴，考慮放寬閾值")
    elif override_rate < 0.05:
        signals.append("✓ constraints 鮮少觸發 → 目前設定偏寬鬆")

    if avg_trading < 0.10:
        signals.append("⚠️ 平均交易預算 < 10% → Capital Flow 長期保守，確認 wealth risk 是否真的高")
    elif avg_trading > 0.35:
        signals.append("✓ 交易預算充足，系統處於進攻模式")

    return " | ".join(signals) if signals else "✓ 系統行為在正常範圍"


def save_reflection(date: str, regime: str, reflection: dict, stats: dict):
    init_db()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO reflections
              (date, regime, win_rate_7d, win_rate_30d, total_picks_30d,
               summary, key_findings, regime_stats, signal_stats, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            date,
            regime,
            None,
            stats.get("overall_rate"),
            stats.get("total"),
            reflection.get("summary", ""),
            json.dumps(reflection.get("key_findings", []), ensure_ascii=False),
            json.dumps(stats.get("regime_stats", {}), ensure_ascii=False),
            json.dumps(stats.get("signal_stats", {}), ensure_ascii=False),
            datetime.now(TZ).isoformat(),
        ))


def get_latest_reflection() -> dict | None:
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reflections ORDER BY date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
