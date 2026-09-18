import pytest

from core import stats


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


def test_record_clean_check():
    stats.record_check([])
    s = stats.summary()
    assert s["total_checks"] == 1
    assert s["total_flagged"] == 0


def test_record_flagged_check():
    stats.record_check(["curt", "impolite"])
    s = stats.summary()
    assert s["total_checks"] == 1
    assert s["total_flagged"] == 1
    assert s["per_question"] == {"curt": 1, "impolite": 1}


def test_accumulates_across_calls():
    stats.record_check(["curt"])
    stats.record_check([])
    stats.record_check(["curt", "unprofessional"])
    s = stats.summary()
    assert s["total_checks"] == 3
    assert s["total_flagged"] == 2
    assert s["per_question"] == {"curt": 2, "unprofessional": 1}
    assert s["flagged_pct"] == pytest.approx(200 / 3, rel=0.01)


def test_reset():
    stats.record_check(["curt"])
    stats.reset()
    assert stats.summary()["total_checks"] == 0
