"""API key storage: OS credential store via `keyring`, with an env var
override for local dev/testing (matches the TYPESAFE_API_KEY convention
already used for the v1 JS smoke tests). One key per provider — see
core/jev_client.py's PROVIDERS:

    jev      -> TYPESAFE_API_KEY
    mercury  -> INCEPTION_API_KEY

The env vars can also live in a `.env` file at the repo root; it's read
once at import and never overrides a variable already set in the
environment.
"""

import os
import sys
from pathlib import Path

import keyring

SERVICE_NAME = "jev-send-guard"
USERNAME = "typesafe_api_key"

_KEYS = {
    "jev": {"env": "TYPESAFE_API_KEY", "username": USERNAME},
    "mercury": {"env": "INCEPTION_API_KEY", "username": "inception_api_key"},
}

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _load_dotenv(path=ENV_PATH):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if name and value:
            os.environ.setdefault(name, value)


_load_dotenv()

# keyring's default backend selection scans installed packages' entry points
# to pick the "best" backend, which is a well-known source of a slow first
# call (can be a second or more). Setting the OS-appropriate backend
# explicitly skips that discovery.
if sys.platform == "win32":
    try:
        from keyring.backends.Windows import WinVaultKeyring

        keyring.set_keyring(WinVaultKeyring())
    except Exception:
        pass  # fall back to auto-detected backend
elif sys.platform == "darwin":
    try:
        from keyring.backends.macOS import Keyring as MacKeyring

        keyring.set_keyring(MacKeyring())
    except Exception:
        pass


def env_var_for(provider="jev"):
    return _KEYS[provider]["env"]


def get_api_key(provider="jev"):
    env_value = os.environ.get(_KEYS[provider]["env"])
    if env_value:
        return env_value
    try:
        return keyring.get_password(SERVICE_NAME, _KEYS[provider]["username"])
    except keyring.errors.KeyringError:
        return None


def set_api_key(value, provider="jev"):
    keyring.set_password(SERVICE_NAME, _KEYS[provider]["username"], value)


def clear_api_key(provider="jev"):
    try:
        keyring.delete_password(SERVICE_NAME, _KEYS[provider]["username"])
    except keyring.errors.PasswordDeleteError:
        pass
