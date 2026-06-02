"""
Constraint Validator — 純 Python，無 LLM
在 CIO Master 輸出後執行，驗證並修正違反硬性限制的建議。

這是系統中唯一真正能「攔截」CIO 輸出的模組。
LLM 只能被「建議」遵守限制，這裡才是真正的 enforcement。
"""

# ── Hard Constraints（可依個人狀況調整）──────────────────────────────────────

# 單一股票最大部位（占交易預算）
SINGLE_STOCK_MAX_PCT = 0.30     # 不超過交易預算的 30%（即整體的 ~9%）

# 單一板塊最大集中度（AI/半導體/散熱等）
SECTOR_MAX_PCT = 0.50           # 單一板塊不超過交易預算的 50%

# 高風險標的黑名單（在此 regime 下自動降為 WATCH）
HIGH_RISK_REGIMES_FOR_BREAKOUT = {"高檔震盪", "空頭賣壓", "恐慌盤"}

# 當 wealth_risk 觸發時，禁止的動作
BLOCKED_ACTIONS_ON_WEALTH_RISK = {"BUY", "ADD"}

# 允許的 verdict 組合（防止 CIO 在低預算時仍給高進攻 verdict）
MAX_VERDICT_BY_TRADING_BUDGET = {
    # trading_budget_pct → 最高允許的 verdict
    0.00: "全面防守",
    0.05: "局部防守",
    0.15: "維持現狀",
    0.30: "局部進攻",
    1.00: "全面進攻",
}


def _get_max_verdict(trading_pct: float) -> str:
    for threshold in sorted(MAX_VERDICT_BY_TRADING_BUDGET.keys()):
        if trading_pct <= threshold:
            return MAX_VERDICT_BY_TRADING_BUDGET[threshold]
    return "全面進攻"


VERDICT_RANK = {
    "全面防守": 0,
    "局部防守": 1,
    "維持現狀": 2,
    "局部進攻": 3,
    "全面進攻": 4,
}


def validate(
    master_output: dict,
    capital_flow: dict,
    regime: dict,
) -> dict:
    """
    驗證 CIO 輸出，修正違規建議。
    回傳：修正後的 master_output，violations 列表附加在 agent_note。
    """
    violations = []
    output = dict(master_output)
    output["_raw_verdict"] = master_output.get("verdict", "")  # preserve for logging

    # If master is degraded/errored, don't fabricate a verdict via budget
    # caps — that misleads the user into thinking we have an opinion.
    # Just pass through with an annotation; front-end already shows the
    # DEGRADED badge.
    if master_output.get("_degraded") or master_output.get("verdict") in ("ERROR", "—", "", None):
        output["constraint_violations"] = []
        output["constraints_triggered"] = False
        return output

    budget = capital_flow.get("budget", {})
    trading_pct = budget.get("trading", 0.30)
    wealth_risk_triggered = capital_flow.get("wealth_risk_triggered", False)
    regime_name = regime.get("market_regime", "")

    # ── Check 1: Verdict vs trading budget ───────────────────────────────────
    cio_verdict = output.get("verdict", "維持現狀")
    max_allowed = _get_max_verdict(trading_pct)

    if VERDICT_RANK.get(cio_verdict, 2) > VERDICT_RANK.get(max_allowed, 2):
        violations.append(
            f"VERDICT_OVERRIDE: CIO 給出「{cio_verdict}」"
            f"但交易預算僅 {trading_pct*100:.0f}%，"
            f"強制修正為「{max_allowed}」"
        )
        output["verdict"] = max_allowed

    # ── Check 2: Blocked actions on wealth risk ───────────────────────────────
    if wealth_risk_triggered:
        recs = output.get("recommendations", [])
        modified_recs = []
        for rec in recs:
            action = str(rec.get("action", "")).upper()
            if action in BLOCKED_ACTIONS_ON_WEALTH_RISK:
                violations.append(
                    f"ACTION_BLOCKED: {rec.get('action')} {rec.get('target','?')} "
                    f"→ 財富風險觸發，降為 WATCH"
                )
                rec = {**rec, "action": "WATCH", "urgency": "LOW",
                       "detail": f"[BLOCKED by wealth risk] {rec.get('detail','')}"}
            modified_recs.append(rec)
        output["recommendations"] = modified_recs

    # ── Check 3: Breakout signals in hostile regimes ──────────────────────────
    if regime_name in HIGH_RISK_REGIMES_FOR_BREAKOUT:
        recs = output.get("recommendations", [])
        modified_recs = []
        for rec in recs:
            detail = str(rec.get("detail", "")).lower()
            # Flag if detail mentions breakout/追價 in hostile regime
            if any(kw in detail for kw in ["突破", "breakout", "追價", "追高", "強攻"]):
                violations.append(
                    f"REGIME_SIGNAL_BLOCKED: {rec.get('target','?')} 的追價信號"
                    f"在「{regime_name}」體制下降為 WATCH"
                )
                rec = {**rec, "action": "WATCH", "urgency": "LOW",
                       "detail": f"[REGIME_BLOCKED: {regime_name}] {rec.get('detail','')}"}
            modified_recs.append(rec)
        output["recommendations"] = modified_recs

    # ── Check 4: Zero trading budget → clear all BUY/ADD ─────────────────────
    if trading_pct == 0.0:
        recs = output.get("recommendations", [])
        modified_recs = []
        for rec in recs:
            action = str(rec.get("action", "")).upper()
            if action in {"BUY", "ADD"}:
                violations.append(
                    f"BUDGET_ZERO: {rec.get('target','?')} BUY/ADD 不被允許（交易預算=0）"
                )
                rec = {**rec, "action": "HOLD", "urgency": "LOW",
                       "detail": f"[BUDGET=0] {rec.get('detail','')}"}
            modified_recs.append(rec)
        output["recommendations"] = modified_recs

    # ── Append violations to agent_note ──────────────────────────────────────
    if violations:
        existing_note = output.get("agent_note", "")
        violation_str = " | ".join(violations)
        output["agent_note"] = f"[CONSTRAINT_VIOLATIONS: {len(violations)}] {violation_str}"
        if existing_note:
            output["agent_note"] += f" || {existing_note}"
        output["constraint_violations"] = violations
        output["constraints_triggered"] = True
    else:
        output["constraint_violations"] = []
        output["constraints_triggered"] = False

    return output
