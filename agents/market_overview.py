"""
市場總覽 Agent — 大方向、市場體制判斷
Bias: 中立但謹慎，優先保本
"""

from .base import call_claude
import json

SYSTEM = """你是一位資深的宏觀市場分析師，負責提供每日市場體制判斷。

【角色偏見】
- 中立但偏謹慎，在不確定時傾向防守
- 優先考慮系統性風險，不追漲

【職責】
- 判斷目前市場處於「風險開啟(Risk-On)」或「風險關閉(Risk-Off)」
- 分析台股大盤位置（高/中/低）
- 分析美股大盤位置
- 識別今日/本週重要事件
- 給出大方向指引，讓其他 Agent 有基礎方向

【輸出 verdict 只能是】
進攻 / 小心進攻 / 觀望 / 防守 / 積極防守

【你不評論個股，只看大盤和總體趨勢】"""


def run(market_data: dict, portfolio: dict, regime: dict = None) -> dict:
    indices = market_data.get("indices", {})
    fx = market_data.get("fx", {})

    regime_line = f"\n【市場體制引擎】{regime['regime_summary']}\n" if regime else ""

    user_content = f"""{regime_line}今日市場資料：

【大盤指數】
- 加權指數 (TAIEX): {indices.get('taiex', {}).get('close', 'N/A')}
  漲跌: {indices.get('taiex', {}).get('change_pct', 'N/A')}%
- S&P 500: {indices.get('sp500', {}).get('close', 'N/A')}
  漲跌: {indices.get('sp500', {}).get('change_pct', 'N/A')}%
- NASDAQ: {indices.get('nasdaq', {}).get('close', 'N/A')}
  漲跌: {indices.get('nasdaq', {}).get('change_pct', 'N/A')}%
- VIX 恐慌指數: {indices.get('vix', {}).get('close', 'N/A')}

【匯率】
- USD/TWD: {fx.get('usd_twd', 'N/A')}
- USD/JPY: {fx.get('jpy_per_usd', 'N/A')}
- JPY/TWD: {fx.get('twd_per_jpy', 'N/A')}

【資產組合概況】
- 台股市值: NT${portfolio['tw_summary']['total_value']:,}（報酬 {portfolio['tw_summary']['total_pnl_pct']}%）
- 美股部位（現有）: {len(portfolio['us_stocks'])} 檔標的
- 房產: {portfolio['real_estate']['name']}，總價 {portfolio['real_estate']['total_price']:,}，{portfolio['real_estate']['status']}

請根據以上資料判斷今日市場體制，給出其他 Agent 的操作基礎方向。"""

    return call_claude(SYSTEM, user_content, "market_overview")
