"""
backtest_swing.py — 波段選股訊號的回測骨架（C 重建的地基）
============================================================
用 scanner.py 同一套 8 維訊號，跑 2 年歷史、模擬波段交易，算 alpha vs 加權。
目的：回答「這些技術訊號到底有沒有 edge」——是規則對執行爛(可救)，還是根本沒edge(該收)。

交易假設（對齊 live 系統）：訊號日收盤觸發 → 隔日收盤進場 → 停損-8%/目標+15%/最多持有22交易日。
alpha = 個股報酬 − 加權同期報酬（多頭市場人人漲，要看贏大盤多少）。
無前視偏誤：訊號用到 t 日收盤，t+1 進場，t+2 起才判停損/目標。
"""
import sys
import pandas as pd
import yfinance as yf

sys.path.insert(0, ".")
try:
    from scanner import fetch_top_stocks
except Exception:
    fetch_top_stocks = None

STOP, TARGET, MAXHOLD, MIN_SCORE = -0.08, 0.15, 22, 5
FALLBACK = ["2330","2317","2454","2308","2382","2412","2882","2891","3711","2303",
            "2881","2886","1216","2884","2357","3034","2379","3008","2395","2603",
            "2609","2615","3037","3231","2409","6505","1303","1301","1326","2002",
            "2207","2105","9910","2474","4938","3045","2345","3661","3443","6415",
            "3529","5269","6669","3017","2049","1590","2327","8046","2377","6285"]


def universe():
    if fetch_top_stocks:
        try:
            top = fetch_top_stocks()
            codes = [s["code"] for s in top[:120]]
            if len(codes) >= 30:
                return codes
        except Exception as e:
            print("[bt] fetch_top_stocks failed:", e)
    print("[bt] 用 fallback 50 檔大型股")
    return FALLBACK


def sig_frame(df, taiex):
    c, h, low, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    dist = (c - ma20) / ma20 * 100
    high60 = c.rolling(60).max()
    pmax, pmin = c.shift(1).rolling(20).max(), c.shift(1).rolling(20).min()
    rng = (pmax - pmin) / pmin * 100
    v5, v20 = v.rolling(5).mean(), v.rolling(20).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, pd.NA))
    t = taiex.reindex(c.index).ffill()
    rs20 = (c / c.shift(20) * 100 - 100) - (t / t.shift(20) * 100 - 100)
    S = pd.DataFrame(index=c.index)
    S["close"] = c
    S["trend_up"] = (ma20 > ma60) & (ma60 > ma60.shift(10))
    S["above60"] = c > ma60
    S["pullback"] = (dist >= -3) & (dist <= 5)
    S["not_ext"] = dist <= 12
    S["base_break"] = (c >= high60) & (rng < 15)
    S["vol_acc"] = (v5 / v20) >= 1.2
    S["rsi_sw"] = (rsi >= 45) & (rsi <= 70)
    S["rs_strong"] = rs20 >= 3
    S["score"] = (S[["trend_up","above60","pullback","not_ext","base_break","vol_acc","rsi_sw","rs_strong"]]
                  .fillna(False).astype(int).sum(axis=1))
    return S


def main():
    codes = universe()
    tickers = [f"{c}.TW" for c in codes] + ["^TWII"]
    print(f"[bt] 下載 {len(codes)} 檔 + 加權 2 年歷史...")
    raw = yf.download(tickers, period="2y", group_by="ticker", auto_adjust=True,
                      progress=False, threads=True)
    taiex = raw["^TWII"]["Close"].dropna()
    taiex_up = (taiex > taiex.rolling(60).mean())  # 體制：加權站上季線=趨勢

    trades = []
    for c in codes:
        tk = f"{c}.TW"
        try:
            df = raw[tk].dropna(subset=["Close"])
        except Exception:
            continue
        if len(df) < 130:
            continue
        S = sig_frame(df, taiex)
        idx = list(S.index)
        n = len(idx)
        j = 65
        while j < n - 2:
            if S["score"].iloc[j] < MIN_SCORE:
                j += 1
                continue
            ei = j + 1  # 隔日進場
            entry = S["close"].iloc[ei]
            if pd.isna(entry) or entry <= 0:
                j += 1
                continue
            stop_p, tgt_p = entry * (1 + STOP), entry * (1 + TARGET)
            xi, reason, xpx = min(ei + MAXHOLD, n - 1), "time", None
            for k in range(ei + 1, min(ei + MAXHOLD + 1, n)):
                if df["Low"].iloc[k] <= stop_p:
                    xi, reason, xpx = k, "stop", stop_p; break
                if df["High"].iloc[k] >= tgt_p:
                    xi, reason, xpx = k, "target", tgt_p; break
            if xpx is None:
                xpx = S["close"].iloc[xi]
            ret = (xpx - entry) / entry * 100
            te = taiex.reindex(S.index).ffill()
            t0, t1 = te.iloc[ei], te.iloc[xi]
            bench = (t1 - t0) / t0 * 100 if t0 and t0 > 0 else 0.0
            r = S.iloc[j]
            trades.append({"score": int(r["score"]), "pullback": bool(r["pullback"]),
                           "base_break": bool(r["base_break"]), "rs_strong": bool(r["rs_strong"]),
                           "regime_up": bool(taiex_up.reindex(S.index).ffill().iloc[ei]),
                           "ret": ret, "alpha": ret - bench, "reason": reason})
            j = xi + 1  # 不重疊

    if not trades:
        print("[bt] 無交易")
        return
    T = pd.DataFrame(trades)

    def stats(d, label):
        if len(d) == 0:
            print(f"  {label:<22} 無樣本"); return
        wr = (d["ret"] > 0).mean() * 100
        print(f"  {label:<22} n={len(d):<4} 勝率{wr:>4.0f}% 平均報酬{d['ret'].mean():+5.2f}% "
              f"平均alpha{d['alpha'].mean():+5.2f}% 中位alpha{d['alpha'].median():+5.2f}%")

    print(f"\n===== 回測結果：{len(T)} 筆交易（訊號 score≥{MIN_SCORE}，停損{int(STOP*100)}%/目標+{int(TARGET*100)}%/持有{MAXHOLD}天）=====")
    stats(T, "全部")
    print("--- 出場原因 ---")
    print("  ", dict(T["reason"].value_counts()))
    print("--- 依 score ---")
    for s in sorted(T["score"].unique()):
        stats(T[T["score"] == s], f"score={s}")
    print("--- 依訊號（單一）---")
    stats(T[T["pullback"]], "含 pullback買點")
    stats(T[T["base_break"]], "含 盤整突破")
    stats(T[T["rs_strong"]], "含 RS強於大盤")
    print("--- 依體制（進場當天）---")
    stats(T[T["regime_up"]], "加權站季線(趨勢)")
    stats(T[~T["regime_up"]], "加權破季線(弱勢)")
    print("--- 最佳組合：突破+RS強+趨勢體制 ---")
    stats(T[T["base_break"] & T["rs_strong"] & T["regime_up"]], "突破+RS+趨勢")
    stats(T[T["pullback"] & T["rs_strong"] & T["regime_up"]], "回檔+RS+趨勢")
    print(f"\n總結：全部平均 alpha {T['alpha'].mean():+.2f}%（>0 才代表訊號有 edge）")


if __name__ == "__main__":
    main()
