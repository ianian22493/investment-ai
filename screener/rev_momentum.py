"""
rev_momentum.py — 寶藏觀察名單的「月營收動能分類器」
=====================================================
薄層：資料一律取自 `fundamental_feed.get_fundamentals()`（單一真相源，與波段候選
的 fundamental_line 同源，不再各讀各的、不會數字打架），這裡只加「趨勢分類 + 訊號
上色 + 一句人讀摘要」，給 watchlist_watcher 每日附掛到 alerts.json → pick 頁面板。

收稅口/雙擊論述真正該盯的是「營收連續加速 + 高毛利」，不是價格——把價格哨兵升級
成論述追蹤器。無網路呼叫、不吃 quota。

signal 三態：
  confirm(🟢 論述兌現)：YoY 加速 / 長期高成長 / 高檔穩健
  warn   (🔴 論述亮紅)：YoY 轉負
  caution(🟡 留意)     ：still 正但明顯減速
  neutral              ：持平／資料不足
"""
import os
import sys

# 單一真相源：與 tw_daily_pick 的 fundamental_line 同一份資料
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fundamental_feed


def _roc_to_label(roc: str) -> str:
    """'11506' → '6月'（西元月，供人讀）。"""
    try:
        return f"{int(str(roc)[-2:])}月"
    except Exception:
        return str(roc)


def _classify(latest, series, streak):
    """依 YoY 最新值 / 序列加速度 / 連續正成長月數，判 trend + signal。"""
    prior3 = series[-4:-1] if len(series) >= 2 else []
    prior_avg = sum(prior3) / len(prior3) if prior3 else latest
    accel = latest - prior_avg
    if latest < 0:
        return "⚠️轉負", "warn"
    if accel >= 8:
        return "🔥加速", "confirm"
    if latest >= 20 and streak >= 4 and accel > -10:
        return "高檔穩健", "confirm"
    if streak >= 12 and accel > -8:
        return "穩定高成長", "confirm"
    if accel <= -10:
        return "🔻減速", "caution"
    return "持平", "neutral"


def rev_momentum(codes: list[str]) -> dict:
    """對每個股票代號算月營收動能 + 毛利率。回傳 code -> dict（無資料的 code 略過）。

    dict：month/yoy/cum_yoy/streak/gm/eps/trend/signal/brief/as_of
    """
    funds = fundamental_feed.get_fundamentals([str(c) for c in codes], streak_months=15)
    out = {}
    for code, f in funds.items():
        series = f.get("yoy_series") or []
        latest = f.get("rev_yoy")
        if latest is None or not series:
            continue
        streak = f.get("yoy_streak") or 0
        trend, signal = _classify(latest, series, streak)

        gm = f.get("gm")
        gmq = f.get("gm_quarter")
        cum = f.get("cum_yoy")
        month = _roc_to_label(f.get("data_month"))
        streak_txt = f"、連{streak}月正" if streak >= 3 else ""
        cum_txt = f"、累計{cum:+.0f}%" if isinstance(cum, (int, float)) else ""
        gm_txt = ""
        if isinstance(gm, (int, float)):
            gm_txt = f"、毛利{gm:.0f}%" + ("🏰" if gm >= 50 else "") + (f"·{gmq}" if gmq else "")
        brief = f"{month}營收 YoY {latest:+.0f}%（{trend}{streak_txt}{cum_txt}{gm_txt}）"

        out[code] = {
            "month": month, "yoy": round(latest, 1),
            "cum_yoy": round(cum, 1) if isinstance(cum, (int, float)) else None,
            "streak": streak, "gm": gm, "gm_quarter": gmq, "eps": f.get("eps"),
            "trend": trend, "signal": signal, "brief": brief,
            "as_of": f.get("data_month"),
        }
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    wl = ["4571", "5306", "3546", "6534", "2755"]
    res = rev_momentum(wl)
    print(f"[rev_momentum] 資料月 as_of ROC {list(res.values())[0]['as_of'] if res else '—'}（單一源＝fundamental_feed）")
    for c in wl:
        r = res.get(c)
        print(f"  {c}: {r['brief']} | signal={r['signal']}" if r else f"  {c}: (無資料)")
