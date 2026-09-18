"""Cross-platform "start at login" installer/uninstaller for tray_app.py.

    uv run install/install.py install
    uv run install/install.py uninstall

Windows: registers a Task Scheduler logon task (no admin required) running
pythonw.exe (no console window) against this venv's tray_app.py.
macOS: writes a launchd user agent plist and loads it.

Not run against a real Windows or macOS session yet — see PLAN.md. Check
`schtasks /query /tn JevSendGuardTray` (Windows) or `launchctl list |
grep jevsendguard` (macOS) after installing to confirm it actually
registered, and that the tray icon actually appears after a real logout/
login (not just "no error was printed").
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRAY_APP = REPO_ROOT / "tray_app.py"

WINDOWS_TASK_NAME = "JevSendGuardTray"
MAC_LABEL = "com.jevsendguard.tray"
MAC_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def _windows_pythonw():
    """pythonw.exe (no console window) next to this venv's python.exe, or
    that same python.exe if pythonw.exe isn't present for some reason."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else exe


def install_windows():
    pythonw = _windows_pythonw()
    command = f'"{pythonw}" "{TRAY_APP}"'
    subprocess.run(
        [
            "schtasks", "/create", "/f",
            "/tn", WINDOWS_TASK_NAME,
            "/tr", command,
            "/sc", "onlogon",
        ],
        check=True,
    )
    print(f"Installed: {WINDOWS_TASK_NAME!r} will run at login.")
    print("Starting it now...")
    subprocess.run(["schtasks", "/run", "/tn", WINDOWS_TASK_NAME], check=False)


def uninstall_windows():
    subprocess.run(["schtasks", "/delete", "/f", "/tn", WINDOWS_TASK_NAME], check=False)
    print(f"Removed: {WINDOWS_TASK_NAME!r}.")


def install_macos():
    log_dir = Path.home() / ".jev-send-guard"
    log_dir.mkdir(parents=True, exist_ok=True)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{MAC_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{TRAY_APP}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{log_dir / "tray_stdout.log"}</string>
    <key>StandardErrorPath</key>
    <string>{log_dir / "tray_stderr.log"}</string>
</dict>
</plist>
"""
    MAC_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAC_PLIST_PATH.write_text(plist)
    subprocess.run(["launchctl", "unload", str(MAC_PLIST_PATH)], check=False)
    subprocess.run(["launchctl", "load", "-w", str(MAC_PLIST_PATH)], check=True)
    print(f"Installed: {MAC_PLIST_PATH} loaded, will run at login.")


def uninstall_macos():
    subprocess.run(["launchctl", "unload", str(MAC_PLIST_PATH)], check=False)
    if MAC_PLIST_PATH.exists():
        MAC_PLIST_PATH.unlink()
    print(f"Removed: {MAC_PLIST_PATH}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    args = parser.parse_args()

    if sys.platform == "win32":
        (install_windows if args.action == "install" else uninstall_windows)()
    elif sys.platform == "darwin":
        (install_macos if args.action == "install" else uninstall_macos)()
    else:
        print(f"Unsupported platform: {sys.platform}")
        sys.exit(1)


if __name__ == "__main__":
    main()
