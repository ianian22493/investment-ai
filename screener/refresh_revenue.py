"""
refresh_revenue.py — 補抓「當月月營收」到 history（寶藏面板即時化）

monthly_screener 每月 11 日跑一次，但當月營收批次源常在 11 日還沒彙整完
（如 8/11 跑時 7 月資料還沒 aggregated），導致 rev_momentum/寶藏面板讀到的
月營收落後整整一個月、顯示舊數字。

這支給每日 pipeline 跑：**只在「history 最新月落後於今天該有的月」時**才去
TWSE/TPEx OpenAPI 補抓當月並寫檔。月營收一個月才變一次，所以真正的 fetch
每月只發生一次（新資料出來後首日）；其餘日子只做一次目錄比對、不 fetch。
只補 rev_YYYMM.json（不重算 track 名單，那是 monthly_screener 的事）。

本機測試用 `--insecure` 繞過 Windows SSL；CI(ubuntu) 不需要。非致命：抓取
失敗就維持舊資料、下次再試（pipeline 該步 continue-on-error）。
"""
from __future__ import annotations
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.join(os.path.dirname(HERE), "data", "screener", "history")
TW = timezone(timedelta(hours=8))


def _expected_latest_roc() -> int:
    """今天該有的最新月營收（民國 YYYMM）。月營收 M 於 M+1 月 10 日前公布，
    故過 11 號 → 上個月已有；否則上上個月。"""
    now = datetime.now(TW)
    back = 1 if now.day >= 11 else 2
    y, m = now.year, now.month - back
    while m <= 0:
        m += 12
        y -= 1
    return (y - 1911) * 100 + m


def _latest_saved_roc() -> int:
    if not os.path.isdir(HIST_DIR):
        return 0
    months = [int(mo.group(1)) for f in os.listdir(HIST_DIR)
              if (mo := re.match(r"^rev_(\d{5})\.json$", f))]
    return max(months) if months else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    expected = _expected_latest_roc()
    saved = _latest_saved_roc()
    print(f"[refresh_revenue] 該有最新月={expected} · history 最新={saved}")
    if saved >= expected:
        print("  已是最新，不需補抓。")
        return 0
    print(f"  批次落後（{saved} < {expected}），嘗試補抓當月…")
    sys.path.insert(0, HERE)
    import monthly_screener as ms
    if "--insecure" in sys.argv:
        ms.INSECURE = True
    try:
        data_month, rev = ms.load_revenue()
    except Exception as e:  # noqa: BLE001 — 非致命
        print(f"  抓取失敗（{e}）——維持舊資料，下次再試。")
        return 0
    api_roc = int(data_month)
    if api_roc <= saved:
        print(f"  API 仍為 {api_roc}（尚未更新到 {expected}）——維持舊資料。")
        return 0
    os.makedirs(HIST_DIR, exist_ok=True)
    out = os.path.join(HIST_DIR, f"rev_{api_roc}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rev, f, ensure_ascii=False)
    print(f"  ✓ 補抓成功：寫入 rev_{api_roc}.json（{len(rev)} 家）——寶藏面板即時化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
