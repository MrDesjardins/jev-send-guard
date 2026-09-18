"""Standalone Settings UI for the tray application.

On macOS this must be a separate process: Tk and the Cocoa event loop used by
pystray cannot safely share one interpreter.
"""

from core.logging_setup import setup_logging

# This helper runs independently from the tray process, so it needs to set up
# the shared diagnostic log before the macOS add-flow backend is imported.
setup_logging()

from core.settings_window import open_settings_window


if __name__ == "__main__":
    open_settings_window()
