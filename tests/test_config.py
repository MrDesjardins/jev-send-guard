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


def test_get_app_returns_none_for_unknown_process():
    assert config.get_app("Nope.exe") is None
