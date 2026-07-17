"""
台股量化掃描器 — SWING 版（2 週-1 個月波段）
- 從 TWSE Open API 取得全體上市股今日行情
- 篩選成交金額前 200 大（排除 ETF、權證）
- 批次下載 6 個月 yfinance 歷史（波段需要 MA60 + 60 日區間結構）
- 計算波段信號：中期趨勢、拉回支撐、盤整突破、量能沉澱、20 日相對強度
- 輸出 data/candidate_stocks.json（前 15 名）

設計哲學（2026-07-17 改版）：
短線版找「明天會噴的股票」（突破 + 量爆 + 5 日高 = 追高）；
波段版找「未來一個月會漲但現在還沒噴的股票」（趨勢確立 + 位階健康 + 買點浮現）。
最大差異：短線獎勵 extended 動能，波段懲罰 extended 動能。
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
MIN_SCORE = 4    # minimum signals to qualify (swing 8-dim scale)
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

def calc_signals(df: pd.DataFrame, code: str, taiex_close: pd.Series = None) -> dict | None:
    """波段版技術信號（8 維，score 0-8）。Returns None if insufficient data.
    taiex_close: TAIEX (^TWII) close series for relative strength calculation.

    信號設計（找「趨勢確立 + 位階健康 + 買點浮現」）：
      trend_up        中期趨勢：MA20 > MA60 且 MA60 上揚（過去 10 日）
      above_ma60      長結構完好：收盤 > MA60
      pullback_buy    拉回買點：價格離 MA20 在 -3%~+5% 內（貼著支撐，不是噴出）
      not_extended    位階健康：價格未超過 MA20 +12%（懲罰追高）
      base_breakout   盤整突破：創 60 日新高，且前 20 日振幅 <15%（有底型）
      vol_accumulate  量能沉澱：近 5 日均量 > 20 日均量 1.2x（資金默默進駐）
      rsi_swing       RSI 45-70（多方結構但未過熱；短線版是 60-80）
      rs_20d_strong   20 日相對強度：贏過大盤 ≥ 3%
    """
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(0, axis=1) if df.columns.nlevels > 1 else df

    required = {"Close", "Volume", "High", "Low"}
    if not required.issubset(set(df.columns)):
        return None

    close = df["Close"].dropna()
    volume = df["Volume"].dropna()
    high = df["High"].dropna()
    low = df["Low"].dropna()

    # 波段需要 MA60 + 60 日區間 → 至少 65 根 K
    if len(close) < 65:
        return None

    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    avg_vol5 = volume.rolling(5).mean()
    avg_vol20 = volume.rolling(20).mean()

    last_close = float(close.iloc[-1])
    ma20_val = float(ma20.iloc[-1])
    ma60_val = float(ma60.iloc[-1])
    ma60_10ago = float(ma60.iloc[-11]) if len(ma60) >= 11 and not pd.isna(ma60.iloc[-11]) else ma60_val

    # ── 1. 中期趨勢：MA20 > MA60 且 MA60 上揚 ──────────────────────────────
    trend_up = (ma20_val > ma60_val) and (ma60_val > ma60_10ago)

    # ── 2. 長結構完好 ────────────────────────────────────────────────────────
    above_ma60 = last_close > ma60_val

    # ── 3. 拉回買點：價格貼近 MA20（-3% ~ +5%）─────────────────────────────
    dist_ma20_pct = (last_close - ma20_val) / ma20_val * 100 if ma20_val > 0 else 0.0
    pullback_buy = -3.0 <= dist_ma20_pct <= 5.0

    # ── 4. 位階健康：未過度乖離（≤ MA20 +12%）──────────────────────────────
    not_extended = dist_ma20_pct <= 12.0

    # ── 5. 盤整突破：60 日新高 + 前 20 日（不含今天）振幅 < 15% ────────────
    high_60d = float(close.tail(60).max())
    is_60d_high = last_close >= high_60d
    base_breakout = False
    if is_60d_high and len(close) >= 21:
        prior20 = close.iloc[-21:-1]
        rng = (float(prior20.max()) - float(prior20.min())) / float(prior20.min()) * 100
        base_breakout = rng < 15.0

    # ── 6. 量能沉澱：5 日均量 > 20 日均量 1.2x ─────────────────────────────
    v5 = float(avg_vol5.iloc[-1]) if not pd.isna(avg_vol5.iloc[-1]) else 0.0
    v20 = float(avg_vol20.iloc[-1]) if not pd.isna(avg_vol20.iloc[-1]) else 1.0
    vol_ratio = v5 / v20 if v20 > 0 else 0.0
    vol_accumulate = vol_ratio >= 1.2

    # ── 7. RSI-14 波段區（45-70）───────────────────────────────────────────
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] and loss.iloc[-1] != 0 else float("nan")
    rsi = 100 - (100 / (1 + rs)) if not pd.isna(rs) else 50.0
    rsi_swing = 45.0 <= rsi <= 70.0

    # ── 8. 20 日相對強度 vs 大盤 ─────────────────────────────────────────────
    rs_20d = 0.0
    rs_20d_strong = False
    if len(close) >= 21:
        prev20 = float(close.iloc[-21])
        stock_20d_ret = (last_close - prev20) / prev20 * 100 if prev20 > 0 else 0.0
        if taiex_close is not None and len(taiex_close) >= 21:
            t_last = float(taiex_close.iloc[-1])
            t_prev20 = float(taiex_close.iloc[-21])
            taiex_20d = (t_last - t_prev20) / t_prev20 * 100 if t_prev20 > 0 else 0.0
            rs_20d = round(stock_20d_ret - taiex_20d, 2)
            rs_20d_strong = rs_20d >= 3.0

    score = sum([trend_up, above_ma60, pullback_buy, not_extended,
                 base_breakout, vol_accumulate, rsi_swing, rs_20d_strong])

    return {
        "trend_up":       bool(trend_up),
        "above_ma60":     bool(above_ma60),
        "pullback_buy":   bool(pullback_buy),
        "not_extended":   bool(not_extended),
        "base_breakout":  bool(base_breakout),
        "vol_accumulate": bool(vol_accumulate),
        "vol_ratio":      float(round(vol_ratio, 2)),
        "rsi":            float(round(rsi, 1)),
        "rsi_swing":      bool(rsi_swing),
        "rs_20d":         float(rs_20d),
        "rs_20d_strong":  bool(rs_20d_strong),
        "dist_ma20_pct":  float(round(dist_ma20_pct, 1)),
        "score":          int(score),
        "last_close":     float(round(last_close, 2)),
        "ma20":           float(round(ma20_val, 2)),
        "ma60":           float(round(ma60_val, 2)),
    }


# ── Main scanner ──────────────────────────────────────────────────────────────

def run_scanner() -> list[dict]:
    print("[scanner] Fetching TWSE all-stock data...")
    top_stocks = fetch_top_stocks()

    if not top_stocks:
        return []

    print(f"[scanner] Analyzing top {len(top_stocks)} stocks by trade value...")

    tickers = [f"{s['code']}.TW" for s in top_stocks]

    # Include TAIEX index for relative strength; downloaded together to save API calls
    # 6mo history: swing signals need MA60 + 60-day range structure (≥65 bars)
    tickers_dl = tickers + ["^TWII"]
    print(f"[scanner] Downloading 6-month history for {len(tickers)} stocks + TAIEX...")
    try:
        raw = yf.download(
            tickers_dl,
            period="6mo",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"[scanner] yfinance batch download failed: {e}")
        return []

    # Extract TAIEX close for relative strength calculation
    taiex_close = None
    try:
        if isinstance(raw.columns, pd.MultiIndex) and "^TWII" in raw.columns.get_level_values(0):
            taiex_df = raw["^TWII"]
            taiex_close = taiex_df["Close"].dropna() if "Close" in taiex_df.columns else None
            if taiex_close is not None and len(taiex_close) > 0:
                print(f"[scanner] TAIEX RS base: {float(taiex_close.iloc[-1]):.0f} "
                      f"({(float(taiex_close.iloc[-1])-float(taiex_close.iloc[-2]))/float(taiex_close.iloc[-2])*100:+.2f}%)")
    except Exception as e:
        print(f"[scanner] TAIEX extraction failed: {e}")

    candidates = []
    for s in top_stocks:
        code = s["code"]
        ticker = f"{code}.TW"
        try:
            # Always use MultiIndex access (tickers_dl always has ≥2 elements)
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    continue
                df = raw[ticker]
            else:
                df = raw  # fallback for edge case

            signals = calc_signals(df, code, taiex_close=taiex_close)
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

    # Sort: score desc, then 20 日相對強度 desc as tiebreaker
    # （波段版 tiebreaker 從 vol_ratio 改 rs_20d：資金持續性 > 單日爆量）
    candidates.sort(key=lambda x: (x["score"], x.get("rs_20d", 0)), reverse=True)
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


def describe_signals(c: dict) -> list[str]:
    """把 candidate 的波段信號轉成人話清單（scanner CLI + agent prompt 共用）。"""
    sigs = []
    if c.get("trend_up"):       sigs.append("中期趨勢向上")
    if c.get("above_ma60"):     sigs.append("站上季線")
    if c.get("pullback_buy"):   sigs.append(f"貼近MA20買點({c.get('dist_ma20_pct',0):+.1f}%)")
    if c.get("base_breakout"):  sigs.append("盤整突破60日高")
    if c.get("vol_accumulate"): sigs.append(f"量能沉澱{c.get('vol_ratio',0)}x")
    if c.get("rsi_swing"):      sigs.append(f"RSI{c.get('rsi',0)}健康區")
    if c.get("rs_20d_strong"):  sigs.append(f"20日贏大盤{c.get('rs_20d',0):+.1f}%")
    if not c.get("not_extended"): sigs.append("⚠乖離過大")
    return sigs


if __name__ == "__main__":
    results = run_scanner()
    save_candidates(results)
    if results:
        print("\n【波段候選股（Top 5）】")
        for i, c in enumerate(results[:5], 1):
            print(f"  {i}. {c['name']}({c['code']}) "
                  f"score={c['score']} | {' / '.join(describe_signals(c))}")
    else:
        print("[scanner] 今日無符合條件的候選股")
