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
    SIGNAL_KEYS = ("breakout_ma20", "above_ma20", "vol_surge", "rsi_zone", "ma_aligned", "five_day_high", "rs_signal")
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

        close = _fetch_close(code, next_day, strict=True)
        if close is None:
            print(f"[outcome_tracker] {next_day} 收盤資料尚未公布或暫不可得（{code}），下次再試")
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
