"""
台股量化掃描器
- 從 TWSE Open API 取得全體上市股今日行情
- 篩選成交金額前 200 大（排除 ETF、權證）
- 批次下載 60 天 yfinance 歷史
- 計算技術信號：MA20 突破、量能爆發、RSI、均線多頭排列、5日高
- 輸出 data/candidate_stocks.json（前 15 名）
"""

import json
import os
import time
from datetime import datetime, timedelta

import requests
import yfinance as yf
import pandas as pd
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

TOP_N = 200      # filter top N stocks by trade value
MIN_SCORE = 3    # minimum signals to qualify
CANDIDATES = 15  # max output candidates

# TWSE endpoints (try in order)
TWSE_OPENAPI = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; InvestmentAI/1.0)"}


# ── Data fetching ─────────────────────────────────────────────────────────────

def _parse_number(s: str) -> float:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def fetch_twse_openapi() -> list[dict]:
    """Try the official TWSE Open API (no date param, returns latest available)."""
    try:
        r = requests.get(TWSE_OPENAPI, timeout=20, headers=HEADERS)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or not data:
            return []
        results = []
        for row in data:
            code = str(row.get("Code", "")).strip()
            name = str(row.get("Name", "")).strip()
            tv = _parse_number(row.get("TradeValue", "0"))
            if not (len(code) == 4 and code.isdigit()):
                continue
            results.append({"code": code, "name": name, "trade_value": int(tv)})
        return results
    except Exception as e:
        print(f"[scanner] openapi.twse.com.tw failed: {e}")
        return []


def fetch_twse_rwd(date_str: str) -> list[dict]:
    """Fallback: TWSE RWD endpoint with explicit date."""
    try:
        r = requests.get(
            TWSE_RWD,
            params={"response": "json", "date": date_str},
            timeout=20,
            headers=HEADERS,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("stat") != "OK":
            return []
        fields = data.get("fields", [])
        results = []
        for row in data.get("data", []):
            d = dict(zip(fields, row))
            code = str(d.get("證券代號", "")).strip()
            name = str(d.get("證券名稱", "")).strip()
            tv = _parse_number(d.get("成交金額", "0"))
            if not (len(code) == 4 and code.isdigit()):
                continue
            results.append({"code": code, "name": name, "trade_value": int(tv)})
        return results
    except Exception as e:
        print(f"[scanner] twse.com.tw/rwd failed for {date_str}: {e}")
        return []


def fetch_top_stocks() -> list[dict]:
    """Fetch top N stocks by today's trade value."""
    # 1. Try Open API (most reliable, no date needed)
    stocks = fetch_twse_openapi()

    # 2. Walk back up to 5 trading days using RWD endpoint
    if not stocks:
        today = datetime.now(TZ)
        for days_back in range(6):
            d = today - timedelta(days=days_back)
            if d.weekday() >= 5:
                continue
            stocks = fetch_twse_rwd(d.strftime("%Y%m%d"))
            if stocks:
                print(f"[scanner] Got data for {d.strftime('%Y-%m-%d')}")
                break

    if not stocks:
        print("[scanner] ERROR: could not fetch TWSE data")
        return []

    stocks.sort(key=lambda x: x["trade_value"], reverse=True)
    return stocks[:TOP_N]


# ── Technical analysis ────────────────────────────────────────────────────────

def calc_signals(df: pd.DataFrame, code: str) -> dict | None:
    """Calculate technical signals. Returns None if insufficient data."""
    if df is None or df.empty:
        return None

    # Flatten MultiIndex if present (single-ticker batch download)
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(0, axis=1) if df.columns.nlevels > 1 else df

    # Require OHLCV columns
    required = {"Close", "Volume"}
    if not required.issubset(set(df.columns)):
        return None

    close = df["Close"].dropna()
    volume = df["Volume"].dropna()

    if len(close) < 22:
        return None

    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    avg_vol20 = volume.rolling(20).mean()

    last_close = float(close.iloc[-1])
    last_vol = float(volume.iloc[-1])
    ma20_val = float(ma20.iloc[-1])
    ma20_prev = float(ma20.iloc[-2]) if len(ma20) >= 2 else ma20_val
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else last_close
    avg_vol = float(avg_vol20.iloc[-1]) if not pd.isna(avg_vol20.iloc[-1]) else 1

    # ── Signals ───────────────────────────────────────────────────────────────
    breakout_ma20 = (last_close > ma20_val) and (prev_close <= ma20_prev)
    above_ma20 = last_close > ma20_val

    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0.0
    vol_surge = vol_ratio >= 2.0

    # RSI-14
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] and loss.iloc[-1] != 0 else float("nan")
    rsi = 100 - (100 / (1 + rs)) if not pd.isna(rs) else 50.0
    rsi_zone = 60.0 <= rsi <= 80.0

    # MA alignment: MA5 > MA10 > MA20
    ma_aligned = (
        float(ma5.iloc[-1]) > float(ma10.iloc[-1]) > float(ma20.iloc[-1])
        if not (pd.isna(ma5.iloc[-1]) or pd.isna(ma10.iloc[-1]))
        else False
    )

    # 5-day high: today is highest close in last 5 sessions
    five_day_high = last_close >= float(close.tail(5).max())

    score = sum([breakout_ma20, above_ma20, vol_surge, rsi_zone, ma_aligned, five_day_high])

    return {
        "breakout_ma20": bool(breakout_ma20),
        "above_ma20": bool(above_ma20),
        "vol_surge": bool(vol_surge),
        "vol_ratio": round(vol_ratio, 2),
        "rsi": round(rsi, 1),
        "rsi_zone": bool(rsi_zone),
        "ma_aligned": bool(ma_aligned),
        "five_day_high": bool(five_day_high),
        "score": score,
        "last_close": round(last_close, 2),
        "ma20": round(ma20_val, 2),
    }


# ── Main scanner ──────────────────────────────────────────────────────────────

def run_scanner() -> list[dict]:
    print("[scanner] Fetching TWSE all-stock data...")
    top_stocks = fetch_top_stocks()

    if not top_stocks:
        return []

    print(f"[scanner] Analyzing top {len(top_stocks)} stocks by trade value...")

    tickers = [f"{s['code']}.TW" for s in top_stocks]

    print(f"[scanner] Downloading 60-day history for {len(tickers)} tickers...")
    try:
        raw = yf.download(
            tickers,
            period="60d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"[scanner] yfinance batch download failed: {e}")
        return []

    candidates = []
    for s in top_stocks:
        code = s["code"]
        ticker = f"{code}.TW"
        try:
            if len(tickers) == 1:
                df = raw
            elif ticker in raw.columns.get_level_values(0):
                df = raw[ticker]
            else:
                continue

            signals = calc_signals(df, code)
            if signals is None or signals["score"] < MIN_SCORE:
                continue

            candidates.append({
                "code": code,
                "name": s["name"],
                "trade_value_m": round(s["trade_value"] / 1_000_000, 1),
                **signals,
            })
        except Exception:
            continue

    # Sort: score desc, then vol_ratio desc as tiebreaker
    candidates.sort(key=lambda x: (x["score"], x["vol_ratio"]), reverse=True)
    result = candidates[:CANDIDATES]

    print(f"[scanner] Found {len(candidates)} qualifying stocks, returning top {len(result)}")
    return result


def save_candidates(candidates: list[dict]):
    os.makedirs(DATA_DIR, exist_ok=True)
    output = {
        "generated_at": datetime.now(TZ).isoformat(),
        "count": len(candidates),
        "candidates": candidates,
    }
    path = os.path.join(DATA_DIR, "candidate_stocks.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[scanner] Saved → {path}")


if __name__ == "__main__":
    results = run_scanner()
    save_candidates(results)
    if results:
        print("\n【今日精選候選股（Top 5）】")
        for i, c in enumerate(results[:5], 1):
            signals = []
            if c.get("breakout_ma20"): signals.append("MA20突破")
            if c.get("vol_surge"):     signals.append(f"量爆{c['vol_ratio']}x")
            if c.get("rsi_zone"):      signals.append(f"RSI{c['rsi']}")
            if c.get("ma_aligned"):    signals.append("均線多排")
            if c.get("five_day_high"): signals.append("5日高")
            print(f"  {i}. {c['name']}({c['code']}) "
                  f"score={c['score']} | {' / '.join(signals)}")
    else:
        print("[scanner] 今日無符合條件的候選股")
