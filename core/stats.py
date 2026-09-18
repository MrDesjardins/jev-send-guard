"""Local-only usage stats: how many checks actually ran, how many got
flagged, and which question fired most often. Never sent anywhere — purely
so you can judge from the Settings window whether thresholds need tuning
(e.g. "unprofessional" over-firing on casual chat, as PLAN.md anticipated)
instead of guessing. Stored at ~/.jev-send-guard/stats.json, separate from
config.toml since this is derived/disposable data, not configuration.

Only counts messages that actually reached Jev and got a real answer —
pre-filter skips (too short/plain ack) and failed/timed-out calls
(fail-open) are deliberately not "checks," since they say nothing about
whether the thresholds are well-tuned.
"""

import json
import threading
from pathlib import Path

STATS_PATH = Path.home() / ".jev-send-guard" / "stats.json"
_lock = threading.Lock()


def _load():
    if not STATS_PATH.exists():
        return {"total_checks": 0, "total_flagged": 0, "per_question": {}}
    try:
        with open(STATS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"total_checks": 0, "total_flagged": 0, "per_question": {}}
    data.setdefault("total_checks", 0)
    data.setdefault("total_flagged", 0)
    data.setdefault("per_question", {})
    return data


def _save(data):
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def record_check(flagged_keys):
    """Call once per real Jev evaluation. `flagged_keys` is the iterable of
    question keys that fired (empty if the draft came back clean)."""
    flagged_keys = list(flagged_keys)
    with _lock:
        data = _load()
        data["total_checks"] += 1
        if flagged_keys:
            data["total_flagged"] += 1
        for key in flagged_keys:
            data["per_question"][key] = data["per_question"].get(key, 0) + 1
        _save(data)


def summary():
    data = _load()
    total = data["total_checks"]
    flagged = data["total_flagged"]
    return {
        "total_checks": total,
        "total_flagged": flagged,
        "flagged_pct": (flagged / total * 100) if total else 0.0,
        "per_question": dict(data["per_question"]),
    }


def reset():
    with _lock:
        _save({"total_checks": 0, "total_flagged": 0, "per_question": {}})
