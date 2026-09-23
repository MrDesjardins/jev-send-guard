import pytest

from core import config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Every test gets its own throwaway config.toml so tests never touch
    (or depend on) the real ~/.jev-send-guard/config.toml."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    yield


def test_default_config_is_empty():
    assert config.list_apps() == []


def test_corrupted_config_fails_closed_instead_of_raising():
    # Regression: load_config() used to have no exception handling at all
    # around parsing config.toml. Since watch_loop.py calls list_apps()
    # on every ~300ms poll, an unhandled exception here (bad manual edit,
    # crash mid-write, a race between two processes saving at once) would
    # propagate straight up and, in tray_app.py, silently kill the
    # background watch thread with no visible symptom.
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text("this is not [valid toml", encoding="utf-8")

    assert config.list_apps() == []
    assert config.load_config() == {"apps": []}


def test_add_and_list_app():
    config.add_app(label="Discord messages", process_name="Discord.exe")
    apps = config.list_apps()
    assert len(apps) == 1
    assert apps[0]["label"] == "Discord messages"
    assert apps[0]["process_name"] == "Discord.exe"


def test_remove_app():
    config.add_app(label="Discord messages", process_name="Discord.exe")
    removed = config.remove_app("Discord messages")
    assert removed == 1
    assert config.list_apps() == []


def test_remove_nonexistent_app_removes_nothing():
    assert config.remove_app("nope") == 0


def test_browser_requires_matching_domain_to_be_watched():
    config.add_app(label="Google Docs", process_name="firefox.exe", domains=["docs.google.com"])
    assert config.is_watched("firefox.exe", domain="docs.google.com") is True
    assert config.is_watched("firefox.exe", domain="mail.google.com") is False
    assert config.is_watched("firefox.exe", domain=None) is False


def test_non_browser_app_ignores_domain():
    config.add_app(label="Discord messages", process_name="Discord.exe")
    assert config.is_watched("Discord.exe") is True
    assert config.is_watched("Discord.exe", domain="anything.com") is True


def test_unwatched_app_is_not_watched():
    assert config.is_watched("Notepad.exe") is False


def test_readding_browser_merges_domains_instead_of_replacing():
    config.add_app(label="Gmail", process_name="firefox.exe", domains=["mail.google.com"])
    config.add_app(label="Google stuff", process_name="firefox.exe", domains=["docs.google.com"])
    apps = config.list_apps()
    assert len(apps) == 1
    assert set(apps[0]["domains"]) == {"mail.google.com", "docs.google.com"}


def test_normalize_domain_accepts_host_and_url_forms():
    assert config.normalize_domain("docs.google.com") == "docs.google.com"
    assert config.normalize_domain("https://docs.google.com/x") == "docs.google.com"
    assert config.normalize_domain("HTTPS://DOCS.GOOGLE.COM") == "docs.google.com"


def test_normalize_domain_rejects_garbage():
    assert config.normalize_domain("") is None
    assert config.normalize_domain("not a domain") is None
    assert config.normalize_domain(None) is None


def test_question_overrides_round_trip():
    config.set_question_override("unprofessional", enabled=False)
    overrides = config.get_question_overrides()
    assert overrides["unprofessional"]["enabled"] is False

    config.reset_question_override("unprofessional")
    assert "unprofessional" not in config.get_question_overrides()


def test_per_app_disabled_questions():
    config.add_app(label="Discord messages", process_name="Discord.exe")
    config.set_app_disabled_questions("Discord.exe", ["unprofessional"])
    app = config.get_app("Discord.exe")
    assert app["disabled_questions"] == ["unprofessional"]


def test_readding_an_app_preserves_its_disabled_questions():
    # Regression: add_app used to only preserve a browser's domains when
    # re-adding an already-watched app, silently dropping any per-app
    # question tuning set via "Edit selected" in the process.
    config.add_app(label="Discord messages", process_name="Discord.exe")
    config.set_app_disabled_questions("Discord.exe", ["unprofessional"])

    config.add_app(label="Discord messages", process_name="Discord.exe")

    app = config.get_app("Discord.exe")
    assert app["disabled_questions"] == ["unprofessional"]


def test_get_app_returns_none_for_unknown_process():
    assert config.get_app("Nope.exe") is None


def test_add_update_remove_custom_question():
    config.add_custom_question(
        "overpromising",
        instructions="Does this overpromise?",
        message="This might be overpromising.",
        severity="warning",
    )
    custom = config.list_custom_questions()
    assert custom["overpromising"]["message"] == "This might be overpromising."
    assert custom["overpromising"]["enabled"] is True
    assert custom["overpromising"]["criteria"] == {
        "true": "This is a concern.",
        "false": "This is not a concern.",
    }

    config.update_custom_question("overpromising", severity="error")
    assert config.list_custom_questions()["overpromising"]["severity"] == "error"

    assert config.remove_custom_question("overpromising") is True
    assert "overpromising" not in config.list_custom_questions()


def test_remove_nonexistent_custom_question_returns_false():
    assert config.remove_custom_question("nope") is False


def test_update_nonexistent_custom_question_is_a_no_op():
    config.update_custom_question("nope", severity="error")
    assert "nope" not in config.list_custom_questions()


def test_idle_seconds_defaults_to_hardcoded_value():
    from core.idle_watcher import IDLE_SECONDS

    assert config.get_idle_seconds() == IDLE_SECONDS == config.DEFAULT_IDLE_SECONDS


def test_idle_seconds_round_trip():
    assert config.set_idle_seconds("1.5") == 1.5
    assert config.get_idle_seconds() == 1.5
    # Other config is preserved alongside it.
    config.add_app(label="Discord", process_name="Discord.exe")
    assert config.get_idle_seconds() == 1.5


def test_idle_seconds_accepts_comma_decimal_and_bounds():
    assert config.validate_idle_seconds("0,8") == 0.8
    assert config.validate_idle_seconds(config.MIN_IDLE_SECONDS) == config.MIN_IDLE_SECONDS
    assert config.validate_idle_seconds(config.MAX_IDLE_SECONDS) == config.MAX_IDLE_SECONDS
    assert config.validate_idle_seconds(2) == 2.0


@pytest.mark.parametrize(
    "value", ["", "  ", "abc", "1.2.3", "nan", "inf", "-1", "0", "0.1", "10.5", "100", True, None]
)
def test_idle_seconds_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        config.validate_idle_seconds(value)


def test_set_idle_seconds_invalid_does_not_write():
    config.set_idle_seconds(2)
    with pytest.raises(ValueError):
        config.set_idle_seconds("99")
    assert config.get_idle_seconds() == 2.0


def test_reset_idle_seconds_restores_default():
    config.set_idle_seconds(3)
    config.reset_idle_seconds()
    assert config.get_idle_seconds() == config.DEFAULT_IDLE_SECONDS
    assert "idle_seconds" not in config.load_config()


def test_invalid_idle_seconds_in_file_falls_back_to_default():
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text('idle_seconds = "soon"\n', encoding="utf-8")
    assert config.get_idle_seconds() == config.DEFAULT_IDLE_SECONDS
    config.CONFIG_PATH.write_text("idle_seconds = 500\n", encoding="utf-8")
    assert config.get_idle_seconds() == config.DEFAULT_IDLE_SECONDS
