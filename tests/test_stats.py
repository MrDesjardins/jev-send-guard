import json
from datetime import date

import pytest

from core import stats

DAY1 = date(2026, 10, 18)
DAY2 = date(2026, 10, 19)


@pytest.fixture(autouse=True)
def isolated_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "STATS_PATH", tmp_path / "stats.json")
    yield


def test_empty_summary():
    s = stats.summary()
    assert s["total_checks"] == 0
    assert s["total_flagged"] == 0
    assert s["flagged_pct"] == 0.0
    assert s["per_question"] == {}
    assert s["legacy"] is None
    assert stats.recorded_days() == []


def test_record_clean_check():
    stats.record_check({"curt": "good", "impolite": "good"})
    s = stats.summary()
    assert s["total_checks"] == 1
    assert s["total_flagged"] == 0
    assert s["per_question"]["curt"] == {"good": 1, "warning": 0, "error": 0}


def test_record_flagged_check_by_severity():
    stats.record_check({"curt": "error", "unprofessional": "warning", "impolite": "good"})
    s = stats.summary()
    assert s["total_checks"] == 1
    assert s["total_flagged"] == 1
    assert s["per_question"] == {
        "curt": {"good": 0, "warning": 0, "error": 1},
        "unprofessional": {"good": 0, "warning": 1, "error": 0},
        "impolite": {"good": 1, "warning": 0, "error": 0},
    }


def test_unknown_outcome_counts_as_warning():
    stats.record_check({"curt": "bogus"})
    assert stats.summary()["per_question"]["curt"]["warning"] == 1


def test_accumulates_across_calls():
    stats.record_check({"curt": "error"})
    stats.record_check({"curt": "good"})
    stats.record_check({"curt": "error", "unprofessional": "warning"})
    s = stats.summary()
    assert s["total_checks"] == 3
    assert s["total_flagged"] == 2
    assert s["per_question"]["curt"] == {"good": 1, "warning": 0, "error": 2}
    assert s["flagged_pct"] == pytest.approx(200 / 3, rel=0.01)


def test_buckets_by_day():
    stats.record_check({"curt": "error"}, day=DAY1)
    stats.record_check({"curt": "good"}, day=DAY2)
    stats.record_check({"curt": "error"}, day=DAY2)

    assert stats.recorded_days() == ["2026-10-19", "2026-10-18"]
    day1 = stats.summary([DAY1])
    assert day1["total_checks"] == 1
    assert day1["per_question"]["curt"] == {"good": 0, "warning": 0, "error": 1}
    day2 = stats.summary(["2026-10-19"])
    assert day2["total_checks"] == 2
    assert day2["per_question"]["curt"] == {"good": 1, "warning": 0, "error": 1}
    assert stats.summary()["total_checks"] == 3


def test_last_n_days():
    assert stats.last_n_days(3, today=DAY2) == ["2026-10-19", "2026-10-18", "2026-10-17"]


def test_legacy_file_is_preserved_but_not_merged():
    stats.STATS_PATH.write_text(json.dumps({
        "total_checks": 32,
        "total_flagged": 18,
        "per_question": {"sarcasm": 6},
    }), encoding="utf-8")

    stats.record_check({"sarcasm": "error"}, day=DAY2)

    all_time = stats.summary()
    assert all_time["total_checks"] == 1
    assert all_time["legacy"]["total_checks"] == 32
    assert all_time["legacy"]["per_question"] == {"sarcasm": 6}
    assert stats.summary([DAY2])["legacy"] is None
    # Still there after the file was rewritten in the new format.
    assert json.loads(stats.STATS_PATH.read_text(encoding="utf-8"))["legacy"]["total_flagged"] == 18


def test_corrupt_file_starts_fresh():
    stats.STATS_PATH.write_text("not json", encoding="utf-8")
    assert stats.summary()["total_checks"] == 0


def test_reset():
    stats.record_check({"curt": "error"})
    stats.reset()
    assert stats.summary()["total_checks"] == 0
    assert stats.recorded_days() == []


def test_tracks_per_app():
    stats.record_check({"curt": "error"}, app="Discord", day=DAY1)
    stats.record_check({"curt": "good"}, app="Slack", day=DAY1)
    stats.record_check({"curt": "error"}, app="Discord", day=DAY2)

    assert stats.recorded_apps() == ["Discord", "Slack"]
    assert stats.recorded_days(app="Slack") == ["2026-10-18"]
    discord = stats.summary(app="Discord")
    assert discord["total_checks"] == 2
    assert discord["per_question"]["curt"] == {"good": 0, "warning": 0, "error": 2}
    assert discord["legacy"] is None
    assert stats.summary([DAY1], app="Slack")["total_flagged"] == 0
    assert stats.summary()["total_checks"] == 3


def test_missing_app_is_bucketed_as_unknown():
    stats.record_check({"curt": "good"})
    assert stats.recorded_apps() == [stats.UNKNOWN_APP]


def test_grouped_summaries():
    stats.record_check({"curt": "error"}, app="Discord", day=DAY1)
    stats.record_check({"curt": "good"}, app="Slack", day=DAY1)
    stats.record_check({"curt": "warning"}, app="Discord", day=DAY2)

    by_app = stats.grouped_summaries("app")
    assert by_app["Discord"]["total_checks"] == 2
    assert by_app["Slack"]["total_flagged"] == 0
    by_day = stats.grouped_summaries("day", app="Discord")
    assert set(by_day) == {"2026-10-18", "2026-10-19"}
    assert by_day["2026-10-19"]["per_question"]["curt"]["warning"] == 1
    assert set(stats.grouped_summaries("app", days=[DAY2])) == {"Discord"}
    with pytest.raises(ValueError):
        stats.grouped_summaries("week")


def test_top_feedback_check():
    assert stats.top_feedback_check({}) is None
    assert stats.top_feedback_check({"curt": {"good": 3, "warning": 0, "error": 0}}) is None
    assert stats.top_feedback_check({
        "curt": {"good": 0, "warning": 0, "error": 1},
        "sarcasm": {"good": 0, "warning": 1, "error": 2},
    }) == "sarcasm"


def test_per_day_file_without_apps_is_migrated():
    stats.STATS_PATH.write_text(json.dumps({
        "version": 2,
        "days": {"2026-10-19": {"checks": 2, "flagged": 1, "per_question": {
            "curt": {"good": 1, "warning": 0, "error": 1},
        }}},
    }), encoding="utf-8")

    stats.record_check({"curt": "good"}, app="Discord", day=DAY2)

    assert stats.recorded_apps() == [stats.UNKNOWN_APP, "Discord"]
    assert stats.summary(app=stats.UNKNOWN_APP)["total_checks"] == 2
    assert stats.summary([DAY2])["total_checks"] == 3
