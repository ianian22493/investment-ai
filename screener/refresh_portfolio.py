"""
refresh_portfolio.py — 帳面價格刷新（防「幽靈持倉」）
=====================================================================
問題：portfolio.json 的 value/pnl 是靜態快照，會隨股價漂移失真（2026-08-28
發現群聯顯示 -32% 實際 -2.7%）。整套系統（審判日/gap/規格檢查）都讀這個檔，
它過期＝系統在用幽靈價推理。

本工具：用 yfinance 即時價重算每檔的 value/pnl/pnl_pct 與彙總，**只動價格衍生
欄位**——shares/cost/_note/_comment（＝你的交易紀錄）一律不碰。每日隨 daily
pipeline 自動跑一次，讓下游一律讀新鮮價。

★分工鐵律：
  - shares/cost = 交易紀錄，只有使用者回報成交後由研究員手動改（鐵律#6 不變）。
  - value/pnl   = 純機械（價×股數），本工具自動刷新，不需人工。
  - _updated    = 最後一次「交易」更新日（staleness 指標，本工具不動）。
  - _price_refreshed = 最後一次「價格」刷新日（本工具寫）。

用法：cd /c/tmp/investment-ai && python screener/refresh_portfolio.py
CI：daily_analysis.yml 一步（continue-on-error，抓價失敗保留舊值、不擋 pipeline）。
"""
import datetime as dt
import json
import os
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PF = os.path.join(ROOT, "data", "portfolio.json")


def _last_close(h):
    """取最後一個非 NaN 收盤；沒有回 None。"""
    if h is None or h.empty:
        return None
    ser = h["Close"].squeeze().dropna()
    if len(ser) == 0:
        return None
    v = float(ser.iloc[-1])
    return v if v == v and v > 0 else None   # v==v 擋 NaN


def _price_tw(code: str):
    """台股：.TW（上市）→ .TWO（上櫃）fallback。抓不到回 None。"""
    import yfinance as yf
    for suf in (".TW", ".TWO"):
        try:
            v = _last_close(yf.download(code + suf, period="5d", progress=False, auto_adjust=True))
            if v is not None:
                return v
        except Exception:  # noqa: BLE001
            continue
    return None


def _price_us(ticker: str):
    import yfinance as yf
    try:
        return _last_close(yf.download(ticker, period="5d", progress=False, auto_adjust=True))
    except Exception:  # noqa: BLE001
        return None


def refresh() -> dict:
    d = json.load(open(PF, encoding="utf-8"))
    today = dt.datetime.now().strftime("%Y-%m-%d")
    report = {"tw": [], "us": [], "tw_fail": [], "us_fail": []}

    # ── 台股：刷新 value/pnl/pnl_pct（cost/shares/_note 不動）──
    tw_val = 0
    for s in d.get("tw_stocks", []):
        price = _price_tw(s["code"])
        if price is None:
            tw_val += s.get("value", 0)
            report["tw_fail"].append(s["code"])
            continue
        new_val = round(price * s["shares"])
        report["tw"].append((s["code"], s["name"], s.get("pnl_pct"),
                             round((new_val - s["cost"]) / s["cost"] * 100, 2) if s.get("cost") else None))
        s["value"] = new_val
        s["pnl"] = new_val - s["cost"]
        s["pnl_pct"] = round(s["pnl"] / s["cost"] * 100, 2) if s.get("cost") else 0.0
        tw_val += new_val
    if "tw_summary" in d and d.get("tw_stocks"):
        tc = sum(s["cost"] for s in d["tw_stocks"])
        d["tw_summary"]["total_value"] = tw_val
        d["tw_summary"]["total_cost"] = tc
        d["tw_summary"]["total_pnl"] = tw_val - tc
        d["tw_summary"]["total_pnl_pct"] = round((tw_val - tc) / tc * 100, 2) if tc else 0.0

    # ── 美股：加/刷新 current_price_usd/value_usd/pnl_usd/pnl_pct（avg_cost_usd/shares 不動）──
    us_val = us_cost = 0.0
    for s in d.get("us_stocks", []):
        price = _price_us(s["ticker"])
        cost = s["shares"] * s.get("avg_cost_usd", 0)
        us_cost += cost
        if price is None:
            us_val += s.get("value_usd", cost)
            report["us_fail"].append(s["ticker"])
            continue
        val = round(price * s["shares"], 2)
        s["current_price_usd"] = round(price, 2)
        s["value_usd"] = val
        s["pnl_usd"] = round(val - cost, 2)
        s["pnl_pct"] = round((val - cost) / cost * 100, 2) if cost else 0.0
        us_val += val
        report["us"].append((s["ticker"], s["pnl_pct"]))
    if "us_summary" in d and d.get("us_stocks"):
        d["us_summary"]["total_value_usd"] = round(us_val, 2)
        d["us_summary"]["total_cost_usd"] = round(us_cost, 2)
        d["us_summary"]["total_pnl_usd"] = round(us_val - us_cost, 2)
        d["us_summary"]["total_pnl_pct"] = round((us_val - us_cost) / us_cost * 100, 2) if us_cost else 0.0

    d["_price_refreshed"] = today
    json.dump(d, open(PF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return report


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = refresh()
    print(f"[refresh_portfolio] TW updated {len(r['tw'])}, US updated {len(r['us'])}"
          + (f", TW fail {r['tw_fail']}" if r["tw_fail"] else "")
          + (f", US fail {r['us_fail']}" if r["us_fail"] else ""))
    for code, name, old, new in r["tw"]:
        if old is not None and new is not None and abs(new - old) >= 5:
            print(f"  {code} {name}: {old:+.1f}% → {new:+.1f}%")
