"""Shared, OS-agnostic storage for the watched-app allowlist.

Config lives at ~/.jev-send-guard/config.toml. Format:

    [[apps]]
    label = "Discord messages"
    process_name = "Discord.exe"

Default is an empty list — nothing is watched until the user explicitly
adds an app via `manage.py add`. See PLAN.md's non-goals: this boundary
is deliberate, not an oversight.
"""

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - project requires >=3.11
    import tomli as tomllib

import tomli_w

CONFIG_DIR = Path.home() / ".jev-send-guard"
CONFIG_PATH = CONFIG_DIR / "config.toml"


def load_config():
    if not CONFIG_PATH.exists():
        return {"apps": []}
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)
    config.setdefault("apps", [])
    return config


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump(config, f)


def list_apps():
    return load_config().get("apps", [])


def add_app(label, process_name):
    """Adds or replaces (by process_name, case-insensitive) a watched app."""
    config = load_config()
    apps = [a for a in config["apps"] if a["process_name"].lower() != process_name.lower()]
    apps.append({"label": label, "process_name": process_name})
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


def is_watched(process_name, config=None):
    config = config or load_config()
    process_name = process_name.lower()
    return any(a["process_name"].lower() == process_name for a in config["apps"])
