"""
smart_money_radar.py — 聰明錢＋訊號雷達（美股寶藏股引擎）
==========================================================
追蹤一批「早期成長/小型股專家」基金的 13F，找出他們本季「新建倉」或「加碼」
的中小型股——美股版的「聰明錢入股 cap table」。資料全來自 SEC EDGAR 公開檔，
無金鑰、無 LLM、不吃 Gemini quota。

哲學（見 寶藏股研究室/CLAUDE.md）：美股的 edge 不是「先看到」（不可能），是
「先相信」——這台雷達不做決定，它把「值得你花信念去研究的 Stage 0/1 候選」
端到面前。多檔聰明錢同時買進的中小型股 = 最強的研究起點。

輸出：
  data/smart_money/history/{fund}_{quarter}.json  各基金各季持股快照（累積）
  data/smart_money/latest.json                    本季 conviction cluster + 各基金新買

節奏：13F 於季末後 45 天內申報（2/中、5/中、8/中、11/中各一批），所以本雷達
排在那之後跑才有新資料。
"""

import json
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "smart_money"
HIST_DIR = OUT_DIR / "history"
UA = {"User-Agent": "yuzu-research ianian22294@gmail.com"}
TW_TZ = timezone(timedelta(hours=8))
CTX = ssl.create_default_context()
if "--insecure" in sys.argv:
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE

# 追蹤名單：早期成長/小型股專家 + 一個逆向者。name_guard 供執行時自我驗證
# （EDGAR 名稱含此字串才採用，避免 CIK 錯抓到別的實體）。
FUNDS = [
    {"name": "Durable Capital",  "cik": "0001798849", "guard": "DURABLE",   "style": "SMID成長(Ellenbogen)"},
    {"name": "Brown Capital",    "cik": "0000885062", "guard": "BROWN CAP", "style": "小型成長"},
    {"name": "Whale Rock",       "cik": "0001387322", "guard": "WHALE ROCK","style": "科技成長"},
    {"name": "Altimeter",        "cik": "0001541617", "guard": "ALTIMETER", "style": "成長(Gerstner)"},
    {"name": "Coatue",           "cik": "0001135730", "guard": "COATUE",    "style": "科技成長"},
    {"name": "Tiger Global",     "cik": "0001167483", "guard": "TIGER GLOBAL","style": "成長"},
    {"name": "Baillie Gifford",  "cik": "0001088875", "guard": "BAILLIE",   "style": "長線成長(早抓TSLA/NVDA)"},
    {"name": "Sands Capital",    "cik": "0001020066", "guard": "SANDS CAP", "style": "成長"},
    {"name": "Lone Pine",        "cik": "0001061165", "guard": "LONE PINE", "style": "成長"},
    {"name": "Scion (Burry)",    "cik": "0001649339", "guard": "SCION",     "style": "逆向/被討厭股"},
]

ADD_THRESHOLD = 0.25   # 股數增加 >25% 才算「加碼」
CLUSTER_MIN = 2        # 至少 N 檔基金同時買進才進 conviction cluster

# 中大型權值股排除清單（不是寶藏，靠 nameOfIssuer 子字串比對）
MEGA_EXCLUDE = {
    "ALPHABET", "AMAZON", "MICROSOFT", "APPLE", "NVIDIA", "META PLATFORMS",
    "TESLA", "BROADCOM", "BERKSHIRE", "ELI LILLY", "JPMORGAN", "VISA",
    "MASTERCARD", "NETFLIX", "ORACLE", "SALESFORCE", "ADOBE", "ADVANCED MICRO",
    "TAIWAN SEMICON", "ASML", "COSTCO", "WALMART", "EXXON", "UNITEDHEALTH",
    "SPDR", "ISHARES", "VANGUARD", "INVESCO QQQ",  # ETF
}


def get(url, raw=False, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45, context=CTX) as r:
                b = r.read()
                return b if raw else b.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def latest_two_13f(cik):
    j = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
    name = j.get("name", "")
    rec = j["filings"]["recent"]
    out = []
    for i, form in enumerate(rec["form"]):
        if form == "13F-HR":
            out.append({"acc": rec["accessionNumber"][i], "date": rec["filingDate"][i],
                        "period": rec.get("reportDate", rec.get("filingDate"))[i]})
        if len(out) == 2:
            break
    return name, out


def parse_infotable(cik, acc):
    accn = acc.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{accn}/"
    idx = json.loads(get(base + "index.json"))
    names = [it["name"] for it in idx["directory"]["item"]]
    # 內容偵測：非 primary_doc 的 xml 檔，含 <infoTable> 者即為持股表。
    # 檔名比對不可靠——各基金用自訂檔名（Q12613FvF.xml / edgbgcomar26.xml…），
    # 純比對檔名會靜默漏抓 → 假的 0 持股。名稱像的先試以省流量。
    xmls = [n for n in names if n.lower().endswith(".xml") and "primary_doc" not in n.lower()]
    xmls.sort(key=lambda n: 0 if ("table" in n.lower() or "info" in n.lower()) else 1)
    blocks, xml = [], ""
    for n in xmls:
        xml = get(base + n)
        blocks = re.findall(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>", xml, re.S)
        if blocks:
            break
        time.sleep(0.2)
    if not blocks:
        return {}
    holdings = {}
    for b in blocks:
        def f(tag):
            m = re.search(rf"<(?:\w+:)?{tag}>(.*?)</(?:\w+:)?{tag}>", b, re.S)
            return m.group(1).strip() if m else None
        cusip = (f("cusip") or "").upper()
        if not cusip:
            continue
        val = int(f("value") or 0)      # 2023 後為完整美元
        sh = int(f("sshPrnamt") or 0)
        h = holdings.setdefault(cusip, {"name": f("nameOfIssuer"), "value": 0, "shares": 0})
        h["value"] += val
        h["shares"] += sh
    return holdings


def is_mega(name):
    u = (name or "").upper()
    return any(m in u for m in MEGA_EXCLUDE)


def main():
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TW_TZ)
    fund_results = []      # 各基金的 new/add
    cluster = {}           # cusip -> {name, funds:[...]}

    for fund in FUNDS:
        try:
            entity, filings = latest_two_13f(fund["cik"])
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {fund['name']}: submissions fail {e}")
            continue
        if fund["guard"] not in entity.upper():
            print(f"[warn] {fund['name']}: guard '{fund['guard']}' not in '{entity}' — skip（CIK 可能錯）")
            continue
        if len(filings) < 2:
            print(f"[info] {fund['name']}: 只有 {len(filings)} 份 13F，無法 diff，跳過")
            continue
        cur = parse_infotable(fund["cik"], filings[0]["acc"]); time.sleep(0.3)
        prev = parse_infotable(fund["cik"], filings[1]["acc"]); time.sleep(0.3)
        cur_total = sum(h["value"] for h in cur.values()) or 1

        new_buys, adds = [], []
        for cusip, h in cur.items():
            if is_mega(h["name"]):
                continue
            pct = h["value"] / cur_total * 100
            row = {"cusip": cusip, "name": h["name"], "value_usd": h["value"],
                   "shares": h["shares"], "pct_of_fund": round(pct, 2)}
            if cusip not in prev:
                row["kind"] = "NEW"
                new_buys.append(row)
            elif prev[cusip]["shares"] and h["shares"] / prev[cusip]["shares"] - 1 >= ADD_THRESHOLD:
                row["kind"] = "ADD"
                row["shares_change_pct"] = round((h["shares"] / prev[cusip]["shares"] - 1) * 100, 1)
                adds.append(row)
            else:
                continue
            c = cluster.setdefault(cusip, {"name": h["name"], "funds": []})
            c["funds"].append({"fund": fund["name"], "kind": row["kind"],
                               "pct_of_fund": row["pct_of_fund"]})

        new_buys.sort(key=lambda r: -r["pct_of_fund"])
        adds.sort(key=lambda r: -r["pct_of_fund"])
        fund_results.append({"fund": fund["name"], "style": fund["style"],
                             "as_of": filings[0]["period"], "filed": filings[0]["date"],
                             "new_buys": new_buys[:12], "adds": adds[:8]})
        (HIST_DIR / f"{fund['cik']}_{filings[0]['period']}.json").write_text(
            json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        print(f"  {fund['name']}: {len(new_buys)} new, {len(adds)} add (as of {filings[0]['period']})")

    clusters = [{"cusip": k, "name": v["name"], "n_funds": len(v["funds"]), "funds": v["funds"]}
                for k, v in cluster.items() if len(v["funds"]) >= CLUSTER_MIN]
    clusters.sort(key=lambda c: -c["n_funds"])

    out = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M %z"),
        "funds_tracked": [f["name"] for f in FUNDS],
        "doc": "conviction_clusters=多檔聰明錢同時新買/加碼的中小型股（研究起點，非買進指令）。"
               "13F 季度落後 45 天；value_usd 為完整美元；ticker 需研究時由 nameOfIssuer 解析。",
        "conviction_clusters": clusters,
        "by_fund": fund_results,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "latest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote data/smart_money/latest.json — {len(clusters)} conviction clusters, {len(fund_results)} funds")


if __name__ == "__main__":
    main()
