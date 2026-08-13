"""
attribution.py — 候選歸因 log（驗證「寶藏雷達有沒有選股能力」的地基）
========================================================================
外部 review(2026-08) 最尖銳的一點：光記「加了什麼」無法回答「雷達是在發現 alpha、
還是在漂亮地描述已被市場發現的股票」。要回答，必須存**決策當時的 point-in-time
快照，含 pass/落選的**——之後才能算「加的檔 vs pass 的檔」的前瞻 alpha ＝ 選股力。

設計原則（point-in-time、無前視）：
- 每筆記錄存「決策當日的價格與屬性」（price_at/pe/big…），這是錨。
- 前瞻報酬在**讀取時**用現價算（record→now），不寫死未來資料。
- **決策分 加/pass/觀察**——關鍵是比「加」有沒有贏「pass」（不只看加的絕對報酬）。
- 樣本要幾十上百筆、跨數季才有統計意義；現在只是「開始存」，不是「開始驗證」。

資料：data/attribution/snapshots.jsonl（append-only，每行一筆候選×一次決策事件）。
"""
import json
import os
from datetime import datetime

import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
ATTR_DIR = os.path.join(HERE, "data", "attribution")
SNAP = os.path.join(ATTR_DIR, "snapshots.jsonl")


def log(records: list[dict], source: str, date: str | None = None) -> int:
    """append 候選快照。records 每筆至少含 code/name/decision/price_at；可含 pe/big/rev_yoy/gm/note。
    decision ∈ {加, pass, 觀察, universe}。依 (date,source,code) 去重。回傳新增筆數。"""
    date = date or datetime.now().strftime("%Y-%m-%d")
    os.makedirs(ATTR_DIR, exist_ok=True)
    seen = set()
    if os.path.exists(SNAP):
        for ln in open(SNAP, encoding="utf-8"):
            try:
                r = json.loads(ln)
                seen.add((r.get("date"), r.get("source"), str(r.get("code"))))
            except Exception:  # noqa: BLE001
                continue
    n = 0
    with open(SNAP, "a", encoding="utf-8") as f:
        for rec in records:
            key = (date, source, str(rec.get("code")))
            if key in seen:
                continue
            row = {"date": date, "source": source, "code": str(rec.get("code")),
                   "name": rec.get("name", ""), "decision": rec.get("decision", ""),
                   "price_at": rec.get("price_at"), "pe": rec.get("pe"),
                   "big": rec.get("big"), "rev_yoy": rec.get("rev_yoy"),
                   "gm": rec.get("gm"), "note": rec.get("note", "")}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            seen.add(key)
            n += 1
    return n


def _fetch_px(code):
    for s in (".TW", ".TWO"):
        try:
            h = yf.Ticker(code + s).history(period="5d")["Close"].dropna()
            if len(h):
                return float(h.iloc[-1]), s
        except Exception:  # noqa: BLE001
            continue
    return None, None


def outcomes(min_days: int = 30) -> dict:
    """讀 snapshots，用現價算每筆前瞻報酬 + alpha（依市場選 benchmark），
    依 decision 聚合。回答：加的檔前瞻 alpha 有沒有贏過 pass 的檔？（＝選股力）"""
    if not os.path.exists(SNAP):
        return {}
    rows = [json.loads(ln) for ln in open(SNAP, encoding="utf-8") if ln.strip()]
    # benchmark 現值 + 決策日值（快取）
    bench = {}
    for b in ("^TWII", "^TWOII"):
        try:
            bench[b] = yf.Ticker(b).history(period="6mo")["Close"].dropna()
        except Exception:  # noqa: BLE001
            bench[b] = None
    today = datetime.now().date()
    agg = {}
    for r in rows:
        px_now, suf = _fetch_px(r["code"])
        pa = r.get("price_at")
        if not px_now or not pa:
            continue
        try:
            d0 = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except Exception:  # noqa: BLE001
            continue
        held = (today - d0).days
        ret = (px_now / pa - 1) * 100
        # benchmark 同期報酬（上櫃用櫃買）
        bkey = "^TWOII" if suf == ".TWO" else "^TWII"
        bser = bench.get(bkey)
        if bser is None or not len(bser):
            bser = bench.get("^TWII")
        alpha = None
        if bser is not None and len(bser):
            try:
                b0 = float(bser[bser.index.date <= d0].iloc[-1]); b1 = float(bser.iloc[-1])
                alpha = ret - (b1 / b0 - 1) * 100
            except Exception:  # noqa: BLE001
                pass
        dec = r.get("decision", "?")
        agg.setdefault(dec, []).append({"code": r["code"], "held": held, "ret": ret, "alpha": alpha})
    # 聚合
    out = {}
    for dec, items in agg.items():
        mature = [x for x in items if x["held"] >= min_days and x["alpha"] is not None]
        alphas = [x["alpha"] for x in mature]
        out[dec] = {"n": len(items), "n_mature": len(mature),
                    "mean_alpha": round(sum(alphas) / len(alphas), 2) if alphas else None,
                    "mean_ret": round(sum(x["ret"] for x in items) / len(items), 2)}
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    o = outcomes()
    print(f"=== Candidate Attribution 成績（前瞻，需 ≥30天才成熟）===")
    tot = sum(v["n"] for v in o.values()) if o else 0
    print(f"總記錄: {tot} 筆")
    for dec, v in sorted(o.items()):
        ma = f"{v['mean_alpha']:+.1f}%" if v["mean_alpha"] is not None else "未成熟"
        print(f"  {dec:8} n={v['n']:<3} 成熟{v['n_mature']:<3} 平均報酬{v['mean_ret']:+5.1f}% 前瞻alpha {ma}")
    print("→ 關鍵問題(數季後)：『加』的前瞻alpha 有沒有明顯贏過『pass』？= 選股力")
