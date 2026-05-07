"""
Reflection Agent — 每日盤後自我檢討
分析近期推薦績效，產生結構化反思，供下次推薦注入。
"""

from .base import call_claude

SYSTEM = """你是一位客觀的量化交易績效分析師，負責每日盤後回顧推薦記錄。

你的工作：
1. 分析近期推薦的成功與失敗模式
2. 找出哪種市場體制下策略最有效
3. 找出哪些信號最可靠、哪些需要降低權重
4. 提出下一期推薦時應注意的具體調整

【要求】
- 只說有數據支持的結論，不要憑感覺猜測
- 如果樣本數太少（< 5筆），明確說「資料不足」
- 絕對不要美化失敗，要直接說失敗原因
- 給出 1-3 個具體、可執行的調整建議

verdict 只能是：
- 策略有效（整體勝率 > 55%）
- 策略需調整（整體勝率 40-55%）
- 策略暫停（整體勝率 < 40%）
"""

REFLECTION_SCHEMA = """
輸出格式（strict JSON）：
{
  "verdict": "策略有效 / 策略需調整 / 策略暫停",
  "overall_win_rate": 0.0,
  "confidence": 0.0-1.0,
  "summary": "3句話總結近期表現（繁體中文）",
  "key_findings": [
    "發現1：哪種體制勝率最高",
    "發現2：哪種信號最可靠",
    "發現3：最近失敗的共同原因"
  ],
  "effective_conditions": ["有效條件1", "有效條件2"],
  "ineffective_conditions": ["失效條件1", "失效條件2"],
  "adjustments": [
    {
      "action": "增加/降低/暫停",
      "target": "什麼策略或信號",
      "reason": "為什麼"
    }
  ],
  "agent_note": "資料品質說明（樣本數、時間範圍等）"
}
"""


def run(
    recent_picks: list,
    performance_stats: dict,
    yesterday_result: dict = None,
) -> dict:
    """
    recent_picks: list of resolved pick dicts from alpha_db
    performance_stats: output of alpha_db.get_performance_stats()
    yesterday_result: optional, the pick from yesterday with its outcome
    """
    if not performance_stats.get("available"):
        return {
            "verdict": "策略需調整",
            "confidence": 0,
            "summary": "尚無足夠歷史資料進行反思，需累積至少 5 筆已結算推薦。",
            "key_findings": ["資料不足"],
            "effective_conditions": [],
            "ineffective_conditions": [],
            "adjustments": [],
            "agent_note": "insufficient data",
        }

    # Build compact performance summary
    stats = performance_stats
    lines = []

    lines.append(f"【近期績效摘要（{stats['total']}筆已結算）】")
    lines.append(f"整體勝率：{stats['wins']}/{stats['total']} = {stats['overall_rate']}%")
    lines.append(f"近期走勢：{stats.get('recent_streak', 'N/A')}")

    regime_stats = stats.get("regime_stats", {})
    if regime_stats:
        lines.append("\n【各體制成績】")
        for regime, s in sorted(
            regime_stats.items(),
            key=lambda x: x[1]["wins"] / max(x[1]["total"], 1),
            reverse=True,
        ):
            rate = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0
            lines.append(f"  {regime}：{s['wins']}/{s['total']} = {rate}%")

    signal_stats = stats.get("signal_stats", {})
    if signal_stats:
        lines.append("\n【各信號成績】")
        for sig, s in sorted(
            signal_stats.items(),
            key=lambda x: x[1]["wins"] / max(x[1]["total"], 1),
            reverse=True,
        ):
            rate = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0
            lines.append(f"  {sig}：{rate}% ({s['total']}次)")

    # Last 10 picks detail
    if recent_picks:
        lines.append("\n【最近10筆推薦明細】")
        for p in recent_picks[:10]:
            result = "✓" if p.get("success") == 1 else "✗"
            ret_str = f"{p.get('return_pct', '?'):+.2f}%" if p.get("return_pct") is not None else "?"
            lines.append(
                f"  {p['date']} {p['stock_name']}({p['stock_code']}) "
                f"[{p.get('regime','?')}] → {ret_str} {result}"
            )

    # Yesterday's result if available
    if yesterday_result:
        lines.append(f"\n【昨日結果】")
        lines.append(
            f"推薦 {yesterday_result.get('stock_name')}({yesterday_result.get('stock_code')}) "
            f"於 {yesterday_result.get('regime')} 體制下 → "
            f"報酬 {yesterday_result.get('return_pct', '?')}% "
            f"({'成功' if yesterday_result.get('success') == 1 else '失敗'})"
        )

    user_content = "\n".join(lines) + """

請根據以上績效數據，進行深度反思：
1. 目前策略在哪種市場體制下最有效？
2. 哪些信號組合最可靠？哪些需要降低依賴？
3. 最近的失敗案例有什麼共同模式？
4. 給出 2-3 個具體調整建議，讓下次推薦更精準

注意：如果某個體制或信號的樣本數 < 3，請明確標注「樣本不足」。
"""

    system_with_schema = SYSTEM + "\n\n" + REFLECTION_SCHEMA

    from .base import client, MODEL
    from google.genai import types
    import json, re, time

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_with_schema,
                ),
            )
            raw = resp.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = re.sub(r'\[\d+\]', '', raw)
            return json.loads(raw)
        except json.JSONDecodeError as e:
            return {
                "verdict": "策略需調整",
                "confidence": 0,
                "summary": f"Reflection JSON parse error: {e}",
                "key_findings": [],
                "effective_conditions": [],
                "ineffective_conditions": [],
                "adjustments": [],
                "agent_note": "parse error",
            }
        except Exception as e:
            err = str(e)
            if "429" in err and attempt < 2:
                wait = 15 * (attempt + 1)
                print(f"  [reflection] 429 quota hit, waiting {wait}s...")
                time.sleep(wait)
                continue
            return {
                "verdict": "策略需調整",
                "confidence": 0,
                "summary": f"Reflection error: {e}",
                "key_findings": [],
                "effective_conditions": [],
                "ineffective_conditions": [],
                "adjustments": [],
                "agent_note": "API call failed",
            }
