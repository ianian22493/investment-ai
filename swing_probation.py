"""
swing_probation.py — 波段 C1 的「試用期止血開關」
====================================================
C1 是 2026-08-07 依回測重建的波段引擎，回測 edge 很薄（tail-driven），**上線後
還沒有實測驗證**。這支模組讀 alpha.db，算「C1 上線後的 live alpha」，到樣本門檻
仍為負就觸發止血——後端強制暫停開新倉（副菜別當主力、別默默失血）。

判定規則（白紙黑字）：
  • 累積 < MIN_SAMPLE 筆已結案實單 → 'probation'（試用中，仍可交易，只顯示進度）
  • 累積 ≥ MIN_SAMPLE 且 live alpha < 0 → 'suspended'（暫停開新倉，需人工複盤重啟）
  • 累積 ≥ MIN_SAMPLE 且 live alpha ≥ 0 → 'active_pass'（通過試用期）

judged by alpha（贏大盤多少），不是絕對報酬——多頭人人漲，輸大盤=假贏。
重啟＝人工複盤 backtest_swing.py 後，把 C1_LAUNCH_DATE 往後推（重開試用期）或收掉。
"""
import os
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "alpha.db")

C1_LAUNCH_DATE = "2026-08-07"   # C1 重建上線日（commit a677724）
MIN_SAMPLE = 10                 # 至少這麼多筆已結案實單才做判定（少於此=噪音，不暫停）
REVIEW_WEEKS = 8                # 超過這麼久仍湊不滿樣本＝提醒人工複盤（不自動暫停）


def status() -> dict:
    """C1 上線後實測體檢 + 是否該止血。回傳給 pipeline gate 與 pick 頁徽章。"""
    out = {
        "launch": C1_LAUNCH_DATE, "n": 0, "mean_alpha": None, "mean_return": None,
        "win_rate": None, "weeks_since": 0.0, "status": "probation",
        "badge": "", "reason": "",
    }
    try:
        d0 = datetime.strptime(C1_LAUNCH_DATE, "%Y-%m-%d").date()
        out["weeks_since"] = round((datetime.now().date() - d0).days / 7.0, 1)
    except Exception:
        pass

    if not os.path.exists(DB):
        out["badge"] = "無資料"
        out["reason"] = "alpha.db 不存在。"
        return out

    try:
        conn = sqlite3.connect(DB)
        rows = conn.execute(
            "SELECT return_pct, alpha_pct FROM picks "
            "WHERE date >= ? AND resolved = 1 AND stock_code != 'NONE' "
            "AND alpha_pct IS NOT NULL AND (exit_reason IS NULL OR exit_reason != 'not_filled')",
            (C1_LAUNCH_DATE,),
        ).fetchall()
        conn.close()
    except Exception as e:  # noqa: BLE001
        out["badge"] = "DB錯誤"
        out["reason"] = str(e)
        return out

    returns = [r[0] for r in rows if r[0] is not None]
    alphas = [r[1] for r in rows if r[1] is not None]
    n = len(alphas)
    out["n"] = n

    if n == 0:
        out["status"] = "probation"
        out["badge"] = f"試用期 0/{MIN_SAMPLE}"
        out["reason"] = (f"C1 上線 {out['weeks_since']} 週、尚無結案實單。累積中——"
                         "先讓它跑、別當主力。")
        return out

    mean_alpha = sum(alphas) / n
    mean_return = sum(returns) / len(returns) if returns else None
    win_rate = 100 * sum(1 for r in returns if r > 0) / len(returns) if returns else None
    out["mean_alpha"] = round(mean_alpha, 2)
    out["mean_return"] = round(mean_return, 2) if mean_return is not None else None
    out["win_rate"] = round(win_rate) if win_rate is not None else None

    if n < MIN_SAMPLE:
        out["status"] = "probation"
        out["badge"] = f"試用期 {n}/{MIN_SAMPLE}｜alpha {mean_alpha:+.1f}%"
        tail = ""
        if out["weeks_since"] >= REVIEW_WEEKS:
            tail = f" 已逾 {REVIEW_WEEKS} 週仍未滿樣本，考慮人工複盤。"
        out["reason"] = (f"C1 上線 {out['weeks_since']} 週、{n} 筆結案，live alpha "
                         f"{mean_alpha:+.2f}%（樣本未滿 {MIN_SAMPLE}，暫不判定）。{tail}")
        return out

    if mean_alpha < 0:
        out["status"] = "suspended"
        out["badge"] = f"⛔ 暫停｜{n}筆 alpha {mean_alpha:+.1f}%"
        out["reason"] = (f"C1 上線後 {n} 筆結案 live alpha {mean_alpha:+.2f}% < 0 → "
                         "觸發止血規則，強制暫停開新倉。重啟需人工複盤 backtest_swing.py，"
                         "再把 C1_LAUNCH_DATE 往後推（重開試用期）或收掉波段。")
    else:
        out["status"] = "active_pass"
        out["badge"] = f"✅ 通過｜{n}筆 alpha {mean_alpha:+.1f}%"
        out["reason"] = (f"C1 上線後 {n} 筆結案 live alpha {mean_alpha:+.2f}% ≥ 0 → 通過試用期，"
                         "維持零用錢副菜定位、繼續監測。")
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    s = status()
    print("=== 波段 C1 試用期體檢 ===")
    for k, v in s.items():
        print(f"  {k}: {v}")
