"""Local-only usage stats: how many checks actually ran, how many got
flagged, and — per question — how often a draft came back good vs. raised
a warning vs. an error, bucketed by local calendar day and by watched app.
Never sent anywhere — purely so you can judge from the Settings window
which kind of feedback you get most (and where), and whether thresholds
need tuning (e.g. "unprofessional" over-firing on casual chat, as PLAN.md
anticipated) instead of guessing. Stored at ~/.jev-send-guard/stats.json,
separate from config.toml since this is derived/disposable data, not
configuration.

Only counts messages that actually reached Jev and got a real answer —
pre-filter skips (too short/plain ack) and failed/timed-out calls
(fail-open) are deliberately not "checks," since they say nothing about
whether the thresholds are well-tuned.

Layout: {"version": 3, "days": {"YYYY-MM-DD": {"<app label>": bucket}}},
where a bucket is {"checks", "flagged", "per_question": {key: {"good",
"warning", "error"}}}. Stats files written before per-day tracking only had
all-time totals and flag counts (no day, app, severity or "good" count).
Those are kept as-is under "legacy" and reported separately rather than
guessed into days.
"""

import json
import os
import threading
from datetime import date, timedelta
from pathlib import Path

STATS_PATH = Path.home() / ".jev-send-guard" / "stats.json"
OUTCOMES = ("good", "warning", "error")
UNKNOWN_APP = "(unknown app)"
_lock = threading.Lock()


def _empty():
    return {"version": 3, "days": {}}


def _empty_bucket():
    return {"checks": 0, "flagged": 0, "per_question": {}}


def _load():
    if not STATS_PATH.exists():
        return _empty()
    try:
        with open(STATS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    if "days" not in data:
        # Pre-per-day format: preserve its totals instead of dropping them.
        legacy = {
            "total_checks": data.get("total_checks", 0),
            "total_flagged": data.get("total_flagged", 0),
            "per_question": dict(data.get("per_question", {})),
        }
        data = _empty()
        if legacy["total_checks"]:
            data["legacy"] = legacy
    days = data.setdefault("days", {})
    for day_key, day in days.items():
        if "checks" in day:
            # Per-day but pre-per-app format: one bucket for the whole day.
            days[day_key] = {UNKNOWN_APP: day}
    data["version"] = 3
    return data


def _save(data):
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATS_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, STATS_PATH)


def record_check(outcomes, app=None, day=None):
    """Call once per real Jev evaluation. `outcomes` maps each question key
    that was evaluated to "good", "warning" or "error" (the question's
    severity when it fired). `app` is the watched app's label; `day`
    defaults to today's local date."""
    outcomes = dict(outcomes)
    day_key = (day or date.today()).isoformat()
    with _lock:
        data = _load()
        day_apps = data["days"].setdefault(day_key, {})
        bucket = day_apps.setdefault(app or UNKNOWN_APP, _empty_bucket())
        bucket["checks"] += 1
        if any(outcome != "good" for outcome in outcomes.values()):
            bucket["flagged"] += 1
        for key, outcome in outcomes.items():
            if outcome not in OUTCOMES:
                outcome = "warning"
            counts = bucket["per_question"].setdefault(key, dict.fromkeys(OUTCOMES, 0))
            counts[outcome] = counts.get(outcome, 0) + 1
        _save(data)


def recorded_days(app=None):
    """Days with at least one recorded check (for `app`, if given), most
    recent first."""
    return sorted(
        (day for day, apps in _load()["days"].items() if app is None or app in apps),
        reverse=True,
    )


def recorded_apps():
    """Every app with at least one recorded check, alphabetically."""
    apps = set()
    for day_apps in _load()["days"].values():
        apps.update(day_apps)
    return sorted(apps, key=str.lower)


def _day_keys(days):
    if days is None:
        return None
    return {d.isoformat() if isinstance(d, date) else d for d in days}


def _iter_buckets(data, days=None, app=None):
    """(day, app, bucket) for every bucket matching the filters."""
    wanted_days = _day_keys(days)
    for day_key, day_apps in data["days"].items():
        if wanted_days is not None and day_key not in wanted_days:
            continue
        for app_key, bucket in day_apps.items():
            if app is None or app_key == app:
                yield day_key, app_key, bucket


def _aggregate(buckets):
    total = flagged = 0
    per_question = {}
    for bucket in buckets:
        total += bucket.get("checks", 0)
        flagged += bucket.get("flagged", 0)
        for key, counts in bucket.get("per_question", {}).items():
            merged = per_question.setdefault(key, dict.fromkeys(OUTCOMES, 0))
            for outcome in OUTCOMES:
                merged[outcome] += counts.get(outcome, 0)
    return {
        "total_checks": total,
        "total_flagged": flagged,
        "flagged_pct": (flagged / total * 100) if total else 0.0,
        "per_question": per_question,
    }


def summary(days=None, app=None):
    """Aggregate stats. `days` is None for all time, or an iterable of
    date / ISO-date strings to restrict to; `app` is None for every app,
    or one app label. `legacy` (pre-per-day totals) is only included in
    the unfiltered all-time view, and never merged into the per-question
    good/warning/error counts."""
    data = _load()
    result = _aggregate(bucket for _d, _a, bucket in _iter_buckets(data, days, app))
    result["legacy"] = data.get("legacy") if days is None and app is None else None
    return result


def grouped_summaries(by, days=None, app=None):
    """{group: summary} with `by` being "app" or "day", each summary shaped
    like summary()'s (without `legacy`), under the same filters."""
    if by not in ("app", "day"):
        raise ValueError("by must be 'app' or 'day'")
    groups = {}
    for day_key, app_key, bucket in _iter_buckets(_load(), days, app):
        groups.setdefault(app_key if by == "app" else day_key, []).append(bucket)
    return {group: _aggregate(buckets) for group, buckets in groups.items()}


def top_feedback_check(per_question):
    """The question key with the most warnings + errors, or None if none
    of them gave any feedback."""
    def feedback(key):
        return per_question[key]["warning"] + per_question[key]["error"]

    top = max(per_question, key=feedback, default=None)
    return top if top is not None and feedback(top) else None


def last_n_days(n, today=None):
    """The ISO dates of the last `n` days, today included."""
    today = today or date.today()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(n)]


def reset():
    with _lock:
        _save(_empty())
