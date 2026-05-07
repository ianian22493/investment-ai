"""
Outcome Tracker
- 每次盤後精選後：儲存推薦記錄
- 每次執行開始：結算前一天的未完成記錄
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf

import alpha_db

TZ = ZoneInfo("Asia/Taipei")


def _prev_trading_day(date_str: str) -> str:
    """回推最近一個非週末交易日（近似，未排除台灣國定假日）。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _fetch_close(code: str, date_str: str) -> float | None:
    """取得特定股票在特定日期的收盤價（使用 yfinance）。"""
    ticker = f"{code}.TW"
    try:
        hist = yf.Ticker(ticker).history(period="10d", auto_adjust=True)
        if hist.empty:
            return None
        # Convert index to date strings
        hist.index = hist.index.tz_localize(None)
        for ts, row in hist.iterrows():
            if ts.strftime("%Y-%m-%d") == date_str:
                return round(float(row["Close"]), 2)
        # Fallback: return latest available close
        return round(float(hist["Close"].iloc[-1]), 2)
    except Exception as e:
        print(f"[outcome_tracker] yfinance error for {code}: {e}")
        return None


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

    # 從 scanner candidates 找出該股的精確信號
    SIGNAL_KEYS = ("breakout_ma20", "above_ma20", "vol_surge", "rsi_zone", "ma_aligned", "five_day_high")
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

    row_id = alpha_db.save_pick(
        date=today,
        pick=pick,
        regime=regime,
        ref_close=float(ref_close) if ref_close else None,
        scanner_signals=scanner_signals,
    )
    sig_str = ", ".join(scanner_signals) if scanner_signals else "無 scanner 信號"
    print(f"[outcome_tracker] 已記錄推薦：{pick.get('name')}({code}) "
          f"信號=[{sig_str}] 參考收盤={ref_close} → DB id={row_id}")


def resolve_pending(today_market_data: dict = None):
    """
    結算所有未完成的推薦：用次日實際收盤填入結果。
    在每次 run_agents.py 開始時呼叫。
    """
    pending = alpha_db.get_unresolved_picks()
    if not pending:
        return

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    resolved_count = 0

    for p in pending:
        pick_date = p["date"]
        code = p["stock_code"]

        # 找「推薦日的隔一個交易日」
        next_day = _next_trading_day(pick_date)

        # 如果隔天還沒到，跳過
        if next_day > today:
            continue

        close = _fetch_close(code, next_day)
        if close is None:
            print(f"[outcome_tracker] 無法取得 {code} 在 {next_day} 的收盤，跳過")
            continue

        alpha_db.resolve_pick(p["id"], close)
        ret = round((close - p["ref_close"]) / p["ref_close"] * 100, 2) if p["ref_close"] else None
        result_str = f"+{ret}% ✓" if (ret and ret > 0) else f"{ret}% ✗"
        print(f"[outcome_tracker] 結算 {pick_date} 推薦 {code}: "
              f"參考={p['ref_close']} → 次日={close} ({result_str})")
        resolved_count += 1

    if resolved_count:
        print(f"[outcome_tracker] 共結算 {resolved_count} 筆")


def _next_trading_day(date_str: str) -> str:
    """取得下一個交易日（近似，未排除台灣國定假日）。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


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
