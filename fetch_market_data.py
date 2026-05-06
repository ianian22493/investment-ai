"""
Market data fetcher — runs before agents every cycle.
Sources: 永豐 Shioaji (台股即時), TWSE Open API, FinMind, yfinance, Frankfurter (JPY rate)
Shioaji is used when SHIOAJI_API_KEY is present; falls back to yfinance automatically.
"""

import json, os, time, requests
from datetime import datetime, date
from zoneinfo import ZoneInfo
import yfinance as yf

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
MARKET_FILE = os.path.join(DATA_DIR, "market_data.json")

TW_CODES = ["00692", "00915", "1104", "2211", "2330", "2536", "2834", "3293", "3703", "4588", "4707"]
US_TICKERS = ["AMZN", "CELH", "GOOGL", "MELI", "MSFT", "NVDA", "ONDS", "RBRK", "S", "SMR", "SOUN", "TSLA", "TTD", "ZS"]
INDEX_TICKERS = {"taiex": "^TWII", "sp500": "^GSPC", "nasdaq": "^IXIC", "vix": "^VIX"}


def fetch_twse_index() -> dict:
    """TAIEX and major index data via yfinance."""
    result = {}
    for name, ticker in INDEX_TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) >= 2:
                prev, curr = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                result[name] = {
                    "close": round(float(curr), 2),
                    "prev_close": round(float(prev), 2),
                    "change": round(float(curr - prev), 2),
                    "change_pct": round(float((curr - prev) / prev * 100), 2),
                }
            elif len(hist) == 1:
                result[name] = {"close": round(float(hist["Close"].iloc[-1]), 2), "change_pct": 0}
        except Exception as e:
            print(f"  [WARN] {name} ({ticker}): {e}")
            result[name] = {}
    return result


def fetch_tw_stocks_shioaji() -> dict:
    """台股即時報價 via 永豐 Shioaji（需 SHIOAJI_API_KEY + SHIOAJI_SECRET_KEY）."""
    api_key    = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")
    if not api_key or not secret_key:
        return {}
    try:
        import shioaji as sj
        api = sj.Shioaji(simulation=False)
        api.login(api_key=api_key, secret_key=secret_key)

        contracts = []
        for code in TW_CODES:
            try:
                contracts.append(api.Contracts.Stocks[code])
            except Exception:
                pass

        snapshots = api.snapshots(contracts)
        result = {}
        for snap in snapshots:
            code = snap.code
            result[code] = {
                "close":       round(float(snap.close), 2),
                "open":        round(float(snap.open), 2),
                "high":        round(float(snap.high), 2),
                "low":         round(float(snap.low), 2),
                "change_pct":  round(float(snap.change_rate), 2),
                "volume":      int(snap.volume),
                "amount":      int(snap.amount),
                "source":      "shioaji_realtime",
            }

        api.logout()
        print(f"  [Shioaji] 即時報價取得 {len(result)} 檔")
        return result
    except Exception as e:
        print(f"  [WARN] Shioaji failed: {e} — falling back to yfinance")
        return {}


def fetch_tw_stocks_yfinance() -> dict:
    """台股收盤價 via yfinance（備援，無 Shioaji 時使用）."""
    result = {}
    for code in TW_CODES:
        ticker = f"{code}.TW"
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if len(hist) >= 2:
                prev, curr = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                vol = hist["Volume"].iloc[-1]
                avg_vol = hist["Volume"].mean()
                result[code] = {
                    "close": round(float(curr), 2),
                    "prev_close": round(float(prev), 2),
                    "change_pct": round(float((curr - prev) / prev * 100), 2),
                    "volume": int(vol),
                    "volume_ratio": round(float(vol / avg_vol), 2),
                    "source": "yfinance_eod",
                }
        except Exception as e:
            print(f"  [WARN] TW {code}: {e}")
            result[code] = {}
    return result


def fetch_tw_stocks() -> dict:
    """台股報價：優先用 Shioaji 即時，失敗則用 yfinance 收盤。"""
    result = fetch_tw_stocks_shioaji()
    if not result:
        result = fetch_tw_stocks_yfinance()
    return result


def fetch_us_stocks() -> dict:
    """US stock prices via yfinance."""
    result = {}
    for ticker in US_TICKERS:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) >= 2:
                prev, curr = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                result[ticker] = {
                    "close": round(float(curr), 2),
                    "change_pct": round(float((curr - prev) / prev * 100), 2),
                }
        except Exception as e:
            print(f"  [WARN] US {ticker}: {e}")
            result[ticker] = {}
    return result


def fetch_tw_institutional(date_str: str = None) -> dict:
    """三大法人買賣超 from TWSE Open API."""
    if not date_str:
        date_str = date.today().strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/fund/TWT38U?response=json&date={date_str}"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        if data.get("stat") != "OK":
            return {}
        rows = data.get("data", [])
        result = {}
        for row in rows:
            code = row[0].strip()
            if code in TW_CODES:
                try:
                    result[code] = {
                        "foreign_net": int(row[4].replace(",", "")),   # 外資買超
                        "trust_net": int(row[10].replace(",", "")),    # 投信買超
                        "dealer_net": int(row[14].replace(",", "")),   # 自營商買超
                        "total_net": int(row[18].replace(",", "")),    # 三大法人合計
                    }
                except (IndexError, ValueError):
                    result[code] = {}
        return result
    except Exception as e:
        print(f"  [WARN] TWSE institutional data: {e}")
        return {}


def fetch_finmind_technical(code: str) -> dict:
    """Technical indicators from FinMind (free tier)."""
    try:
        today = date.today().strftime("%Y-%m-%d")
        url = (
            f"https://api.finmindtrade.com/api/v4/data?"
            f"dataset=TaiwanStockPrice&data_id={code}&start_date=2025-11-01&end_date={today}"
        )
        r = requests.get(url, timeout=15)
        data = r.json()
        records = data.get("data", [])
        if not records:
            return {}

        closes = [float(d["close"]) for d in records[-60:]]
        volumes = [float(d["Trading_Volume"]) for d in records[-60:]]

        def sma(arr, n):
            if len(arr) < n:
                return None
            return round(sum(arr[-n:]) / n, 2)

        ma5  = sma(closes, 5)
        ma10 = sma(closes, 10)
        ma20 = sma(closes, 20)
        ma60 = sma(closes, 60)
        curr = closes[-1]

        return {
            "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
            "above_ma20": curr > ma20 if ma20 else None,
            "above_ma60": curr > ma60 if ma60 else None,
            "vol_5d_avg": round(sum(volumes[-5:]) / 5, 0) if len(volumes) >= 5 else None,
        }
    except Exception as e:
        print(f"  [WARN] FinMind {code}: {e}")
        return {}


def fetch_jpy_rate() -> dict:
    """JPY/TWD and USD/JPY via Frankfurter API (free, no key needed)."""
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=JPY&to=TWD,USD", timeout=10)
        rates = r.json().get("rates", {})
        twd_per_jpy = rates.get("TWD")
        usd_per_jpy = rates.get("USD")
        jpy_per_usd = round(1 / usd_per_jpy, 2) if usd_per_jpy else None
        return {
            "twd_per_jpy": round(twd_per_jpy, 4) if twd_per_jpy else None,
            "jpy_per_usd": jpy_per_usd,
        }
    except Exception as e:
        print(f"  [WARN] JPY rate: {e}")
        return {}


def fetch_usd_twd() -> float | None:
    """USD/TWD spot rate via Frankfurter."""
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=USD&to=TWD", timeout=10)
        return r.json().get("rates", {}).get("TWD")
    except Exception:
        return None


def compute_portfolio_value(portfolio: dict, us_prices: dict, usd_twd: float) -> dict:
    """Compute current total portfolio value in TWD."""
    tw_val = portfolio["tw_summary"]["total_value"]

    us_val_usd = sum(
        s["shares"] * us_prices.get(s["ticker"], {}).get("close", 0)
        for s in portfolio["us_stocks"]
    )
    us_val_twd = round(us_val_usd * (usd_twd or 32), 0)

    fund_val_twd = portfolio["funds_summary"]["total_value_twd"]
    re_val = portfolio["real_estate"]["total_price"]

    total = tw_val + us_val_twd + fund_val_twd + re_val

    return {
        "tw_stocks_twd": tw_val,
        "us_stocks_twd": int(us_val_twd),
        "us_stocks_usd": round(us_val_usd, 2),
        "funds_twd": fund_val_twd,
        "real_estate_twd": re_val,
        "loan_twd": portfolio["real_estate"]["loan_amount"],
        "net_assets_twd": int(total - portfolio["real_estate"]["loan_amount"]),
        "total_gross_twd": int(total),
        "allocation_pct": {
            "tw_stocks": round(tw_val / total * 100, 1),
            "us_stocks": round(us_val_twd / total * 100, 1),
            "funds": round(fund_val_twd / total * 100, 1),
            "real_estate": round(re_val / total * 100, 1),
        }
    }


def run():
    print(f"[{datetime.now(TZ).strftime('%H:%M:%S')}] Fetching market data...")

    with open(PORTFOLIO_FILE) as f:
        portfolio = json.load(f)

    print("  indices...")
    indices = fetch_twse_index()

    print("  TW stocks...")
    tw_prices = fetch_tw_stocks()

    print("  US stocks...")
    us_prices = fetch_us_stocks()

    print("  TW institutional...")
    institutional = fetch_tw_institutional()

    print("  technical indicators (FinMind)...")
    technicals = {}
    for code in TW_CODES:
        technicals[code] = fetch_finmind_technical(code)
        time.sleep(0.3)  # be polite to free tier

    print("  JPY rate...")
    jpy = fetch_jpy_rate()

    print("  USD/TWD rate...")
    usd_twd = fetch_usd_twd() or 32.0

    print("  computing portfolio value...")
    pf_value = compute_portfolio_value(portfolio, us_prices, usd_twd)

    market_data = {
        "fetched_at": datetime.now(TZ).isoformat(),
        "indices": indices,
        "tw_stocks": {
            code: {**tw_prices.get(code, {}), **{"institutional": institutional.get(code, {}), "technical": technicals.get(code, {})}}
            for code in TW_CODES
        },
        "us_stocks": us_prices,
        "fx": {
            "usd_twd": round(usd_twd, 4),
            **jpy
        },
        "portfolio_value": pf_value,
    }

    with open(MARKET_FILE, "w") as f:
        json.dump(market_data, f, ensure_ascii=False, indent=2)

    print(f"  saved → data/market_data.json")
    print(f"  TAIEX: {indices.get('taiex', {}).get('close')} ({indices.get('taiex', {}).get('change_pct')}%)")
    print(f"  S&P500: {indices.get('sp500', {}).get('close')} ({indices.get('sp500', {}).get('change_pct')}%)")
    print(f"  USD/JPY: {jpy.get('jpy_per_usd')}, JPY/TWD: {jpy.get('twd_per_jpy')}")
    print(f"  Net assets: NT${pf_value.get('net_assets_twd'):,}")
    print("Done.")


if __name__ == "__main__":
    run()
