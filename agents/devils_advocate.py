"""
反對派 Agent (Devil's Advocate) — 打臉所有其他 Agent
Bias: 極度懷疑，專找漏洞
禁止: 不能給出任何正面肯定，只能質疑和反駁
"""

from .base import call_llm
import json

SYSTEM = """你是一位投資委員會裡的反對派委員，你的唯一職責是挑戰其他分析師的結論。

【角色偏見 — 極度懷疑主義】
- 沒有任何結論是你不會質疑的
- 你相信市場永遠在最多人預期錯的地方打臉所有人
- 你的工作是保護投資組合免受「groupthink（群體思維）」傷害

【分析方法】
1. 如果其他 Agent 都說「進攻」，你要問：如果你們全錯了呢？
2. 找出每個 Agent 分析中的盲點和假設
3. 提出市場上被大家忽略的尾部風險（black swan）
4. 質疑每個「看起來很好」的機會

【絕對禁止】
- ❌ 不能說「這個分析不錯」
- ❌ 不能給出操作建議（你只負責質疑，不負責行動）
- ❌ 不能只說空話，每個質疑都要有具體論點

verdict 只能是：強烈反對 / 部分反對 / 有所保留 / 勉強接受（但永遠不會是「同意」）"""


def run(all_agent_outputs: dict) -> dict:
    summaries = []
    for agent_name, output in all_agent_outputs.items():
        insights = "; ".join(output.get("key_insights", [])[:3])
        risks = "; ".join(output.get("risk_flags", [])[:3])
        summaries.append(
            f"【{agent_name}】verdict={output.get('verdict')} confidence={output.get('confidence')}\n"
            f"  摘要: {output.get('summary','')[:120]}\n"
            f"  洞察: {insights or '無'}\n"
            f"  風險: {risks or '無'}"
        )

    # Collect all recommendations
    all_recs = []
    for agent_name, output in all_agent_outputs.items():
        for rec in output.get("recommendations", []):
            all_recs.append(f"{agent_name}: {rec.get('action')} {rec.get('target')} — {rec.get('detail','')[:60]}")

    user_content = f"""其他 Agent 的分析摘要：

{chr(10).join(summaries)}

【所有操作建議彙整】
{chr(10).join(all_recs) if all_recs else '（無建議）'}

請你作為反對派委員，針對以上分析：
1. 哪些結論過於樂觀？為什麼可能錯？
2. 哪些被忽略的尾部風險？（地緣政治、流動性危機、系統性崩跌）
3. 如果市場在接下來 1-2 週出現意外，會是什麼？
4. 哪個 Agent 的分析最需要被質疑？

記住：你的目的不是阻止所有行動，而是確保決策者看到他們可能忽略的風險。"""

    return call_llm(SYSTEM, user_content, "devils_advocate")
