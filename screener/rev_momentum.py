"""
rev_momentum.py — 寶藏觀察名單的「月營收動能引擎」
=====================================================
純本地讀 data/screener/history/rev_{ROCYYYMM}.json（全市場 code→營收月快照，
monthly_screener 每月11日更新），對指定股票算「月營收年增趨勢」——收稅口/雙擊
論述真正該盯的訊號（營收連續加速 vs 轉弱），把價格哨兵升級成論述追蹤器。

無網路呼叫、不吃 quota。給 watchlist_watcher 每日附掛到 alerts.json。

signal 三態：
  confirm(🟢 論述兌現)：YoY 加速 或 長期穩定高成長
  warn   (🔴 論述亮紅)：YoY 轉負／明顯衰退
  caution(🟡 留意)     ：still 正但明顯減速
  neutral              ：持平／資料不足
"""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HIST_DIR = os.path.join(ROOT, "data", "screener", "history")


def _roc_to_label(roc: str) -> str:
    """'11506' → '6月'（西元月，供人讀）。"""
    try:
        mm = int(roc[-2:])
        return f"{mm}月"
    except Exception:
        return roc


def _load_snapshots(max_months: int = 6) -> list[tuple[str, dict]]:
    """回傳最近 max_months 份月快照 [(roc, code->rec), ...] 由舊到新。"""
    files = sorted(glob.glob(os.path.join(HIST_DIR, "rev_*.json")))
    files = files[-max_months:]
    out = []
    for f in files:
        roc = os.path.basename(f)[4:-5]  # rev_11506.json → 11506
        try:
            with open(f, encoding="utf-8") as fp:
                out.append((roc, json.load(fp)))
        except Exception:
            continue
    return out


def rev_momentum(codes: list[str], max_months: int = 6) -> dict:
    """對每個股票代號算月營收動能。回傳 code -> dict 或 code 不在資料就略過。

    dict 欄位：
      month      最新資料月（'6月'）
      yoy        最新月營收年增 %
      cum_yoy    累計年增 %
      streak     連續 YoY>0 月數（從最新往回數）
      trend      '🔥加速' / '🔻減速' / '⚠️轉負' / '持平'
      signal     'confirm' / 'caution' / 'warn' / 'neutral'
      brief      一句人讀摘要（給面板顯示）
    """
    snaps = _load_snapshots(max_months)
    if not snaps:
        return {}
    latest_roc = snaps[-1][0]
    out = {}
    for code in codes:
        series = []  # [(roc, yoy)] 由舊到新
        cum_yoy = None
        for roc, mp in snaps:
            rec = mp.get(code)
            if rec and rec.get("yoy") is not None:
                series.append((roc, rec["yoy"]))
                cum_yoy = rec.get("cum_yoy")
        if not series:
            continue
        latest = series[-1][1]
        prior3 = [y for _, y in series[-4:-1]] if len(series) >= 2 else []
        prior_avg = sum(prior3) / len(prior3) if prior3 else latest
        accel = latest - prior_avg
        # streak：從最新往回數連續 yoy>0
        streak = 0
        for _, y in reversed(series):
            if y > 0:
                streak += 1
            else:
                break

        if latest < 0:
            trend, signal = ("⚠️轉負", "warn")
        elif accel >= 8 and latest > 0:
            trend, signal = ("🔥加速", "confirm")
        elif latest >= 20 and streak >= 4 and accel > -10:
            trend, signal = ("高檔穩健", "confirm")
        elif streak >= 12 and latest > 0 and accel > -8:
            trend, signal = ("穩定高成長", "confirm")
        elif accel <= -10 and latest > 0:
            trend, signal = ("🔻減速", "caution")
        else:
            trend, signal = ("持平", "neutral")

        month = _roc_to_label(series[-1][0])
        cum_txt = f"、累計{cum_yoy:+.0f}%" if isinstance(cum_yoy, (int, float)) else ""
        streak_txt = f"、連{streak}月正" if streak >= 3 else ""
        brief = f"{month}營收 YoY {latest:+.0f}%（{trend}{streak_txt}{cum_txt}）"
        out[code] = {
            "month": month,
            "yoy": round(latest, 1),
            "cum_yoy": round(cum_yoy, 1) if isinstance(cum_yoy, (int, float)) else None,
            "streak": streak,
            "trend": trend,
            "signal": signal,
            "brief": brief,
            "as_of": latest_roc,
        }
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    wl = ["4571", "5306", "3546", "6534", "2755"]
    res = rev_momentum(wl)
    print(f"[rev_momentum] 資料月 as_of ROC {list(res.values())[0]['as_of'] if res else '—'}")
    for c in wl:
        r = res.get(c)
        print(f"  {c}: {r['brief']} | signal={r['signal']}" if r else f"  {c}: (無資料)")
