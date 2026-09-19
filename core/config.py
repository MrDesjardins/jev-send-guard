"""Shared, OS-agnostic storage for the watched-app allowlist.

Config lives at ~/.jev-send-guard/config.toml. Format:

    [[apps]]
    label = "Discord messages"
    process_name = "Discord.exe"

Browser entries additionally carry an explicit list of allowed hosts:

    [[apps]]
    label = "Google Docs"
    process_name = "Google Chrome"
    domains = ["docs.google.com"]

Browser processes without a host rule are deliberately not watched. This
keeps a browser allowlist from becoming an allowlist for every website.

Default is an empty list — nothing is watched until the user explicitly
adds an app via `manage.py add`. See PLAN.md's non-goals: this boundary
is deliberate, not an oversight.

Question tuning also lives here, under two separate tables:

    [questions.unprofessional]
    enabled = false

    [custom_questions.overpromising]
    instructions = "..."
    message = "This might be overpromising."
    severity = "warning"
    criteria = { true = "...", false = "..." }
    enabled = true

`questions` holds *overrides* on the four built-in questions defined in
core/jev_client.py's QUESTIONS (edit wording/severity/enabled without
touching the code defaults — "Reset to default" just deletes the entry
here). `custom_questions` holds fully user-defined questions beyond the
built-in four — deleting one removes it outright, since there's no
code-level default to fall back to. Both are merged by
core/jev_client.py's get_question_defs().
"""

import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - project requires >=3.11
    import tomli as tomllib

import tomli_w

log = logging.getLogger("jev-send-guard")

CONFIG_DIR = Path.home() / ".jev-send-guard"
CONFIG_PATH = CONFIG_DIR / "config.toml"

_BROWSER_PROCESS_NAMES = {
    "chrome",
    "google chrome",
    "chromium",
    "microsoft edge",
    "msedge",
    "firefox",
}


def load_config():
    if not CONFIG_PATH.exists():
        return {"apps": []}
    try:
        with open(CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        # Fail closed to an empty config rather than raising: this is
        # called on every ~300ms poll of the watch loop, so an unhandled
        # exception here (from a bad manual edit, a crash mid-write, or a
        # race between two processes saving around the same time) would
        # propagate straight up and, in tray_app.py, silently kill the
        # background watch thread — the tray icon keeps showing "running"
        # while nothing is ever checked again, with no visible symptom.
        log.exception("config.toml is corrupted or unreadable — treating as empty")
        return {"apps": []}
    config.setdefault("apps", [])
    return config


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump(config, f)


def _process_key(process_name):
    return process_name.lower().removesuffix(".exe")


def is_browser_process(process_name):
    """Whether a process needs an active-tab host before it may be watched."""
    return bool(process_name) and _process_key(process_name) in _BROWSER_PROCESS_NAMES


def normalize_domain(value):
    """Return a normalized exact hostname, accepting either a host or URL."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(char.isspace() for char in value):
        return None
    parsed = urlsplit(value if "://" in value else f"//{value}")
    if parsed.username or parsed.password:
        return None
    hostname = parsed.hostname
    if not hostname or hostname.endswith("."):
        return None
    try:
        return hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def list_apps():
    return load_config().get("apps", [])


def get_app(process_name):
    if not process_name:
        return None
    process_name = process_name.lower()
    return next(
        (a for a in load_config()["apps"] if a["process_name"].lower() == process_name),
        None,
    )


def set_app_disabled_questions(process_name, disabled_keys):
    """Per-app override: which of the four Jev questions to skip for this
    app specifically (e.g. turn off `unprofessional` for a casual Discord
    server without affecting anything else)."""
    config = load_config()
    for app in config["apps"]:
        if app["process_name"].lower() == process_name.lower():
            app["disabled_questions"] = sorted(set(disabled_keys))
            break
    save_config(config)


def add_app(label, process_name, domains=None):
    """Adds or replaces a watched app.

    Re-adding a browser merges its host rules, so one browser can safely
    watch both Gmail and Google Docs without broadening to every website.
    """
    normalized_domains = []
    for domain in domains or []:
        normalized = normalize_domain(domain)
        if normalized and normalized not in normalized_domains:
            normalized_domains.append(normalized)

    config = load_config()
    previous = next(
        (app for app in config["apps"] if app["process_name"].lower() == process_name.lower()),
        None,
    )
    apps = [a for a in config["apps"] if a["process_name"].lower() != process_name.lower()]
    entry = {"label": label, "process_name": process_name}
    if is_browser_process(process_name):
        for domain in (previous or {}).get("domains", []):
            normalized = normalize_domain(domain)
            if normalized and normalized not in normalized_domains:
                normalized_domains.append(normalized)
        entry["domains"] = normalized_domains
    # Re-adding an already-watched app (e.g. re-running "Add app..." for
    # it) must not silently wipe per-app question tuning set via "Edit
    # selected" — only domains used to be preserved here, which lost this
    # the same way until caught in a review.
    if previous and previous.get("disabled_questions"):
        entry["disabled_questions"] = list(previous["disabled_questions"])
    apps.append(entry)
    config["apps"] = apps
    save_config(config)


def remove_app(identifier):
    """Removes any app whose label or process_name matches identifier
    (case-insensitive). Returns the number of entries removed."""
    config = load_config()
    identifier = identifier.lower()
    before = len(config["apps"])
    config["apps"] = [
        a
        for a in config["apps"]
        if identifier not in (a["label"].lower(), a["process_name"].lower())
    ]
    save_config(config)
    return before - len(config["apps"])


def is_watched(process_name, domain=None, config=None):
    """Whether this focused control is inside an allowed app/site.

    Browser host rules are exact-match only. A missing or stale tab host must
    never make a browser eligible for draft reading.
    """
    # Preserve the former ``is_watched(process_name, config)`` call shape for
    # small scripts using this module directly.
    if config is None and isinstance(domain, dict):
        config, domain = domain, None
    config = config or load_config()
    process_name = process_name.lower()
    app = next(
        (a for a in config["apps"] if a["process_name"].lower() == process_name),
        None,
    )
    if app is None:
        return False
    if not is_browser_process(app["process_name"]):
        return True
    normalized_domain = normalize_domain(domain)
    allowed_domains = {
        normalized
        for value in app.get("domains", [])
        if (normalized := normalize_domain(value))
    }
    return normalized_domain in allowed_domains


def app_requires_browser_host(process_name):
    """True for known browser processes, including legacy hostless entries."""
    return is_browser_process(process_name)


def get_question_overrides():
    """User-edited overrides for the built-in Jev questions (see
    core/jev_client.py's QUESTIONS for the defaults each of these merges
    onto). Keyed by question key, e.g.:

        [questions.unprofessional]
        enabled = false
        instructions = "..."
    """
    return load_config().get("questions", {})


def set_question_override(key, **fields):
    config = load_config()
    questions = config.setdefault("questions", {})
    entry = questions.setdefault(key, {})
    entry.update({k: v for k, v in fields.items() if v is not None})
    save_config(config)


def reset_question_override(key):
    config = load_config()
    questions = config.get("questions", {})
    if key in questions:
        del questions[key]
        config["questions"] = questions
        save_config(config)


def list_custom_questions():
    """Fully user-defined questions beyond the built-in four. See
    core/jev_client.py's get_question_defs(), which merges these in."""
    return load_config().get("custom_questions", {})


def add_custom_question(key, instructions, message, severity="warning"):
    config = load_config()
    custom = config.setdefault("custom_questions", {})
    custom[key] = {
        "instructions": instructions,
        "message": message,
        "severity": severity,
        "criteria": {
            "true": "This is a concern.",
            "false": "This is not a concern.",
        },
        "enabled": True,
    }
    save_config(config)


def update_custom_question(key, **fields):
    config = load_config()
    custom = config.get("custom_questions", {})
    if key not in custom:
        return
    custom[key].update({k: v for k, v in fields.items() if v is not None})
    config["custom_questions"] = custom
    save_config(config)


def remove_custom_question(key):
    config = load_config()
    custom = config.get("custom_questions", {})
    if key in custom:
        del custom[key]
        config["custom_questions"] = custom
        save_config(config)
        return True
    return False
