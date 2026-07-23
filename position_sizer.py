"""
部位大小計算（2026-07-24 · 波段系統補洞）

系統原本只給「買哪檔 / 進場區 / 停損 / 目標」，卻沒說「買多少」——
使用者只能憑感覺抓金額。這個模組補上：用**風險基準法**（professional
fixed-fractional sizing）算出建議部位，並揭露觸停損時的實際虧損金額。

核心哲學：每筆交易「觸停損時最多虧交易預算的固定比例」（RISK_CEIL_PCT），
而不是每筆都投一樣多錢。停損寬的部位自動縮小、停損窄的可以大一點，
讓每筆交易的「風險金額」齊平——這才是真正的風控。

同時受「均分預算」上限約束：單一部位不超過 交易預算 ÷ 最大倉位數，
避免一筆吃掉整個池子。

純 Python，不呼叫 LLM。
"""
from __future__ import annotations
import re

# 單筆交易觸停損時，最多虧「交易預算」的百分比。2.5% 是波段的穩健值：
# 交易預算 ~48 萬時，每筆最多虧 ~1.2 萬，連錯 5 筆才傷到 6% 預算。
RISK_CEIL_PCT = 2.5
TW_LOT = 1000   # 台股一張 = 1000 股


def _parse_first_number(text) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    return float(m.group()) if m else None


def _parse_entry_mid(entry_zone) -> float | None:
    """"288.0-293.0" → 290.5（中點）；"288" → 288。取區間中點當成交估價。"""
    nums = re.findall(r"\d+(?:\.\d+)?", str(entry_zone or ""))
    if not nums:
        return None
    vals = [float(n) for n in nums[:2]]
    return sum(vals) / len(vals)


def compute(entry_zone, stop_loss, trading_budget_twd: float, max_positions: int) -> dict:
    """回傳部位大小建議。

    trading_budget_twd: 波段交易總預算（= capital_flow trading% × 現金）。
    max_positions: 最大同時在倉數（決定均分上限）。

    回傳 dict：available / shares / lots_desc / size_twd / size_pct_of_budget /
    stop_distance_pct / max_loss_twd / max_loss_pct_of_budget / binding / note。
    available=False 時帶 reason（缺資料或停損價不合理）。
    """
    entry_mid = _parse_entry_mid(entry_zone)
    stop = _parse_first_number(stop_loss)

    if not entry_mid or not stop:
        return {"available": False, "reason": "缺進場價或停損價，無法計算部位"}
    if trading_budget_twd is None or trading_budget_twd <= 0:
        return {"available": False, "reason": "交易預算為 0（可能倉滿或風控封鎖）"}
    if stop >= entry_mid:
        return {"available": False, "reason": "停損價 ≥ 進場價，風險方向不合理"}
    if max_positions < 1:
        max_positions = 1

    stop_dist_pct = (entry_mid - stop) / entry_mid   # 例 0.08 = 停損距進場 8%

    # 兩個上限取小者
    equal_weight_twd = trading_budget_twd / max_positions            # 均分預算上限
    risk_ceil_twd = (RISK_CEIL_PCT / 100.0) * trading_budget_twd     # 容許的虧損金額
    max_size_by_risk = risk_ceil_twd / stop_dist_pct                 # 反推部位上限

    binding = "風險上限" if max_size_by_risk < equal_weight_twd else "均分預算"
    target_twd = min(equal_weight_twd, max_size_by_risk)

    # 換算股數（無條件捨去到能買的整股）
    shares = int(target_twd // entry_mid)
    if shares <= 0:
        return {"available": False,
                "reason": f"單股價 {entry_mid:.1f} 過高，此預算連 1 股都買不滿"}

    actual_twd = shares * entry_mid
    max_loss_twd = actual_twd * stop_dist_pct
    lots = shares / TW_LOT

    if shares >= TW_LOT:
        lots_desc = f"{lots:.1f} 張" if shares % TW_LOT else f"{shares // TW_LOT} 張"
    else:
        lots_desc = f"{shares} 股（零股）"

    return {
        "available": True,
        "shares": shares,
        "lots_desc": lots_desc,
        "entry_ref": round(entry_mid, 2),
        "size_twd": round(actual_twd),
        "size_pct_of_budget": round(actual_twd / trading_budget_twd * 100, 1),
        "stop_distance_pct": round(stop_dist_pct * 100, 1),
        "max_loss_twd": round(max_loss_twd),
        "max_loss_pct_of_budget": round(max_loss_twd / trading_budget_twd * 100, 2),
        "binding": binding,
        "risk_ceil_pct": RISK_CEIL_PCT,
        "note": (
            f"以進場中點 {entry_mid:.1f} 估，建議 {lots_desc}"
            f"（約 NT$ {round(actual_twd):,}，佔交易預算 {round(actual_twd/trading_budget_twd*100)}%）。"
            f"若觸停損 {stop:.1f}，最多虧約 NT$ {round(max_loss_twd):,}"
            f"（交易預算的 {round(max_loss_twd/trading_budget_twd*100,1)}%）。"
            f"限制因子：{binding}。此為機械式建議，實際下單張數請自行判斷。"
        ),
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    budget = 479269  # 近期實際交易預算
    for ez, sl, mp, label in [
        ("288.0-293.0", "275.0", 3, "力智型（停損 ~5%，窄）"),
        ("208.0-215.0", "198.0", 3, "長榮航太型（停損 ~6%）"),
        ("34.5-35.5", "31.5", 3, "低價金融股（停損 ~10%，寬）"),
        ("7800-8000", "7400", 3, "川湖型（高價股，零股）"),
        ("100", "95", 5, "改 5 倉的效果"),
    ]:
        r = compute(ez, sl, budget, mp)
        print(f"\n[{label}] max_pos={mp}")
        if r["available"]:
            print(f"  {r['note']}")
        else:
            print(f"  無法計算：{r['reason']}")
