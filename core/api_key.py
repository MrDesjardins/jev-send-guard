"""API key storage: OS credential store via `keyring`, with an env var
override for local dev/testing (matches the TYPESAFE_API_KEY convention
already used for the v1 JS smoke tests).
"""

import os
import sys

import keyring

SERVICE_NAME = "jev-send-guard"
USERNAME = "typesafe_api_key"

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


def get_api_key():
    env_value = os.environ.get("TYPESAFE_API_KEY")
    if env_value:
        return env_value
    return keyring.get_password(SERVICE_NAME, USERNAME)


def set_api_key(value):
    keyring.set_password(SERVICE_NAME, USERNAME, value)


def clear_api_key():
    try:
        keyring.delete_password(SERVICE_NAME, USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass
