"""
Market data fetcher — runs before agents every cycle.
Sources: 永豐 Shioaji (台股即時), TWSE Open API, FinMind, yfinance, Frankfurter (JPY rate)
Shioaji is used when SHIOAJI_API_KEY is present; falls back to yfinance automatically.
"""

import json, os, time, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import yfinance as yf
import urllib3
from error_log import log_error, log_warning

# TWSE 證交所 SSL 憑證鏈不完整（Missing Subject Key Identifier）→ 對 TWSE 請求關閉驗證。
# 政府公開資料來源、且非機密路徑，可接受。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
TWSE_VERIFY = False

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
MARKET_FILE = os.path.join(DATA_DIR, "market_data.json")

TW_CODES = ["00692", "00915", "1104", "2211", "2330", "2536", "2834", "3293", "3703", "4588", "4707"]
US_TICKERS = ["AMZN", "CELH", "GOOGL", "MELI", "MSFT", "NVDA", "ONDS", "RBRK", "S", "SOUN", "TSLA", "ZS"]
INDEX_TICKERS = {"taiex": "^TWII", "sp500": "^GSPC", "nasdaq": "^IXIC", "vix": "^VIX"}

# AI/semi sector basket — proxy for signal_fusion.ai_sector_strength.
# Mix of large-cap AI plays (台積電/聯發科) and AI-server/散熱 mid-caps that
# react more sensitively to AI demand cycles. ETF 00891 included as
# a broad confirmation signal.
AI_SECTOR_BASKET = [
    "2330",   # 台積電  — AI chip fabricator
    "2454",   # 聯發科  — AI SoC
    "3017",   # 奇鋐    — AI server 散熱
    "3653",   # 健策    — AI server 散熱
    "6669",   # 緯穎    — AI server ODM
    "3661",   # 世芯-KY — ASIC
    "00891",  # 中信關鍵半導體 ETF
]


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


def fetch_ai_sector_momentum() -> dict:
    """Compute AI/semi sector momentum from a basket of TW AI stocks + ETF.
    Used by signal_fusion to compute ai_sector_strength as a real number
    instead of the previous news-keyword inference (batch 2 target).

    Returns:
      {
        "constituents": {"2330": {"close": .., "change_pct": ..}, ...},
        "avg_change_pct": +1.23,   # mean daily change of available members
        "median_change_pct": +0.88,
        "count": 6                 # how many members had valid data
      }
    """
    members = {}
    changes = []
    for code in AI_SECTOR_BASKET:
        ticker = f"{code}.TW" if not code.startswith("00") else f"{code}.TW"
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if len(hist) >= 2:
                prev, curr = float(hist["Close"].iloc[-2]), float(hist["Close"].iloc[-1])
                chg_pct = round((curr - prev) / prev * 100, 2)
                members[code] = {"close": round(curr, 2), "change_pct": chg_pct}
                changes.append(chg_pct)
        except Exception as e:
            print(f"  [WARN] AI basket {code}: {e}")

    if not changes:
        return {"constituents": {}, "avg_change_pct": None, "median_change_pct": None, "count": 0}

    avg = round(sum(changes) / len(changes), 3)
    med = round(sorted(changes)[len(changes) // 2], 3)
    print(f"  [AI sector] {len(changes)}/{len(AI_SECTOR_BASKET)} members, avg {avg:+.2f}% median {med:+.2f}%")
    return {
        "constituents":      members,
        "avg_change_pct":    avg,
        "median_change_pct": med,
        "count":             len(changes),
    }


def fetch_tw_institutional(date_str: str = None) -> dict:
    """三大法人買賣超 from TWSE Open API."""
    if not date_str:
        date_str = date.today().strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/fund/TWT38U?response=json&date={date_str}"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, verify=TWSE_VERIFY)
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


def _fetch_market_breadth_for_date(date_str: str) -> dict:
    """單日 MI_INDEX 抓取（不含 fallback 邏輯）.
    新格式（2026 起）：fields=['類型', '整體市場', '股票'],
                       data=[['上漲(漲停)', '6,492(374)', '335(29)'], ...]
    """
    import re
    url = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
    params = {"response": "json", "type": "ALLBUT0999", "date": date_str}
    try:
        r = requests.get(url, params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, verify=TWSE_VERIFY)
        raw = r.json()
        if raw.get("stat") != "OK":
            return {}

        # Locate table by title "漲跌證券數合計"
        target = None
        for t in raw.get("tables", []):
            if "漲跌證券數合計" in str(t.get("title", "")):
                target = t
                break
        if not target:
            print("  [WARN] MI_INDEX: 找不到「漲跌證券數合計」表")
            return {}

        fields = target.get("fields", [])
        data = target.get("data", [])
        # Find "股票" column index (only-stock, excludes ETF/warrants — preferred for breadth signal)
        try:
            col_stock = fields.index("股票")
        except ValueError:
            col_stock = 2  # fallback to known position

        def parse_count(s):
            """Parse "335(29)" → (main_count, limit_count). Plain "57" → (57, 0)."""
            s = str(s).replace(",", "").strip()
            m = re.match(r"^(-?\d+)(?:\((-?\d+)\))?$", s)
            if not m:
                return 0, 0
            return int(m.group(1)), int(m.group(2) or 0)

        advance = decline = unchanged = limit_up = limit_down = 0
        for row in data:
            if not row or col_stock >= len(row):
                continue
            label = str(row[0])
            main, limit = parse_count(row[col_stock])
            if "上漲" in label:
                advance = main           # main already includes limit_up
                limit_up = limit
            elif "下跌" in label:
                decline = main
                limit_down = limit
            elif "持平" in label or "平盤" in label or "未漲跌" in label:
                unchanged = main

        total_directional = advance + decline
        if total_directional == 0:
            return {}

        advance_ratio = round(advance / total_directional, 3)
        print(f"  [Breadth] 上漲:{advance}({limit_up}漲停) 下跌:{decline}({limit_down}跌停) 持平:{unchanged} 廣度:{advance_ratio:.2%}")
        return {
            "advance":       advance,
            "decline":       decline,
            "limit_up":      limit_up,
            "limit_down":    limit_down,
            "unchanged":     unchanged,
            "advance_ratio": advance_ratio,
            "total_stocks":  advance + decline + unchanged,
        }
    except Exception as e:
        print(f"  [WARN] MI_INDEX breadth: {e}")
        log_warning("fetch:MI_INDEX", str(e)[:300])
        return {}


def fetch_market_breadth() -> dict:
    """台股漲跌家數 — 自動回溯到最近一個有資料的交易日."""
    return _twse_fetch_with_fallback(_fetch_market_breadth_for_date)


def _twse_fetch_with_fallback(fetcher, max_days_back: int = 7):
    """呼叫 fetcher(date_str)，若回空就往前一交易日找，最多回溯 max_days_back 天。
    覆蓋情境：盤中（當天還沒出資料）、週末、國定假日。
    """
    base = date.today()
    for back in range(max_days_back + 1):
        d = (base - timedelta(days=back))
        if d.weekday() >= 5:  # Sat/Sun — TWSE 不發
            continue
        date_str = d.strftime("%Y%m%d")
        result = fetcher(date_str)
        if result:
            if back > 0:
                print(f"    [fallback] 使用 {date_str} 的資料（往前 {back} 天）")
            return result
    return {}


def _fetch_market_institutional_for_date(date_str: str) -> dict:
    """單日 BFI82U 抓取（不含 fallback 邏輯）."""
    url = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
    params = {"dayDate": date_str, "type": "day", "response": "json"}
    try:
        r = requests.get(url, params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, verify=TWSE_VERIFY)
        raw = r.json()
        if raw.get("stat") != "OK":
            return {}

        data = raw.get("data", [])
        if not data:
            return {}

        def parse_int(s):
            try:
                return int(str(s).replace(",", "").strip())
            except (ValueError, TypeError):
                return 0

        foreign_net = trust_net = dealer_net = total_net = 0
        for row in data:
            if not row or len(row) < 4:
                continue
            name = str(row[0]).strip()
            net = parse_int(row[3])  # 買賣差額
            if name.startswith("外資及陸資"):
                foreign_net = net      # 含「(不含外資自營商)」註記，這是主要外資數
            elif name == "外資自營商":
                dealer_net += net      # 外資自營商歸入 dealer
            elif "投信" in name:
                trust_net = net
            elif name.startswith("自營商"):
                dealer_net += net      # 自營商(自行買賣) + 自營商(避險)
            elif "合計" in name:
                total_net = net

        print(
            f"  [BFI82U] 外資:{foreign_net/1e8:+.1f}億 投信:{trust_net/1e8:+.1f}億 "
            f"自營:{dealer_net/1e8:+.1f}億 合計:{total_net/1e8:+.1f}億"
        )
        return {
            "foreign_net_amount":       foreign_net,    # 元
            "trust_net_amount":         trust_net,
            "dealer_net_amount":        dealer_net,
            "total_institutional_net":  total_net,
            "foreign_net_positive":     foreign_net > 0,
            "date":                     date_str,
            # Backward-compat: keep "shares" name pointing to amount (will normalize differently in signal_fusion)
            "foreign_net_shares":       foreign_net,
            "trust_net_shares":         trust_net,
            "dealer_net_shares":        dealer_net,
            "unit":                     "TWD",          # 元
        }
    except Exception as e:
        print(f"  [WARN] BFI82U institutional: {e}")
        log_warning("fetch:BFI82U", str(e)[:300])
        return {}


def fetch_market_institutional() -> dict:
    """全市場三大法人買賣超 — 自動回溯到最近一個有資料的交易日."""
    return _twse_fetch_with_fallback(_fetch_market_institutional_for_date)


def _fetch_margin_balance_for_date(date_str: str) -> dict:
    """單日 MI_MARGN 抓取（不含 fallback 邏輯）."""
    url = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
    params = {"date": date_str, "selectType": "MS", "response": "json"}
    try:
        r = requests.get(url, params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, verify=TWSE_VERIFY)
        raw = r.json()
        if raw.get("stat") != "OK":
            return {}

        def parse_int(s):
            try:
                return int(str(s).replace(",", "").strip())
            except (ValueError, TypeError):
                return 0

        margin_today = margin_prev = short_today = short_prev = 0
        for t in raw.get("tables", []):
            for row in t.get("data", []):
                if not row or len(row) < 6:
                    continue
                label = str(row[0])
                if "融資金額" in label and "仟元" in label:
                    margin_prev = parse_int(row[4]) * 1000     # 前日餘額 → 元
                    margin_today = parse_int(row[5]) * 1000    # 今日餘額 → 元
                elif "融券" in label and "交易單位" in label:
                    short_prev = parse_int(row[4])
                    short_today = parse_int(row[5])

        if margin_today == 0:
            return {}

        balance_change = margin_today - margin_prev
        balance_change_pct = round(balance_change / max(margin_prev, 1) * 100, 3) if margin_prev else 0

        print(
            f"  [Margin] 融資餘額:{margin_today/1e8:.0f}億 ({balance_change_pct:+.2f}%) "
            f"融券:{short_today:,}張"
        )
        return {
            "margin_balance_amount":    margin_today,
            "margin_prev_balance":      margin_prev,
            "margin_change_amount":     balance_change,
            "margin_change_pct":        balance_change_pct,
            "short_balance_units":      short_today,
            "short_prev_units":         short_prev,
            "date":                     date_str,
        }
    except Exception as e:
        print(f"  [WARN] MI_MARGN: {e}")
        log_warning("fetch:MI_MARGN", str(e)[:300])
        return {}


def fetch_margin_balance() -> dict:
    """全市場融資/融券餘額 — 自動回溯到最近一個有資料的交易日."""
    return _twse_fetch_with_fallback(_fetch_margin_balance_for_date)


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

    print("  market institutional total (BFI82U)...")
    institutional_market = fetch_market_institutional()

    print("  margin balance (MI_MARGN)...")
    margin_balance = fetch_margin_balance()

    print("  technical indicators (FinMind)...")
    technicals = {}
    for code in TW_CODES:
        technicals[code] = fetch_finmind_technical(code)
        time.sleep(0.3)  # be polite to free tier

    print("  JPY rate...")
    jpy = fetch_jpy_rate()

    print("  AI sector basket momentum...")
    ai_sector = fetch_ai_sector_momentum()

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
        "margin_balance": margin_balance,
        "ai_sector": ai_sector,
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
