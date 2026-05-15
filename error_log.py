"""
Unified error log → data/errors.log (JSONL).

Each pipeline step calls log_error / log_warning / log_info instead of
just print(). The frontend reads the last N entries and shows the most
recent in DATA HEALTH.

Log roll: keeps last MAX_LINES entries (default 200).
"""

import json
import os
import traceback as _tb
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
LOG_PATH = os.path.join(DATA_DIR, "errors.log")
MAX_LINES = 200


def _write_entry(entry: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    lines = []
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []
    lines.append(json.dumps(entry, ensure_ascii=False) + "\n")
    if len(lines) > MAX_LINES:
        lines = lines[-MAX_LINES:]
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def log_error(stage: str, error, kind: str = "error"):
    """Append an error/warning/info entry.
    error: Exception instance OR plain string.
    kind: 'error' | 'warning' | 'info'
    """
    if isinstance(error, BaseException):
        msg = str(error)[:500]
        tb = "".join(_tb.format_exception(type(error), error, error.__traceback__))[-1500:]
    else:
        msg = str(error)[:500]
        tb = None

    entry = {
        "ts":    datetime.now(TZ).isoformat(timespec="seconds"),
        "stage": stage,
        "kind":  kind,
        "msg":   msg,
    }
    if tb:
        entry["traceback"] = tb
    _write_entry(entry)


def log_warning(stage: str, message: str):
    log_error(stage, message, kind="warning")


def log_info(stage: str, message: str):
    log_error(stage, message, kind="info")
