"""CLI to manage the watched-app allowlist: add, list, remove.

    uv run manage.py add
    uv run manage.py list
    uv run manage.py remove "Discord messages"

`add` is interactive: it asks you to switch to the target app and type a
couple of words there, detects which process that was, then asks for a
short label. Nothing is watched until you explicitly add it here — see
PLAN.md's non-goals.
"""

import argparse
import getpass
import sys

from core import api_key as api_key_store
from core import config


def cmd_add(args):
    if sys.platform == "win32":
        from platform_backends import windows as backend
    elif sys.platform == "darwin":
        print("macOS support isn't wired up yet.")
        return 1
    else:
        print(f"Unsupported platform: {sys.platform}")
        return 1

    detected = backend.run_add_flow()
    if detected is None:
        print(f"Didn't detect any typing in a new window within "
              f"{backend.ADD_FLOW_TIMEOUT_SEC}s. Try again.")
        return 1

    process_name = detected["process_name"]
    hint = detected["control_name_hint"]
    print(f"\nDetected: process={process_name!r}"
          + (f", field={hint!r}" if hint else ""))

    label = input("Give this a short label (2-3 words), e.g. "
                   "'Discord messages': ").strip()
    if not label:
        label = process_name

    config.add_app(label=label, process_name=process_name)
    print(f"Added {label!r} ({process_name}) to the watch list.")
    return 0


def cmd_list(args):
    apps = config.list_apps()
    if not apps:
        print("No apps are being watched yet. Run `manage.py add` to add one.")
        return 0
    for app in apps:
        print(f"- {app['label']}  ({app['process_name']})")
    return 0


def cmd_set_key(args):
    value = getpass.getpass("TypeSafe API key (input hidden): ").strip()
    if not value:
        print("No key entered, nothing saved.")
        return 1
    api_key_store.set_api_key(value)
    print("Saved to the OS credential store.")
    return 0


def cmd_remove(args):
    removed = config.remove_app(args.identifier)
    if removed:
        print(f"Removed {removed} matching entry(ies) for {args.identifier!r}.")
    else:
        print(f"No watched app matched {args.identifier!r}.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("add", help="Register a new app to watch").set_defaults(func=cmd_add)
    subparsers.add_parser("list", help="List watched apps").set_defaults(func=cmd_list)
    subparsers.add_parser("set-key", help="Store the TypeSafe API key in the OS credential store").set_defaults(func=cmd_set_key)

    remove_parser = subparsers.add_parser("remove", help="Stop watching an app")
    remove_parser.add_argument("identifier", help="Label or process name to remove")
    remove_parser.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
