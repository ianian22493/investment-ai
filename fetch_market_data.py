"""
Market data fetcher — runs before agents every cycle.
Sources: 永豐 Shioaji (台股即時), TWSE Open API, FinMind, yfinance, Frankfurter (JPY rate)
Shioaji is used when SHIOAJI_API_KEY is present; falls back to yfinance automatically.
"""

import json, os, time, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import yfinance as yf

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
MARKET_FILE = os.path.join(DATA_DIR, "market_data.json")

TW_CODES = ["00692", "00915", "1104", "2211", "2330", "2536", "2834", "3293", "3703", "4588", "4707"]
US_TICKERS = ["AMZN", "CELH", "GOOGL", "MELI", "MSFT", "NVDA", "ONDS", "RBRK", "S", "SOUN", "TSLA", "ZS"]
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


def fetch_shioaji_data() -> tuple[dict, list]:
    """台股即時報價 + 實際持倉 via 永豐 Shioaji（單次登入取兩種資料）.
    Returns: (prices_dict, positions_list)
    positions_list: [{"code", "shares", "avg_cost", "pnl"}, ...]
    """
    api_key    = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")
    if not api_key or not secret_key:
        return {}, []
    try:
        import shioaji as sj
        api = sj.Shioaji(simulation=False)
        api.login(api_key=api_key, secret_key=secret_key)

        # 1. 即時報價
        contracts = []
        for code in TW_CODES:
            try:
                contracts.append(api.Contracts.Stocks[code])
            except Exception:
                pass

        snapshots = api.snapshots(contracts)
        prices = {}
        for snap in snapshots:
            code = snap.code
            prices[code] = {
                "close":      round(float(snap.close), 2),
                "open":       round(float(snap.open), 2),
                "high":       round(float(snap.high), 2),
                "low":        round(float(snap.low), 2),
                "change_pct": round(float(snap.change_rate), 2),
                "volume":     int(snap.volume),
                "amount":     int(snap.amount),
                "source":     "shioaji_realtime",
            }

        # 2. 實際持倉（quantity 單位：股）
        positions = []
        try:
            raw = api.list_positions(api.stock_account)
            for p in raw:
                if int(p.quantity) > 0:
                    positions.append({
                        "code":     str(p.code),
                        "shares":   int(p.quantity),
                        "avg_cost": round(float(p.price), 2),
                        "pnl":      round(float(p.pnl), 0),
                    })
            print(f"  [Shioaji] 持倉 {len(positions)} 檔: {[p['code'] for p in positions]}")
        except Exception as e:
            print(f"  [WARN] Shioaji list_positions: {e}")

        api.logout()
        print(f"  [Shioaji] 即時報價取得 {len(prices)} 檔")
        return prices, positions
    except Exception as e:
        print(f"  [WARN] Shioaji failed: {e} — falling back to yfinance")
        return {}, []


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


def fetch_tw_stocks() -> tuple[dict, list]:
    """台股報價 + 持倉：優先用 Shioaji 即時，失敗則用 yfinance（無持倉）。
    Returns: (prices_dict, positions_list)
    """
    prices, positions = fetch_shioaji_data()
    if not prices:
        prices = fetch_tw_stocks_yfinance()
    return prices, positions


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


def fetch_market_breadth() -> dict:
    """台股漲跌家數 via TWSE MI_INDEX（全市場廣度指標）.
    Returns: advance, decline, unchanged, limit_up, limit_down, advance_ratio
    """
    url = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
    params = {"response": "json", "type": "ALLBUT0999"}
    try:
        r = requests.get(url, params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        raw = r.json()
        if raw.get("stat") != "OK":
            return {}

        advance = decline = unchanged = limit_up = limit_down = 0

        # MI_INDEX returns multiple tables; find the one with 漲跌家數
        tables = raw.get("tables", [])
        for table in tables:
            fields = table.get("fields", [])
            data = table.get("data", [])
            # Look for table with "漲跌情形" or "漲停" in fields/data
            field_str = " ".join(str(f) for f in fields)
            if "漲跌" not in field_str and not any("漲停" in str(r[0]) for r in data[:3] if r):
                continue
            for row in data:
                if not row:
                    continue
                try:
                    label = str(row[0])
                    # Count column: last numeric column
                    cnt_str = next(
                        (str(row[i]).replace(",", "").strip() for i in range(len(row)-1, -1, -1)
                         if str(row[i]).replace(",", "").strip().lstrip("-").isdigit()), "0"
                    )
                    cnt = int(cnt_str)
                    if "漲停" in label:
                        limit_up += cnt
                    elif "上漲" in label:
                        advance += cnt
                    elif "未漲跌" in label or "平盤" in label:
                        unchanged += cnt
                    elif "下跌" in label and "跌停" not in label:
                        decline += cnt
                    elif "跌停" in label:
                        limit_down += cnt
                except (ValueError, IndexError):
                    continue

        total_directional = advance + limit_up + decline + limit_down
        if total_directional == 0:
            return {}

        # Include limit-up/down in advance/decline for ratio
        adv_total = advance + limit_up
        dec_total = decline + limit_down
        advance_ratio = round(adv_total / max(adv_total + dec_total, 1), 3)

        print(f"  [Breadth] 上漲:{adv_total} 下跌:{dec_total} 漲停:{limit_up} 跌停:{limit_down} 廣度:{advance_ratio:.2%}")
        return {
            "advance":       adv_total,
            "decline":       dec_total,
            "limit_up":      limit_up,
            "limit_down":    limit_down,
            "unchanged":     unchanged,
            "advance_ratio": advance_ratio,
            "total_stocks":  adv_total + dec_total + unchanged,
        }
    except Exception as e:
        print(f"  [WARN] MI_INDEX breadth: {e}")
        return {}


def fetch_market_institutional(date_str: str = None) -> dict:
    """全市場三大法人買賣超 via TWSE T86（市場級，非個股）.
    Returns: foreign_net_shares, trust_net_shares, dealer_net_shares, total_institutional_net
    單位：股
    """
    if not date_str:
        date_str = date.today().strftime("%Y%m%d")
    url = "https://www.twse.com.tw/rwd/zh/fund/T86"
    params = {"date": date_str, "selectType": "ALLBUT0999", "response": "json"}
    try:
        r = requests.get(url, params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        raw = r.json()
        if raw.get("stat") != "OK":
            return {}

        fields = raw.get("fields", [])
        data   = raw.get("data", [])
        if not data or not fields:
            return {}

        # Last data row = 合計（全市場匯總）
        total_row = data[-1]

        def parse_int(s):
            try:
                return int(str(s).replace(",", "").strip())
            except (ValueError, TypeError):
                return 0

        foreign_net = trust_net = dealer_net = total_net = 0
        for i, field in enumerate(fields):
            if i >= len(total_row):
                break
            val = parse_int(total_row[i])
            # 外資及陸資（不含外資自營商）買賣超
            if "外資" in field and "自營商" not in field and "買賣超" in field:
                foreign_net = val
            # 投信買賣超
            elif "投信" in field and "買賣超" in field:
                trust_net = val
            # 自營商（自行買賣）買賣超（取合計）
            elif "自營商" in field and "買賣超" in field and "避險" not in field:
                dealer_net += val
            # 三大法人合計
            elif "三大法人" in field and "合計" in field:
                total_net = val

        print(
            f"  [T86] 外資:{foreign_net:+,} 投信:{trust_net:+,} "
            f"自營:{dealer_net:+,} 合計:{total_net:+,}"
        )
        return {
            "foreign_net_shares":       foreign_net,
            "trust_net_shares":         trust_net,
            "dealer_net_shares":        dealer_net,
            "total_institutional_net":  total_net,
            "foreign_net_positive":     foreign_net > 0,
            "date":                     date_str,
        }
    except Exception as e:
        print(f"  [WARN] T86 institutional: {e}")
        return {}


def fetch_finmind_technical(code: str) -> dict:
    """Technical indicators from FinMind (free tier)."""
    try:
        today = date.today().strftime("%Y-%m-%d")
        start = (date.today() - timedelta(days=180)).strftime("%Y-%m-%d")
        url = (
            f"https://api.finmindtrade.com/api/v4/data?"
            f"dataset=TaiwanStockPrice&data_id={code}&start_date={start}&end_date={today}"
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


def fetch_news() -> dict:
    """Recent market news via Google News RSS (no API key needed)."""
    try:
        import feedparser
        tw_feed = feedparser.parse(
            "https://news.google.com/rss/search?q=台股+股市&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        )
        us_feed = feedparser.parse(
            "https://news.google.com/rss/search?q=stock+market+Wall+Street&hl=en-US&gl=US&ceid=US:en"
        )
        tw = [e.title for e in tw_feed.entries[:12]]
        us = [e.title for e in us_feed.entries[:10]]
        print(f"  [News] TW:{len(tw)} US:{len(us)} headlines")
        return {"tw": tw, "us": us}
    except Exception as e:
        print(f"  [WARN] News fetch: {e}")
        return {"tw": [], "us": []}


def fetch_usd_twd() -> float | None:
    """USD/TWD spot rate via Frankfurter."""
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=USD&to=TWD", timeout=10)
        return r.json().get("rates", {}).get("TWD")
    except Exception:
        return None


def compute_portfolio_value(portfolio: dict, tw_prices: dict, us_prices: dict, usd_twd: float, tw_positions: list = None) -> dict:
    """Compute current total portfolio value in TWD with dynamic P&L.
    tw_positions: live positions from Shioaji; if provided, overrides portfolio["tw_stocks"] shares/cost.
    """
    # Build TW stocks live list
    tw_stocks_live = []
    tw_val = 0

    if tw_positions:
        # Use live positions from brokerage (auto-sync)
        portfolio_meta = {s["code"]: s for s in portfolio["tw_stocks"]}
        for p in tw_positions:
            code = p["code"]
            meta = portfolio_meta.get(code, {})
            close = tw_prices.get(code, {}).get("close")
            cost = round(p["shares"] * p["avg_cost"])
            dyn_value = round(p["shares"] * close) if close else cost
            dyn_pnl = dyn_value - cost
            dyn_pnl_pct = round(dyn_pnl / cost * 100, 2) if cost else 0
            tw_stocks_live.append({
                "code":     code,
                "name":     meta.get("name", code),
                "shares":   p["shares"],
                "value":    dyn_value,
                "cost":     cost,
                "pnl":      dyn_pnl,
                "pnl_pct":  dyn_pnl_pct,
                "type":     meta.get("type", "stock"),
                "strategy": meta.get("strategy", "long"),
            })
            tw_val += dyn_value
    else:
        # Fallback: use static portfolio.json data
        for s in portfolio["tw_stocks"]:
            close = tw_prices.get(s["code"], {}).get("close")
            dyn_value = round(s["shares"] * close) if close else s["value"]
            cost = s.get("cost", dyn_value)
            dyn_pnl = dyn_value - cost
            dyn_pnl_pct = round(dyn_pnl / cost * 100, 2) if cost else 0
            tw_stocks_live.append({**s, "value": dyn_value, "pnl": dyn_pnl, "pnl_pct": dyn_pnl_pct})
            tw_val += dyn_value

    tw_cost_total = sum(s["cost"] for s in tw_stocks_live)
    tw_pnl_total = tw_val - tw_cost_total
    tw_pnl_pct = round(tw_pnl_total / tw_cost_total * 100, 2) if tw_cost_total else 0

    # US stocks
    us_val_usd = sum(
        s["shares"] * us_prices.get(s["ticker"], {}).get("close", 0)
        for s in portfolio["us_stocks"]
    )
    us_val_twd = round(us_val_usd * (usd_twd or 32), 0)

    us_cost_usd = sum(
        s.get("avg_cost_usd", 0) * s["shares"]
        for s in portfolio["us_stocks"]
    )
    us_pnl_usd = round(us_val_usd - us_cost_usd, 2) if us_cost_usd else None
    us_pnl_pct = round(us_pnl_usd / us_cost_usd * 100, 2) if (us_cost_usd and us_pnl_usd is not None) else None

    fund_val_twd = portfolio["funds_summary"]["total_value_twd"]
    re_val = portfolio["real_estate"]["total_price"]
    total = tw_val + us_val_twd + fund_val_twd + re_val

    return {
        "tw_stocks_twd": tw_val,
        "tw_stocks_live": tw_stocks_live,
        "tw_summary_live": {
            "total_value": tw_val,
            "total_cost": tw_cost_total,
            "total_pnl": tw_pnl_total,
            "total_pnl_pct": tw_pnl_pct,
            "broker": portfolio["tw_summary"].get("broker", ""),
        },
        "us_stocks_twd": int(us_val_twd),
        "us_stocks_usd": round(us_val_usd, 2),
        "us_cost_usd": round(us_cost_usd, 2) if us_cost_usd else None,
        "us_pnl_usd": us_pnl_usd,
        "us_pnl_pct": us_pnl_pct,
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

    print("  TW stocks + positions...")
    tw_prices, tw_positions = fetch_tw_stocks()

    print("  US stocks...")
    us_prices = fetch_us_stocks()

    print("  TW institutional (per-stock)...")
    institutional = fetch_tw_institutional()

    print("  market breadth (MI_INDEX)...")
    breadth = fetch_market_breadth()

    print("  market institutional total (T86)...")
    institutional_market = fetch_market_institutional()

    print("  technical indicators (FinMind)...")
    technicals = {}
    for code in TW_CODES:
        technicals[code] = fetch_finmind_technical(code)
        time.sleep(0.3)  # be polite to free tier

    print("  JPY rate...")
    jpy = fetch_jpy_rate()

    print("  news headlines...")
    news = fetch_news()

    print("  USD/TWD rate...")
    usd_twd = fetch_usd_twd() or 32.0

    print("  computing portfolio value...")
    pf_value = compute_portfolio_value(portfolio, tw_prices, us_prices, usd_twd, tw_positions or None)

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
        "tw_positions_live": tw_positions,
        "breadth": breadth,
        "institutional_market": institutional_market,
        "news": news,
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
