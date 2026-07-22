"""
Outcome Tracker
- 每次盤後精選後：儲存推薦記錄
- 每次執行開始：結算前一天的未完成記錄
"""

import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf

import alpha_db
import tw_stock_lookup

# Sanity check: if AI's entry zone deviates more than this fraction from
# the real reference close, treat the pick as a price hallucination and
# refuse to save. Protects against the 緯穎(6669) case where AI gave
# entry 2730-2780 while real price was ~5345 (49% deviation).
ENTRY_SANITY_MAX_DEVIATION = 0.30

TZ = ZoneInfo("Asia/Taipei")


def _prev_trading_day(date_str: str) -> str:
    """回推最近一個非週末交易日（近似，未排除台灣國定假日）。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _fetch_close(code: str, date_str: str, strict: bool = False) -> float | None:
    """取得特定股票在特定日期的收盤價（使用 yfinance）。
    strict=True：當該日資料不存在 → 回 None（不 fallback 到最新收盤）。
    period 30d 是為了支援 backfill。
    自動嘗試 .TW（上市）和 .TWO（上櫃）兩種 suffix，因為 yfinance 區分兩者。
    """
    for suffix in (".TW", ".TWO"):
        try:
            hist = yf.Ticker(code + suffix).history(period="30d", auto_adjust=True)
            if hist.empty:
                continue
            hist.index = hist.index.tz_localize(None)
            for ts, row in hist.iterrows():
                if ts.strftime("%Y-%m-%d") == date_str:
                    return round(float(row["Close"]), 2)
            if not strict:
                return round(float(hist["Close"].iloc[-1]), 2)
        except Exception:
            continue
    if not strict:
        return None
    return None


def _fetch_benchmark_close(date_str: str) -> float | None:
    """0050 元大台灣50 在特定日期的收盤價（同期基準）。
    yfinance ticker '0050.TW'。假日/週末會 fallback 到 前後 3 天內第一個交易日。
    """
    from datetime import datetime as _dt, timedelta as _td
    try:
        hist = yf.Ticker("0050.TW").history(period="60d", auto_adjust=True)
        if hist.empty:
            return None
        hist.index = hist.index.tz_localize(None)
        target = _dt.strptime(date_str, "%Y-%m-%d")
        # exact match preferred
        for ts, row in hist.iterrows():
            if ts.strftime("%Y-%m-%d") == date_str:
                return round(float(row["Close"]), 2)
        # else: nearest previous trading day within 3 days
        for offset in range(1, 4):
            probe = (target - _td(days=offset)).strftime("%Y-%m-%d")
            for ts, row in hist.iterrows():
                if ts.strftime("%Y-%m-%d") == probe:
                    return round(float(row["Close"]), 2)
    except Exception:
        pass
    return None


def _fetch_ohlc_range(code: str, start_date: str, end_date: str) -> list[dict]:
    """取得 [start_date, end_date] 區間內所有交易日的 OHLC。
    自動嘗試 .TW / .TWO。回 list[{date, open, high, low, close}]。
    period=3mo：波段持有窗 22 個交易日 ≈ 32+ 個日曆天，加上連假 buffer。
    """
    for suffix in (".TW", ".TWO"):
        try:
            hist = yf.Ticker(code + suffix).history(period="3mo", auto_adjust=True)
            if hist.empty:
                continue
            hist.index = hist.index.tz_localize(None)
            rows = []
            for ts, r in hist.iterrows():
                d = ts.strftime("%Y-%m-%d")
                if start_date <= d <= end_date:
                    rows.append({
                        "date":  d,
                        "open":  round(float(r["Open"]),  2),
                        "high":  round(float(r["High"]),  2),
                        "low":   round(float(r["Low"]),   2),
                        "close": round(float(r["Close"]), 2),
                    })
            if rows:
                return rows
        except Exception:
            continue
    return []


def _parse_hold_days_max(text: str) -> int:
    """從 hold_days 字串抽出最大天數。
    "10-22 個交易日" → 22 | "7" → 7 | "持有 3-5 個交易日" → 5
    預設 22（波段版預設持有窗；舊短線 picks 的 hold_days 都有數字，不受影響）。
    """
    nums = re.findall(r"\d+", str(text or ""))
    if not nums:
        return 22
    return max(int(n) for n in nums)


def _n_trading_days_after(start_date: str, n: int) -> str:
    """從 start_date 起算第 n 個交易日（近似，不排國定假日）。"""
    d = datetime.strptime(start_date, "%Y-%m-%d")
    count = 0
    while count < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime("%Y-%m-%d")


def _parse_first_number(text: str):
    """從字串裡抽出第一個浮點數（例如 "286.0-290.0" → 286.0）。"""
    m = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    return float(m.group()) if m else None


def save_today_pick(pick_output: dict, regime: dict, market_data: dict, candidates: list = None):
    """
    在 run_agents.py 盤後執行完 tw_daily_pick 後呼叫。
    pick_output: tw_daily_pick.run() 的完整輸出
    candidates: scanner 候選股清單（用來還原該股觸發的精確信號）
    """
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    pick = pick_output.get("pick", {})
    code = pick.get("code", "NONE")

    # 不記錄空手觀望（code == "—" 或 verdict 含「觀望」）
    verdict = pick_output.get("verdict", "")
    if not code or code in ("—", "NONE") or "觀望" in verdict:
        print(f"[outcome_tracker] 今日空手觀望，不記錄推薦")
        return

    # Validate AI's stock_name against canonical TWSE / TPEx lookup.
    # AI sometimes hallucinates a name for the right code (e.g. 6150 → 撼訊
    # was once mislabeled '勤誠'). If lookup disagrees, override + warn.
    ai_name = pick.get("name", "")
    canonical, corrected = tw_stock_lookup.validate_name(code, ai_name)
    if corrected:
        print(f"[outcome_tracker] ⚠ AI 把 {code} 取名為「{ai_name}」, 實際應為「{canonical}」— 已修正")
        pick["name"] = canonical

    # 從 scanner candidates 找出該股的精確信號
    SIGNAL_KEYS = ("trend_up", "above_ma60", "pullback_buy", "base_breakout", "vol_accumulate", "rsi_swing", "rs_20d_strong")
    scanner_signals = None
    if candidates:
        matched = next((c for c in candidates if c.get("code") == code), None)
        if matched:
            scanner_signals = [k for k in SIGNAL_KEYS if matched.get(k)]

    # 嘗試從 market_data 取今日收盤作為參考基準
    tw_stocks = market_data.get("tw_stocks", {})
    ref_close = None
    if code in tw_stocks:
        ref_close = tw_stocks[code].get("close")
    if not ref_close:
        ref_close = _fetch_close(code, today)

    # SANITY CHECK · entry vs ref_close. If AI hallucinated a price that's
    # >30% off the real close, refuse to save — that pick is unactionable
    # (e.g. 6669 5/21: AI gave entry 2730-2780 while close was 5345).
    if ref_close:
        entry_est = _parse_first_number(pick.get("entry_zone", ""))
        if entry_est:
            dev = abs(entry_est - ref_close) / ref_close
            if dev > ENTRY_SANITY_MAX_DEVIATION:
                print(
                    f"[outcome_tracker] ⚠ REJECT pick {code} ({pick.get('name')}): "
                    f"AI entry {entry_est} 與實際收盤 {ref_close} 偏離 {dev*100:.1f}% "
                    f"(>{ENTRY_SANITY_MAX_DEVIATION*100:.0f}%)，幾乎肯定是 AI 對股價的幻覺，不儲存。"
                )
                return

    # Benchmark: 0050 收盤 for alpha vs 大盤
    bench_ref = _fetch_benchmark_close(today)

    row_id = alpha_db.save_pick(
        date=today,
        pick=pick,
        regime=regime,
        ref_close=float(ref_close) if ref_close else None,
        scanner_signals=scanner_signals,
        benchmark_ref_close=bench_ref,
        # verdict 在 pick_output 外層（歷史 bug：曾從內層讀 → 永遠空白）
        verdict=pick_output.get("verdict", ""),
        # target_date = 可下單的目標交易日 = pick 頁檔名 → 月曆 join 靠這欄
        target_date=_next_trading_day(today),
    )
    sig_str = ", ".join(scanner_signals) if scanner_signals else "無 scanner 信號"
    print(f"[outcome_tracker] 已記錄推薦：{pick.get('name')}({code}) "
          f"信號=[{sig_str}] 參考收盤={ref_close} → DB id={row_id}")


def _sanitize_stop_target(
    ref_close: float, stop_loss: float | None, target: float | None
) -> tuple[float | None, float | None]:
    """如果 AI 寫的 stop/target 跟 ref_close 不合理，回 None（忽略該閾值）。
    Logic:
      - stop_loss 必須在 ref_close 的 60%~99% 之間（停損是低於進場價）
      - target 必須在 ref_close 的 101%~200% 之間（目標是高於進場價）
    超出這個範圍 = AI 對股價有幻覺（如 6150 ref=66 但 target=390）。
    """
    if stop_loss is not None:
        if stop_loss >= ref_close * 1.0 or stop_loss < ref_close * 0.5:
            stop_loss = None
    if target is not None:
        if target <= ref_close * 1.0 or target > ref_close * 2.0:
            target = None
    return stop_loss, target


def _walk_hold_period(
    code: str,
    pick_date: str,
    ref_close: float,
    stop_loss: float | None,
    target: float | None,
    hold_days_max: int,
    today: str,
) -> dict | None:
    """遍歷推薦日之後 [1, hold_days_max] 個交易日的 OHLC，回傳結算用統計。
    若資料不足（連次日 OHLC 都沒有），回 None。
    """
    # AI 偶爾會在 stop/target 寫出跟 ref 完全不同數量級的值（幻覺）。
    # 這裡 sanity check 後，若不合理就退到 time-based 結算（不假裝觸停損 / 目標）。
    stop_loss, target = _sanitize_stop_target(ref_close, stop_loss, target)

    start = _next_trading_day(pick_date)
    end = _n_trading_days_after(pick_date, hold_days_max)
    check_until = min(end, today)
    rows = _fetch_ohlc_range(code, start, check_until)
    if not rows:
        return None

    max_high = max((r["high"] for r in rows), default=ref_close)
    min_low  = min((r["low"]  for r in rows), default=ref_close)
    max_close = max((r["close"] for r in rows), default=ref_close)
    min_close = min((r["close"] for r in rows), default=ref_close)

    exit_close = None
    exit_date  = None
    exit_reason = None
    hit_target = 0
    hit_stop   = 0

    for r in rows:
        # 同日同時觸停損 & 目標 — 保守假設先觸停損（更悲觀）
        if stop_loss is not None and r["low"] <= stop_loss:
            exit_close = stop_loss     # 假設於停損價成交
            exit_date  = r["date"]
            exit_reason = "stop"
            hit_stop = 1
            break
        if target is not None and r["high"] >= target:
            exit_close = target        # 假設於目標價成交
            exit_date  = r["date"]
            exit_reason = "target"
            hit_target = 1
            break

    pending = False
    if exit_reason is None:
        # 沒觸停損也沒到目標
        if rows[-1]["date"] >= end or check_until >= end:
            # 持有窗已過完 — 用最後收盤結算
            exit_close = rows[-1]["close"]
            exit_date  = rows[-1]["date"]
            exit_reason = "time"
        else:
            # 持有窗還沒結束 — 標 pending，下次再看
            exit_close = rows[-1]["close"]
            exit_date  = rows[-1]["date"]
            exit_reason = "pending"
            pending = True

    hold_days_actual = len(rows) if exit_reason in ("time", "pending") else (
        next((i + 1 for i, r in enumerate(rows) if r["date"] == exit_date), len(rows))
    )

    return {
        "exit_close":  exit_close,
        "exit_date":   exit_date,
        "exit_reason": exit_reason,
        "max_close":   max_close,
        "min_close":   min_close,
        "max_high":    max_high,
        "min_low":     min_low,
        "hit_target":  hit_target,
        "hit_stop":    hit_stop,
        "hold_days_actual": hold_days_actual,
        "pending":     pending,
    }


def resolve_pending(today_market_data: dict = None):
    """
    結算所有未完成的推薦。
    新版邏輯：遍歷整個 hold_days 期間，記錄 max/min/觸停損/觸目標。
    持有窗未結束的 pick 標為 pending（resolved=0，但會記錄當前 max/min）。
    """
    pending = alpha_db.get_unresolved_picks()
    if not pending:
        return

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    resolved_count = 0
    pending_count = 0

    for p in pending:
        pick_date = p["date"]
        code      = p["stock_code"]
        ref       = p["ref_close"]
        if not ref:
            continue

        # Skip if pick was made today — too early to resolve
        if pick_date >= today:
            continue

        stop_loss = _parse_first_number(p.get("stop_loss"))
        target    = _parse_first_number(p.get("target"))
        hold_max  = _parse_hold_days_max(p.get("hold_days"))

        result = _walk_hold_period(code, pick_date, ref, stop_loss, target, hold_max, today)
        if result is None:
            print(f"[outcome_tracker] {pick_date} {code} 持有期間 OHLC 暫不可得，下次再試")
            continue

        # Benchmark 0050 close on exit_date (for alpha vs 大盤)
        bench_exit = _fetch_benchmark_close(result["exit_date"]) if result.get("exit_date") else None

        alpha_db.resolve_pick_full(
            pick_id=p["id"],
            exit_close=result["exit_close"],
            exit_date=result["exit_date"],
            exit_reason=result["exit_reason"],
            max_close=result["max_close"],
            min_close=result["min_close"],
            max_high=result["max_high"],
            min_low=result["min_low"],
            hit_target=result["hit_target"],
            hit_stop=result["hit_stop"],
            hold_days_actual=result["hold_days_actual"],
            pending=result["pending"],
            benchmark_exit_close=bench_exit,
        )

        if result["pending"]:
            ret = (result["exit_close"] - ref) / ref * 100
            mg  = (result["max_close"] - ref) / ref * 100
            md  = (result["min_close"] - ref) / ref * 100
            print(f"[outcome_tracker] ◯ pending {pick_date} {code}: "
                  f"當前 {ret:+.2f}% (max {mg:+.2f}% / dd {md:+.2f}%) "
                  f"D+{result['hold_days_actual']}/{hold_max}")
            pending_count += 1
        else:
            ret = (result["exit_close"] - ref) / ref * 100
            mark = "✓" if ret > 0 else "✗"
            print(f"[outcome_tracker] {mark} {pick_date} {code}: "
                  f"{result['exit_reason']} @ {result['exit_close']} ({ret:+.2f}%) "
                  f"D+{result['hold_days_actual']}")
            resolved_count += 1

    if resolved_count or pending_count:
        print(f"[outcome_tracker] 結算 {resolved_count} 筆 / 仍開倉 {pending_count} 筆")

    # Also resolve watch_log entries (scanner top1 of past 觀望 days)
    _resolve_pending_watch_log(today)


WATCH_LOG_RESOLVE_DAYS = 10   # 波段版：觀望日後看 10 個交易日（原短線版 5 日）

def _resolve_pending_watch_log(today: str):
    """For each unresolved watch_log entry that's ≥10 trading days old,
    look at its scanner_top1's price action over those days. Classify
    as missed/avoided/flat. This is what Reflection uses to detect bias.
    波段改版後拉長到 10 日：波段機會的展開需要更長觀察窗，5 日內不動
    不代表 miss。
    """
    pending = alpha_db.get_unresolved_watch_log()
    if not pending:
        return
    resolved_n = 0
    for w in pending:
        wd = w["date"]
        code = w["scanner_top_code"]
        ref = w["ref_close"]
        if not ref:
            continue
        end = _n_trading_days_after(wd, WATCH_LOG_RESOLVE_DAYS)
        if end > today:
            continue   # observation window not finished yet
        start = _next_trading_day(wd)
        rows = _fetch_ohlc_range(code, start, end)
        if not rows:
            continue
        max_high = max(r["high"] for r in rows)
        min_low  = min(r["low"]  for r in rows)
        max_gain = (max_high - ref) / ref * 100
        max_dd   = (min_low  - ref) / ref * 100
        alpha_db.resolve_watch_log(wd, round(max_gain, 2), round(max_dd, 2))
        resolved_n += 1
    if resolved_n:
        print(f"[outcome_tracker] 觀望日 {WATCH_LOG_RESOLVE_DAYS} 日結算 {resolved_n} 筆")


_HOLIDAYS_CACHE = None

def _tw_holidays() -> tuple[set, set]:
    """讀 data/tw_holidays.json → (封關日 set, 補班交易日 set)。cached。"""
    global _HOLIDAYS_CACHE
    if _HOLIDAYS_CACHE is not None:
        return _HOLIDAYS_CACHE
    import json as _json
    hols, special = set(), set()
    path = os.path.join(os.path.dirname(__file__), "data", "tw_holidays.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = _json.load(f)
        for y in (d.get("holidays") or {}).values():
            hols.update(y)
        for y in (d.get("special_trading_days") or {}).values():
            if isinstance(y, list):
                special.update(y)
    except Exception:
        pass
    _HOLIDAYS_CACHE = (hols, special)
    return _HOLIDAYS_CACHE


def _next_trading_day(date_str: str) -> str:
    """取得下一個交易日（跳過週末 + 台股封關日；含補班交易日）。"""
    hols, special = _tw_holidays()
    d = datetime.strptime(date_str, "%Y-%m-%d")
    while True:
        d += timedelta(days=1)
        ds = d.strftime("%Y-%m-%d")
        if ds in special:
            return ds
        if d.weekday() >= 5 or ds in hols:
            continue
        return ds


def print_summary():
    """印出近期績效摘要（供手動查看）。"""
    stats = alpha_db.get_performance_stats(30)
    prompt_str = alpha_db.format_stats_for_prompt(stats)
    if prompt_str:
        print("\n" + prompt_str)
    else:
        print("[outcome_tracker] 尚無足夠歷史資料（需要至少 1 筆已結算推薦）")


if __name__ == "__main__":
    resolve_pending()
    print_summary()
