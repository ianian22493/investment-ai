"""
Alpha Database — SQLite 持久化層
儲存每次推薦記錄、結果、以及每日反思
"""

import json
import os
import sqlite3
from datetime import datetime, date, timedelta
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
            close_next_day  REAL,                   -- 次日收盤（舊欄位，保留向後相容）
            return_pct      REAL,                   -- (exit_close - ref) / ref，新版以 exit_close 計
            hit_target      INTEGER,                -- 1=持有期間 high 觸目標
            hit_stop        INTEGER,                -- 1=持有期間 low 觸停損
            success         INTEGER,                -- 1=return_pct>0
            resolved        INTEGER DEFAULT 0,
            resolved_at     TEXT
        );
        -- v2 columns (full hold-period tracking, added 2026-06-02)
        -- ALTER TABLE doesn't IF NOT EXISTS in SQLite, so we try/except below.
        """)
        # Forward-compat migrations: add new columns if they don't exist yet.
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(picks)")}
        new_columns = [
            ("exit_close",       "REAL"),    # 退場日收盤
            ("exit_date",        "TEXT"),    # 退場日期
            ("exit_reason",      "TEXT"),    # 'stop' / 'target' / 'time' / 'pending'
            ("max_close",        "REAL"),    # 持有期間最高收盤
            ("min_close",        "REAL"),    # 持有期間最低收盤
            ("max_high",         "REAL"),    # 持有期間最高 high（看是否真的觸目標）
            ("min_low",          "REAL"),    # 持有期間最低 low（看是否真的觸停損）
            ("max_gain_pct",     "REAL"),    # (max_close - ref) / ref
            ("max_drawdown_pct", "REAL"),    # (min_close - ref) / ref
            ("hold_days_actual", "INTEGER"), # 實際交易日數
            # v3 (2026-07-08) benchmark tracking：pick 同期 0050 表現
            ("benchmark_ref_close",  "REAL"),    # 0050 在 pick 發布日的收盤
            ("benchmark_exit_close", "REAL"),    # 0050 在退場日的收盤
            ("benchmark_return_pct", "REAL"),    # 0050 同期報酬 %
            ("alpha_pct",            "REAL"),    # pick_return - benchmark_return
            # v4 (2026-07-22) 波段目標交易日：date=cron 決策日，target_date=
            # 可下單的隔一交易日（= pick 頁檔名）。7/6 檔名改版後兩者錯開，
            # 月曆 manifest 的勝負 join 因此斷裂——用這欄修復。
            ("target_date",          "TEXT"),
            # v5 (2026-07-22) 進場成交追蹤：pick 後 3 個交易日內價格要進
            # entry_zone 才算真的開倉。沒觸價 → exit_reason='not_filled'，
            # 不計入勝率（修正「假設 pick 日收盤一定買到」的樂觀偏誤）。
            ("entry_fill_date",      "TEXT"),    # 實際觸價成交日
            ("entry_fill_price",     "REAL"),    # 假設成交價（zone 中點 clip 到當日區間）
        ]
        for name, kind in new_columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE picks ADD COLUMN {name} {kind}")
        conn.executescript("""

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
    scanner_signals: list = None,
    benchmark_ref_close: float = None,
    verdict: str = None,
    target_date: str = None,
) -> int:
    """Insert a new pick record. Returns row id.

    benchmark_ref_close: 0050 收盤在 pick 日 — 用於後續算 alpha vs 大盤。
    verdict: 推薦出手/謹慎試單 — 在 pick_output 外層，呼叫端要自己傳
             （歷史 bug：曾從內層 pick dict 讀，永遠空字串）。
    target_date: 可下單的目標交易日（= pick 頁檔名日期）。
    """
    init_db()
    code = pick.get("code", "NONE")
    if not code or code == "—":
        code = "NONE"

    # Prefer scanner signals (precise); fall back to core_logic (coarse)
    # Swing 版 core_logic 欄位：trend_structure / sector / fundamental /
    # catalyst / chips / event_risk（短線版舊欄位保留 fallback 相容）
    SCANNER_SIGNAL_KEYS = ("trend_up", "above_ma60", "pullback_buy", "base_breakout", "vol_accumulate", "rsi_swing", "rs_20d_strong")
    if scanner_signals:
        signals = scanner_signals
    else:
        core = pick.get("core_logic", {})
        signals = []
        if core.get("trend_structure") or core.get("technical"):
            signals.append("trend_structure")
        if core.get("sector"):        signals.append("sector_flow")
        if core.get("fundamental"):   signals.append("fundamental")
        if core.get("catalyst"):      signals.append("catalyst")
        if core.get("chips"):         signals.append("institutional_buy")
        if core.get("capital_flow"):  signals.append("capital_flow")

    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO picks
              (date, stock_code, stock_name, entry_zone, stop_loss, target,
               hold_days, regime, risk_level, signals, verdict, confidence, ref_close,
               benchmark_ref_close, target_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            verdict if verdict is not None else pick.get("verdict", ""),
            float(pick.get("confidence", 0) or 0),
            ref_close,
            benchmark_ref_close,
            target_date,
        ))
        return cur.lastrowid


def resolve_pick(pick_id: int, close_next_day: float):
    """Backward-compat shim — call resolve_pick_full when possible.
    Falls back to old next-day-only logic if only close_next_day is known.
    """
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


def resolve_pick_full(
    pick_id: int,
    exit_close: float,
    exit_date: str,
    exit_reason: str,
    max_close: float,
    min_close: float,
    max_high: float,
    min_low: float,
    hit_target: int,
    hit_stop: int,
    hold_days_actual: int,
    pending: bool = False,
    benchmark_exit_close: float = None,
    entry_fill_date: str = None,
    entry_fill_price: float = None,
):
    """Resolve a pick with full hold-period stats.
    pending=True writes interim stats (max/min so far) but keeps resolved=0
    so the next cron can update again. Used while the hold window is still open.

    benchmark_exit_close: 0050 收盤在 exit_date — 用於算 vs 大盤的 alpha。
    entry_fill_price: 進場成交價（v5）——有值時損益以它為基準而非 ref_close。
                      alpha 的 0050 基準仍取 pick 日（相差 1-3 日，可接受誤差）。
    exit_reason='not_filled'：進場窗內未觸價 → exit_close=None →
                      return_pct/success 皆 NULL，不進勝率統計。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ref_close, benchmark_ref_close FROM picks WHERE id=?", (pick_id,)
        ).fetchone()
        if not row:
            return
        ref = entry_fill_price or row["ref_close"]
        bench_ref = row["benchmark_ref_close"]

        return_pct = None
        max_gain_pct = None
        max_drawdown_pct = None
        success = None
        if ref and ref > 0:
            if exit_close is not None:
                return_pct = round((exit_close - ref) / ref * 100, 3)
                success = 1 if return_pct > 0 else 0
            # Use high/low (intraday peaks) — "最高/最低瞬間"
            # 這樣 max_gain_pct 一定 >= return_pct（沒有「漲過頭錯過」是 OK 的）
            if max_high is not None:
                max_gain_pct = round((max_high - ref) / ref * 100, 3)
            if min_low is not None:
                max_drawdown_pct = round((min_low - ref) / ref * 100, 3)

        # Benchmark vs pick (alpha)
        benchmark_return_pct = None
        alpha_pct = None
        if bench_ref and bench_ref > 0 and benchmark_exit_close is not None:
            benchmark_return_pct = round((benchmark_exit_close - bench_ref) / bench_ref * 100, 3)
            if return_pct is not None:
                alpha_pct = round(return_pct - benchmark_return_pct, 3)

        conn.execute("""
            UPDATE picks SET
              close_next_day=COALESCE(close_next_day, ?),  -- preserve old value if set
              exit_close=?, exit_date=?, exit_reason=?,
              max_close=?, min_close=?, max_high=?, min_low=?,
              max_gain_pct=?, max_drawdown_pct=?,
              hit_target=?, hit_stop=?, hold_days_actual=?,
              return_pct=?, success=?,
              benchmark_exit_close=COALESCE(?, benchmark_exit_close),
              benchmark_return_pct=COALESCE(?, benchmark_return_pct),
              alpha_pct=COALESCE(?, alpha_pct),
              entry_fill_date=COALESCE(?, entry_fill_date),
              entry_fill_price=COALESCE(?, entry_fill_price),
              resolved=?, resolved_at=?
            WHERE id=?
        """, (
            exit_close,  # for close_next_day COALESCE
            exit_close, exit_date, exit_reason,
            max_close, min_close, max_high, min_low,
            max_gain_pct, max_drawdown_pct,
            hit_target, hit_stop, hold_days_actual,
            return_pct, success,
            benchmark_exit_close, benchmark_return_pct, alpha_pct,
            entry_fill_date, entry_fill_price,
            0 if pending else 1, datetime.now(TZ).isoformat(),
            pick_id,
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


def get_open_positions() -> list[dict]:
    """波段在倉部位（resolved=0 的 picks + 即時追蹤數據）。
    outcome_tracker.resolve_pending() 每天會更新 pending picks 的
    return_pct / max_gain_pct / max_drawdown_pct / hold_days_actual，
    所以這裡讀出來就是最新狀態。給 run_agents 注入 prompt + 前端顯示。
    """
    init_db()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, date, target_date, stock_code, stock_name, entry_zone, stop_loss,
                   target, hold_days, ref_close, return_pct, max_gain_pct,
                   max_drawdown_pct, hold_days_actual, verdict, confidence,
                   exit_reason, entry_fill_date, entry_fill_price
            FROM picks
            WHERE resolved=0 AND stock_code != 'NONE'
            ORDER BY date ASC
        """).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["current_return_pct"] = d.pop("return_pct", None)
            # v5：進場窗內還沒觸價的部位標 waiting_fill，前端顯示「待觸價」
            d["waiting_fill"] = (d.get("exit_reason") == "pending_fill")
            out.append(d)
        return out


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
    Compute win rates by regime and by signal. Includes:
      - resolved aggregate stats (overall_rate, wins, losses)
      - open positions (resolved=0)
      - recent_picks (last 30, both open and resolved, for dashboard display)
      - best / worst trade
      - avg win/loss return
      - hit-target vs hit-stop breakdown
    """
    init_db()

    # All recent picks (resolved + open) — for dashboard recent_picks list
    all_recent = _query_recent_picks(days, include_open=True)
    # Just resolved — for win-rate stats
    resolved = [p for p in all_recent if p.get("resolved") == 1]
    open_picks = [p for p in all_recent if p.get("resolved") != 1]

    if not resolved and not open_picks:
        return {"available": False, "total": 0, "open_count": 0, "recent_picks": []}

    total = len(resolved)
    wins = sum(1 for p in resolved if p["success"] == 1)
    losses = total - wins
    overall_rate = round(wins / total * 100, 1) if total else 0

    # Win rate by regime (with pre-computed rate)
    regime_stats: dict[str, dict] = {}
    for p in resolved:
        r = p.get("regime") or "未知"
        if r not in regime_stats:
            regime_stats[r] = {"wins": 0, "total": 0}
        regime_stats[r]["total"] += 1
        if p["success"] == 1:
            regime_stats[r]["wins"] += 1
    for r in regime_stats.values():
        r["rate"] = round(r["wins"] / r["total"] * 100, 1) if r["total"] else 0

    # Win rate by signal
    signal_stats: dict[str, dict] = {}
    for p in resolved:
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
    for s in signal_stats.values():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0

    # Recent streak (last 7 resolved)
    recent7 = resolved[:7]
    streak = "".join("✓" if p["success"] == 1 else "✗" for p in recent7)

    # Return distribution stats
    returns = [p["return_pct"] for p in resolved if p.get("return_pct") is not None]
    win_returns = [p["return_pct"] for p in resolved
                   if p.get("success") == 1 and p.get("return_pct") is not None]
    loss_returns = [p["return_pct"] for p in resolved
                    if p.get("success") != 1 and p.get("return_pct") is not None]

    best_pick = max(resolved, key=lambda p: p.get("return_pct") or -999, default=None) if returns else None
    worst_pick = min(resolved, key=lambda p: p.get("return_pct") or 999, default=None) if returns else None

    avg_win = round(sum(win_returns) / len(win_returns), 2) if win_returns else None
    avg_loss = round(sum(loss_returns) / len(loss_returns), 2) if loss_returns else None
    avg_return = round(sum(returns) / len(returns), 2) if returns else None

    # Hit-target vs hit-stop
    hit_target = sum(1 for p in resolved if p.get("hit_target") == 1)
    hit_stop = sum(1 for p in resolved if p.get("hit_stop") == 1)

    # Lightweight recent_picks for dashboard (strip heavy fields)
    def _trim(p):
        return {
            "id": p.get("id"),
            "date": p.get("date"),
            "code": p.get("stock_code"),
            "name": p.get("stock_name"),
            "verdict": p.get("verdict"),
            "confidence": p.get("confidence"),
            "regime": p.get("regime"),
            "entry_zone": p.get("entry_zone"),
            "stop_loss": p.get("stop_loss"),
            "target": p.get("target"),
            "hold_days": p.get("hold_days"),
            "ref_close": p.get("ref_close"),
            "close_next_day": p.get("close_next_day"),
            "return_pct": p.get("return_pct"),
            "hit_target": p.get("hit_target"),
            "hit_stop": p.get("hit_stop"),
            "success": p.get("success"),
            "resolved": p.get("resolved"),
            "resolved_at": p.get("resolved_at"),
            # v2 hold-period fields
            "exit_close":       p.get("exit_close"),
            "exit_date":        p.get("exit_date"),
            "exit_reason":      p.get("exit_reason"),
            "max_gain_pct":     p.get("max_gain_pct"),
            "max_drawdown_pct": p.get("max_drawdown_pct"),
            "hold_days_actual": p.get("hold_days_actual"),
        }

    watch = get_watch_stats(days)
    watch_streak = get_watch_streak()

    return {
        "available": True,
        "total": total,
        "wins": wins,
        "losses": losses,
        "overall_rate": overall_rate,
        "regime_stats": regime_stats,
        "signal_stats": signal_stats,
        "recent_streak": streak,
        "open_count": len(open_picks),
        "open_picks": [_trim(p) for p in open_picks],
        "recent_picks": [_trim(p) for p in all_recent],
        "avg_return": avg_return,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "best": _trim(best_pick) if best_pick else None,
        "worst": _trim(worst_pick) if worst_pick else None,
        "hit_target_count": hit_target,
        "hit_stop_count": hit_stop,
        # v2: discipline stats
        "watch_days":     watch["watch_days"],
        "analysis_days":  watch["analysis_days"],
        "pick_days":      watch["pick_days"],
        "watch_rate":     watch["watch_rate"],
        # Consecutive days without a pick (NEW 2026-06-12)
        "watch_streak":   watch_streak,
    }


def log_watch_day(date: str, scanner_top: dict, regime_name: str, reason: str):
    """記錄一個觀望日，連同 scanner top1 候選，供日後判斷
    「系統觀望時錯失了多少」。每天最多 1 筆（IGNORE 重複）。
    scanner_top: 候選 dict from candidate_stocks.json (code/name/score/...)
    """
    init_db()
    code = scanner_top.get("code") if scanner_top else None
    if not code:
        return
    with get_conn() as conn:
        # Ensure schema
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watch_log (
                date              TEXT PRIMARY KEY,
                scanner_top_code  TEXT,
                scanner_top_name  TEXT,
                scanner_top_score INTEGER,
                ref_close         REAL,
                regime            TEXT,
                watch_reason      TEXT,
                max_gain_pct      REAL,    -- highest %gain in next 5 trading days
                max_dd_pct        REAL,    -- worst %drawdown in next 5 trading days
                outcome           TEXT,    -- 'missed' (>+3%) / 'avoided' (<-3%) / 'flat'
                resolved          INTEGER DEFAULT 0,
                resolved_at       TEXT,
                created_at        TEXT
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO watch_log
              (date, scanner_top_code, scanner_top_name, scanner_top_score,
               ref_close, regime, watch_reason, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            date, code, scanner_top.get("name"), scanner_top.get("score"),
            scanner_top.get("close"), regime_name, reason[:300] if reason else "",
            datetime.now(TZ).isoformat(),
        ))


def get_unresolved_watch_log() -> list[dict]:
    """取得尚未結算的觀望日紀錄。供 outcome_tracker 回填 5 日績效。"""
    init_db()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watch_log'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT * FROM watch_log WHERE resolved=0 ORDER BY date ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def resolve_watch_log(date: str, max_gain_pct: float, max_dd_pct: float):
    """填入觀望日 scanner top1 的 5 日 max/min 變化 + 分類 outcome。"""
    if max_gain_pct >= 3.0:
        outcome = "missed"      # 錯失：5 日內最高漲幅 ≥ +3%
    elif max_dd_pct <= -3.0:
        outcome = "avoided"     # 避開：5 日內最深跌幅 ≤ -3%
    else:
        outcome = "flat"        # 平淡：兩邊都沒突破 3%
    init_db()
    with get_conn() as conn:
        conn.execute("""
            UPDATE watch_log SET
              max_gain_pct=?, max_dd_pct=?, outcome=?,
              resolved=1, resolved_at=?
            WHERE date=?
        """, (
            max_gain_pct, max_dd_pct, outcome,
            datetime.now(TZ).isoformat(), date,
        ))


# Tunable thresholds for bias classification in get_watch_outcomes_summary.
# After ~30 resolved watch_log samples, revisit these — the 1.5× ratio is
# a starting guess. Lower if you want the system to flag overcautious
# behaviour sooner.
MISSED_AVOIDED_RATIO_BIAS = 1.5  # missed > avoided × this → overcautious
MIN_SAMPLES_FOR_BIAS      = 5    # don't classify with too few samples


def get_watch_outcomes_summary(days: int = 60) -> dict:
    """彙整近 N 天觀望日的結果：錯失 vs 避開 vs 平淡。
    幫助判斷系統的選股保守度是否合理。
    """
    init_db()
    # Use Asia/Taipei TZ for cutoff (GH Actions runs in UTC; without TZ
    # this would slip 1 day during early-UTC hours and cut off recent data)
    cutoff = (datetime.now(TZ).date() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watch_log'"
        ).fetchone()
        if not exists:
            return {"available": False}
        rows = conn.execute(
            "SELECT * FROM watch_log WHERE date >= ? AND resolved=1",
            (cutoff,),
        ).fetchall()
    if not rows:
        return {"available": False, "total": 0}
    rows = [dict(r) for r in rows]
    missed   = [r for r in rows if r["outcome"] == "missed"]
    avoided  = [r for r in rows if r["outcome"] == "avoided"]
    flat     = [r for r in rows if r["outcome"] == "flat"]
    total = len(rows)
    avg_missed_gain = (sum(r["max_gain_pct"] or 0 for r in missed) / len(missed)) if missed else 0
    avg_avoided_dd  = (sum(r["max_dd_pct"] or 0 for r in avoided) / len(avoided)) if avoided else 0
    # Diagnostic: if missed substantially outweighs avoided, system is overcautious
    bias = None
    if total >= MIN_SAMPLES_FOR_BIAS:
        if len(missed) > len(avoided) * MISSED_AVOIDED_RATIO_BIAS:
            bias = "overcautious"      # 系統過度保守，錯失多於避開
        elif len(avoided) > len(missed) * MISSED_AVOIDED_RATIO_BIAS:
            bias = "well-calibrated"   # 觀望時多半避開了跌
    return {
        "available": True,
        "period_days": days,
        "total": total,
        "missed":      len(missed),
        "avoided":     len(avoided),
        "flat":        len(flat),
        "missed_rate": round(len(missed) / total * 100, 1),
        "avg_missed_gain": round(avg_missed_gain, 2),
        "avg_avoided_dd":  round(avg_avoided_dd, 2),
        "bias":        bias,
    }


def get_watch_streak() -> int:
    """連續空手天數：從最新的盤後 analysis day 倒推，數連續沒 pick 的天數。
    回 0 表示最新的盤後 cron 有出 pick。
    回 N 表示已連續 N 天沒推薦（可能系統過度保守）。
    """
    init_db()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='system_decisions'"
        ).fetchone()
        if not exists:
            return 0
        # All analysis dates with at least one pick
        pick_dates = {
            r["date"] for r in conn.execute(
                "SELECT DISTINCT date FROM picks WHERE stock_code != 'NONE'"
            )
        }
        # All analysis dates (post-market gets the pick decision; pre-market doesn't)
        all_dates = [
            r["date"] for r in conn.execute(
                "SELECT DISTINCT date FROM system_decisions ORDER BY date DESC"
            )
        ]
    streak = 0
    for d in all_dates:
        if d in pick_dates:
            break
        streak += 1
    return streak


def get_watch_stats(days: int = 30) -> dict:
    """近 N 天空手率。
    Logic:
      - analysis_days = system_decisions 表中近 N 天的不同日期數（系統有分析過的日子）
      - pick_days = picks 表中近 N 天非 NONE 的紀錄數（系統有出手的日子）
      - watch_days = analysis_days - pick_days（空手觀望的日子）
    """
    init_db()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='system_decisions'"
        ).fetchone()
        if not exists:
            return {"available": False, "analysis_days": 0, "pick_days": 0, "watch_days": 0, "watch_rate": 0}
        analysis_days = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM system_decisions WHERE date >= ?",
            (cutoff,),
        ).fetchone()[0]
        pick_days = conn.execute(
            "SELECT COUNT(*) FROM picks WHERE stock_code != 'NONE' AND date >= ?",
            (cutoff,),
        ).fetchone()[0]

    watch_days = max(0, analysis_days - pick_days)
    return {
        "available": True,
        "period_days": days,
        "analysis_days": analysis_days,
        "pick_days": pick_days,
        "watch_days": watch_days,
        "watch_rate": round(watch_days / max(analysis_days, 1) * 100, 1),
    }


def _query_recent_picks(days: int = 30, include_open: bool = False) -> list[dict]:
    """Internal: get recent picks, optionally including unresolved ones."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff_date = (date.today() - timedelta(days=days)).isoformat()
    if include_open:
        sql = "SELECT * FROM picks WHERE date >= ? ORDER BY date DESC, id DESC"
    else:
        sql = "SELECT * FROM picks WHERE resolved = 1 AND date >= ? ORDER BY date DESC, id DESC"
    rows = conn.execute(sql, (cutoff_date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


# ── Swing scorecard（寶藏雷達連動 #4）─────────────────────────────────────────

SWING_ERA_START = "2026-07-17"   # 波段改版上線日；之前的 picks 屬短線時代


def build_swing_scorecard() -> dict:
    """聚合所有已結案 picks 成季度記分卡，供寶藏股研究室的
    「季度持倉審判日」任務抓取（GitHub Pages 公開 JSON）。

    分兩個時代統計（short_term vs swing），審判日最想知道的就是
    「改版後有沒有比較好」。
    """
    init_db()

    def _agg(rows: list) -> dict:
        if not rows:
            return {"total": 0}
        # not_filled = 進場窗未觸價 → 排除在勝率/報酬統計外（v5），
        # 但單獨列 count（「開單但買不到」也是策略品質的訊號）
        not_filled = [r for r in rows if r["exit_reason"] == "not_filled"]
        traded = [r for r in rows if r["exit_reason"] != "not_filled"]
        rets   = [r["return_pct"] for r in traded if r["return_pct"] is not None]
        alphas = [r["alpha_pct"] for r in traded if r["alpha_pct"] is not None]
        holds  = [r["hold_days_actual"] for r in traded if r["hold_days_actual"]]
        wins   = [r for r in traded if r["success"] == 1]
        reasons = {}
        for r in rows:
            k = r["exit_reason"] or "unknown"
            reasons[k] = reasons.get(k, 0) + 1
        return {
            "total":          len(rows),
            "traded":         len(traded),
            "not_filled":     len(not_filled),
            "wins":           len(wins),
            "win_rate_pct":   round(len(wins) / len(traded) * 100, 1) if traded else None,
            "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
            "avg_alpha_pct":  round(sum(alphas) / len(alphas), 2) if alphas else None,
            "avg_hold_days":  round(sum(holds) / len(holds), 1) if holds else None,
            "exit_reasons":   reasons,
        }

    def _quarter(date_str: str) -> str:
        y, m = date_str[:4], int(date_str[5:7])
        return f"{y}Q{(m - 1) // 3 + 1}"

    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT date, stock_code, stock_name, return_pct, alpha_pct,
                   success, exit_reason, hold_days_actual, verdict
            FROM picks
            WHERE resolved=1 AND stock_code != 'NONE'
            ORDER BY date ASC
        """).fetchall()]

    short_era = [r for r in rows if r["date"] < SWING_ERA_START]
    swing_era = [r for r in rows if r["date"] >= SWING_ERA_START]

    by_quarter = {}
    for r in rows:
        by_quarter.setdefault(_quarter(r["date"]), []).append(r)

    return {
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "_doc": ("Swing pick 系統記分卡（機器可讀）。讀取者：寶藏股研究室季度審判日"
                 "（quarterly-holdings-judgment-day 排程）。short_term=改版前(1-3日動能)、"
                 "swing=2026-07-17 起(10-22 交易日波段)。alpha=vs 0050 同期。"),
        "swing_era_start": SWING_ERA_START,
        "by_era": {
            "short_term": _agg(short_era),
            "swing":      _agg(swing_era),
        },
        "by_quarter": {q: _agg(rs) for q, rs in sorted(by_quarter.items())},
        "open_positions": [
            {"code": p["stock_code"], "name": p["stock_name"], "entry_date": p["date"],
             "current_return_pct": p.get("current_return_pct"),
             "days_held": p.get("hold_days_actual")}
            for p in get_open_positions()
        ],
        "recent_resolved": [
            {"date": r["date"], "code": r["stock_code"], "name": r["stock_name"],
             "return_pct": r["return_pct"], "alpha_pct": r["alpha_pct"],
             "exit": r["exit_reason"], "days": r["hold_days_actual"]}
            for r in rows[-10:]
        ],
    }
