# -*- coding: utf-8 -*-
"""
決策記分卡結算腳本 (2026-09-05 建) — 系統的「對答案」迴路。
讀 決策記分卡.md 每筆決策 → 拉即時價 + 對應大盤同期 → 算 alpha → 打分。
判對錯用 alpha (贏大盤多少) 不用絕對漲跌 (憲法鐵律#8)。
benchmark: .TW→^TWII / .TWO→^TWOII / 美股→SPY。
每季 (1/4/7/10月) 重跑一次。用法: PYTHONUTF8=1 python -X utf8 screener/scorecard_reconcile.py
"""
import re, sys, datetime
import yfinance as yf
import pandas as pd

SCORECARD = r"C:\Users\USER\Desktop\Cowork\寶藏股研究室\決策記分卡.md"
YEAR = 2026

# 美股 ticker 白名單 (從 股票 欄第一個 token 抓)
US_TICKERS = {"FPS","CRH","ACN","NKE","LVS","MRVL","INTC","BE","MOD","AVGO","CRDO","AAOI"}

def classify(decision):
    d = decision
    if "審判" in d or "全個股" in d:
        return "SKIP"
    if "加" in d and "pass" not in d.lower():
        return "ADD"
    if any(k in d.lower() for k in ["pass","避開","剔除","移出","非收稅口"]):
        return "PASS"
    # 翻案/修正=續抱(bullish-hold)、觀察/彩券/雷達/S0=interested
    return "WATCH"

def parse_price(s):
    s = s.replace("$","").replace("~"," ").replace("→"," ")
    m = re.search(r"\d+\.?\d*", s)
    return float(m.group()) if m else None

def map_ticker(stock):
    stock = stock.strip()
    tok = stock.split()[0] if stock.split() else stock
    tok = tok.replace("(美)","")
    # 美股
    if tok in US_TICKERS or "(美)" in stock:
        t = re.match(r"[A-Z]+", tok)
        if t: return t.group(), "US"
    # 台股: 前導數字
    m = re.match(r"(\d{4})", stock)
    if m: return m.group(1), "TW"
    return None, None

def load_series(ticker, market):
    """回傳 (price_series, benchmark_ticker解析後的用哪個)。"""
    if market == "US":
        tk = yf.Ticker(ticker)
        h = tk.history(start="2026-07-25", auto_adjust=True)
        if len(h): return h["Close"], "US"
        return None, None
    # TW: 試 .TW 再 .TWO
    for sfx, bench in [(".TW","TWII"),(".TWO","TWOII")]:
        h = yf.Ticker(ticker+sfx).history(start="2026-07-25", auto_adjust=True)
        if len(h) > 5:
            return h["Close"], bench
    return None, None

def asof(series, date):
    """取 date (含) 當日或之前最近的收盤。"""
    s = series[series.index.tz_localize(None) <= pd.Timestamp(date)] if series.index.tz is not None else series[series.index <= pd.Timestamp(date)]
    return float(s.iloc[-1]) if len(s) else None

def main():
    txt = open(SCORECARD, encoding="utf-8").read()
    rows = []
    for line in txt.splitlines():
        m = re.match(r"\|\s*(\d{2})-(\d{2})", line)
        if not m: continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0]='' [1]=date [2]=stock [3]=decision [4]=price [5]=PE [6]=大戶%
        if len(cells) < 5: continue
        mm, dd = int(m.group(1)), int(m.group(2))
        date = datetime.date(YEAR, mm, dd)
        stock = cells[2]; decision = cells[3]; price = parse_price(cells[4])
        chip = None
        if len(cells) > 6:
            cm = re.search(r"(\d+\.?\d*)\s*%", cells[6])
            if cm: chip = float(cm.group(1))
        rows.append(dict(date=date, stock=stock, decision=decision, price=price,
                         chip=chip, bucket=classify(decision)))

    # benchmark 序列 (一次抓)
    benches = {}
    for b, sym in [("TWII","^TWII"),("TWOII","^TWOII"),("US","SPY")]:
        h = yf.Ticker(sym).history(start="2026-07-25", auto_adjust=True)
        benches[b] = h["Close"] if len(h) else None
    # 規則: ^TWOII 抓不到或太稀疏 → 退回 ^TWII (記分卡表頭)
    if benches.get("TWOII") is None or len(benches["TWOII"]) < 5:
        benches["TWOII"] = benches["TWII"]

    results = []
    for r in rows:
        if r["bucket"] == "SKIP" or r["price"] is None:
            continue
        ticker, market = map_ticker(r["stock"])
        if not ticker:
            continue
        try:
            ser, bench = load_series(ticker, market)
        except Exception as e:
            ser = None
        if ser is None or len(ser) == 0:
            results.append({**r, "ticker": ticker, "err": "no_price"}); continue
        p_now = float(ser.iloc[-1])
        p_dec = r["price"]  # 用記分卡當時價 (決策價) 當基準, 更誠實
        stock_ret = (p_now/p_dec - 1)*100
        bser = benches.get(bench)
        b_dec = asof(bser, r["date"]) if bser is not None else None
        b_now = float(bser.iloc[-1]) if bser is not None else None
        bench_ret = (b_now/b_dec - 1)*100 if (b_dec and b_now) else None
        alpha = (stock_ret - bench_ret) if bench_ret is not None else None
        results.append({**r, "ticker": ticker, "market": market, "bench": bench,
                        "p_now": p_now, "stock_ret": stock_ret, "bench_ret": bench_ret, "alpha": alpha})

    # ===== 報表 =====
    print("="*90)
    print(f"決策記分卡結算  執行日 {datetime.date.today()}  (判對錯用 alpha=個股−對應大盤)")
    print("="*90)
    ok = [r for r in results if r.get("alpha") is not None]
    print(f"解析 {len(rows)} 筆 / 可算 alpha {len(ok)} 筆 / 失敗 {len(results)-len(ok)} 筆\n")

    def show(bucket, title):
        sub = [r for r in ok if r["bucket"]==bucket]
        if not sub: return
        sub.sort(key=lambda r: -(r["alpha"]))
        print(f"\n----- {title} ({len(sub)}筆) -----")
        print(f"{'日期':6}{'標的':16}{'決策價':>8}{'現價':>8}{'個股%':>7}{'大盤%':>7}{'alpha':>7}{'大戶%':>6}")
        for r in sub:
            nm = r["stock"][:15]
            chip = f'{r["chip"]:.0f}' if r["chip"] else '-'
            print(f'{r["date"].strftime("%m-%d"):6}{nm:16}{r["price"]:>8.1f}{r["p_now"]:>8.1f}{r["stock_ret"]:>+7.1f}{r["bench_ret"]:>+7.1f}{r["alpha"]:>+7.1f}{chip:>6}')

    show("ADD", "✅ 加 (實際進場) — alpha>0=贏大盤=對")
    show("PASS", "❌ pass (剔除/避開) — alpha<0=它輸大盤=pass對")
    show("WATCH", "🔸 觀察/彩券/翻案 (未進場·追蹤讀對沒)")

    # 命中率
    print("\n" + "="*90)
    print("命中率統計")
    print("="*90)
    add = [r for r in ok if r["bucket"]=="ADD"]
    if add:
        hit = sum(1 for r in add if r["alpha"]>0)
        print(f"加的命中率 (alpha>0): {hit}/{len(add)} = {hit/len(add)*100:.0f}%  | 平均alpha {sum(r['alpha'] for r in add)/len(add):+.1f}pp")
    pas = [r for r in ok if r["bucket"]=="PASS"]
    if pas:
        good = sum(1 for r in pas if r["alpha"]<0)
        print(f"pass正確率 (alpha<0=它輸大盤): {good}/{len(pas)} = {good/len(pas)*100:.0f}%  | 平均alpha {sum(r['alpha'] for r in pas)/len(pas):+.1f}pp")
    wat = [r for r in ok if r["bucket"]=="WATCH"]
    if wat:
        up = sum(1 for r in wat if r["alpha"]>0)
        print(f"觀察讀對率 (alpha>0): {up}/{len(wat)} = {up/len(wat)*100:.0f}%  | 平均alpha {sum(r['alpha'] for r in wat)/len(wat):+.1f}pp")

    # 大戶籌碼驗證
    print("\n" + "="*90)
    print("💰 大戶籌碼訊號驗證 (決策時大戶% 高 vs 低, 比後續 alpha)")
    print("="*90)
    chipped = [r for r in ok if r["chip"] is not None]
    if len(chipped) >= 4:
        hi = [r for r in chipped if r["chip"]>=65]
        lo = [r for r in chipped if r["chip"]<65]
        if hi and lo:
            ah = sum(r["alpha"] for r in hi)/len(hi)
            al = sum(r["alpha"] for r in lo)/len(lo)
            print(f"大戶≥65% ({len(hi)}筆) 平均alpha {ah:+.1f}pp   vs   大戶<65% ({len(lo)}筆) 平均alpha {al:+.1f}pp")
            print(f"→ {'高籌碼組較強·訊號有預測力(暫)' if ah>al else '高籌碼組沒較強·籌碼訊號本季無效(暫)'}")
    else:
        print(f"有大戶%的樣本僅 {len(chipped)} 筆, 不足結論")

    # 失敗清單
    fails = [r for r in results if r.get("alpha") is None and r["bucket"]!="SKIP"]
    if fails:
        print("\n(未能算 alpha, 需手動: " + ", ".join(f'{r["stock"][:10]}' for r in fails[:20]) + ")")

if __name__ == "__main__":
    main()
