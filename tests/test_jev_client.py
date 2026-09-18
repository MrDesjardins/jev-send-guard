import httpx
import pytest

from core import config, jev_client


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    yield


def test_get_question_defs_defaults():
    defs = jev_client.get_question_defs()
    assert set(defs) == {"curt", "missing_ask", "unprofessional", "impolite"}
    assert defs["curt"]["severity"] == "error"
    assert defs["unprofessional"]["severity"] == "warning"
    assert all(q["enabled"] for q in defs.values())


def test_override_disables_a_question():
    config.set_question_override("unprofessional", enabled=False)
    defs = jev_client.get_question_defs()
    assert defs["unprofessional"]["enabled"] is False
    assert defs["curt"]["enabled"] is True


def test_override_changes_instructions_and_message_without_touching_defaults():
    config.set_question_override("curt", message="Custom message", instructions="Custom instructions")
    defs = jev_client.get_question_defs()
    assert defs["curt"]["message"] == "Custom message"
    assert defs["curt"]["instructions"] == "Custom instructions"
    assert defs["curt"]["severity"] == "error"  # untouched field keeps its default
    # QUESTIONS itself (the built-in defaults) must never be mutated.
    assert jev_client.QUESTIONS["curt"]["message"] != "Custom message"


def test_message_for_and_severity_of_reflect_overrides():
    config.set_question_override("impolite", severity="warning")
    assert jev_client.severity_of("impolite") == "warning"
    assert jev_client.message_for("curt") == jev_client.QUESTIONS["curt"]["message"]


def test_no_api_key_returns_none():
    assert jev_client.check_draft(None, "some text") is None
    assert jev_client.check_draft("", "some text") is None


def test_all_questions_disabled_returns_empty_without_network_call(monkeypatch):
    for key in jev_client.QUESTIONS:
        config.set_question_override(key, enabled=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not have called the network")

    monkeypatch.setattr(jev_client._CLIENT, "post", fail_if_called)
    assert jev_client.check_draft("fake-key", "some text") == {}


def test_disabled_keys_param_excludes_that_question_from_the_request(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "answers": {
                    "curt": {"noul": 0.9},
                    "missing_ask": {"noul": 0.1},
                    "impolite": {"noul": 0.9},
                }
            }

    def fake_post(url, headers, json, timeout):
        captured["questions"] = set(json["questions"])
        return FakeResponse()

    monkeypatch.setattr(jev_client._CLIENT, "post", fake_post)
    result = jev_client.check_draft("fake-key", "some text", disabled_keys={"unprofessional"})

    assert "unprofessional" not in captured["questions"]
    assert captured["questions"] == {"curt", "missing_ask", "impolite"}
    assert result == {"curt": True, "missing_ask": False, "impolite": True}


def test_fails_open_on_http_error(monkeypatch):
    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(jev_client._CLIENT, "post", fake_post)
    assert jev_client.check_draft("fake-key", "some text") is None


def test_fails_open_on_non_200(monkeypatch):
    class FakeResponse:
        status_code = 500

        def json(self):
            return {}

    monkeypatch.setattr(jev_client._CLIENT, "post", lambda *a, **k: FakeResponse())
    assert jev_client.check_draft("fake-key", "some text") is None


def test_fails_open_on_missing_answers(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {}

    monkeypatch.setattr(jev_client._CLIENT, "post", lambda *a, **k: FakeResponse())
    assert jev_client.check_draft("fake-key", "some text") is None


def test_score_below_threshold_is_not_flagged(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"answers": {"curt": {"noul": 0.69}}}

    monkeypatch.setattr(jev_client._CLIENT, "post", lambda *a, **k: FakeResponse())
    result = jev_client.check_draft(
        "fake-key", "some text",
        disabled_keys={"missing_ask", "unprofessional", "impolite"},
    )
    assert result == {"curt": False}
