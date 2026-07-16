"""
backfill_history.py — 一次性回填篩選器歷史快照
================================================
月營收：公開資訊觀測站靜態檔（上市/上櫃 × 國內/KY），回填近 12 個月
季損益：MOPS ajax_t163sb04（上市/上櫃），回填近 2 季（毛利率趨勢用）

跑一次即可；之後由 monthly_screener.py 每月自然累積。
用法：python screener/backfill_history.py [--insecure] [--months 12]
"""

import json
import re
import ssl
import sys
import time
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST_DIR = ROOT / "data" / "screener" / "history"

INSECURE = "--insecure" in sys.argv
N_MONTHS = 12
if "--months" in sys.argv:
    N_MONTHS = int(sys.argv[sys.argv.index("--months") + 1])

CTX = None
if INSECURE:
    CTX = ssl.create_default_context()
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE


def http_get(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"GET failed: {url} — {last}")


def http_post(url, data, retries=3):
    body = urllib.parse.urlencode(data).encode()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"User-Agent": "Mozilla/5.0",
                         "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"POST failed: {url} — {last}")


class TableGrab(HTMLParser):
    """把 HTML 中所有 <tr> 的 <td>/<th> 文字抓成 list[list[str]]。"""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._in_cell = [], None, [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell, self._cell = True, []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            if self._row is not None:
                self._row.append("".join(self._cell).strip())
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def parse_rows(html_bytes):
    text = html_bytes.decode("cp950", errors="replace")
    p = TableGrab()
    p.feed(text)
    return p.rows


def to_f(v):
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s not in ("", "-", "N/A") else None
    except (ValueError, TypeError):
        return None


# ── 月營收回填 ───────────────────────────────────────────────────────────────

def month_seq_before(roc_month, n):
    """回傳 roc_month（如 '11506'）之前的 n 個月份字串，新到舊。"""
    y, m = int(roc_month[:3]), int(roc_month[3:])
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return out


def backfill_month(roc_ym):
    y, m = int(roc_ym[:3]), int(roc_ym[3:])
    merged = {}
    for market, seg in (("twse", "sii"), ("tpex", "otc")):
        for suffix in ("0", "1"):  # 0=國內 1=KY
            # MOPS 2025 改版後，舊版靜態檔搬到 mopsov 網域（mops.twse.com.tw 會 404）
            url = f"https://mopsov.twse.com.tw/nas/t21/{seg}/t21sc03_{y}_{m}_{suffix}.html"
            try:
                rows = parse_rows(http_get(url))
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] {roc_ym} {seg}_{suffix}: {e}")
                continue
            for r in rows:
                # 公司列：code, name, 當月營收, 上月營收, 去年當月營收, 上月比較%, 去年同月%…
                if len(r) < 7 or not re.fullmatch(r"\d{4,6}", r[0] or ""):
                    continue
                rev, rev_ly = to_f(r[2]), to_f(r[4])
                cum, cum_ly = (to_f(r[7]), to_f(r[8])) if len(r) > 8 else (None, None)
                yoy = round((rev / rev_ly - 1) * 100, 2) if rev and rev_ly else None
                cum_yoy = round((cum / cum_ly - 1) * 100, 2) if cum and cum_ly else None
                mom_base = to_f(r[3])
                merged[r[0]] = {
                    "name": r[1], "industry": "", "market": market,
                    "rev": rev, "yoy": yoy,
                    "mom": round((rev / mom_base - 1) * 100, 2) if rev and mom_base else None,
                    "cum_yoy": cum_yoy,
                }
            time.sleep(1.5)
    return merged


# ── 季損益回填（毛利率/EPS） ─────────────────────────────────────────────────

def quarter_seq_before(tag, n):
    """tag 如 '115Q1' → 之前 n 季，新到舊。"""
    y, s = int(tag.split("Q")[0]), int(tag.split("Q")[1])
    out = []
    for _ in range(n):
        s -= 1
        if s == 0:
            y, s = y - 1, 4
        out.append(f"{y}Q{s}")
    return out


def backfill_quarter(tag):
    y, s = tag.split("Q")
    merged = {}
    for typek in ("sii", "otc"):
        try:
            html = http_post("https://mopsov.twse.com.tw/mops/web/ajax_t163sb04", {
                "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
                "isQuery": "Y", "TYPEK": typek, "year": y, "season": f"{int(s):02d}",
            })
            rows = parse_rows(html)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] {tag} {typek}: {e}")
            continue
        header_idx = {}
        for r in rows:
            joined = "".join(r)
            if "公司代號" in joined and "營業收入" in joined:
                header_idx = {c: i for i, c in enumerate(r)}
                continue
            if not header_idx or len(r) < 4:
                continue
            code_i = next((i for c, i in header_idx.items() if "代號" in c), 0)
            code = (r[code_i] or "").strip()
            if not re.fullmatch(r"\d{4,6}", code):
                continue
            def col(*needles):
                for c, i in header_idx.items():
                    if all(n in c for n in needles) and i < len(r):
                        return to_f(r[i])
                return None
            revenue = col("營業收入")
            gross = col("毛利", "淨額") or col("毛利")
            eps = col("每股盈餘")
            gm = round(gross / revenue * 100, 2) if revenue and gross is not None else None
            merged[code] = {"revenue": revenue, "gm": gm, "eps": eps}
        time.sleep(3)
    return merged


def main():
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    # 以現有最新快照定位「現在」
    rev_files = sorted(HIST_DIR.glob("rev_*.json"), reverse=True)
    fin_files = sorted(HIST_DIR.glob("fin_*.json"), reverse=True)
    if not rev_files or not fin_files:
        print("先跑 monthly_screener.py 產生當期快照，再回填。")
        sys.exit(1)
    cur_month = rev_files[0].stem.replace("rev_", "")
    cur_q = fin_files[0].stem.replace("fin_", "")

    print(f"回填 {N_MONTHS} 個月營收（{cur_month} 之前）…")
    for ym in month_seq_before(cur_month, N_MONTHS):
        out = HIST_DIR / f"rev_{ym}.json"
        if out.exists():
            print(f"  {ym}: 已存在，略過")
            continue
        data = backfill_month(ym)
        if data:
            out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            print(f"  {ym}: {len(data)} 家 OK")
        else:
            print(f"  {ym}: 無資料 EMPTY")

    print(f"回填 2 季損益（{cur_q} 之前）…")
    for q in quarter_seq_before(cur_q, 2):
        out = HIST_DIR / f"fin_{q}.json"
        if out.exists():
            print(f"  {q}: 已存在，略過")
            continue
        data = backfill_quarter(q)
        if data:
            out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            print(f"  {q}: {len(data)} 家 OK")
        else:
            print(f"  {q}: 無資料 EMPTY")

    print("回填完成。重新執行 monthly_screener.py 以升級 full mode。")


if __name__ == "__main__":
    main()
