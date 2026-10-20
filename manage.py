"""CLI to manage the watched-app allowlist: add, list, remove.

    uv run manage.py add
    uv run manage.py list
    uv run manage.py remove "Discord messages"
    uv run manage.py inspect-context

`add` is interactive: it asks you to switch to the target app and type a
couple of words there, detects which process that was, then asks for a
short label. Nothing is watched until you explicitly add it here — see
PLAN.md's non-goals.

`inspect-context` waits a few seconds so you can click into a text box,
then shows what conversation context core/context.py finds there and
saves a structure dump (local only) for tuning the heuristic on that app.
"""

import argparse
import getpass
import sys
import time
from datetime import datetime

from core import api_key as api_key_store
from core import config


def cmd_add(args):
    if sys.platform == "win32":
        from platform_backends import windows as backend
    elif sys.platform == "darwin":
        from platform_backends import macos as backend
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

    domains = None
    if config.is_browser_process(process_name):
        raw_domain = input(
            "Browser detected. Enter the one website host to allow "
            "(for example docs.google.com): "
        )
        domain = config.normalize_domain(raw_domain)
        if domain is None:
            print("Invalid host. Browser was not added; it cannot be enabled for every page.")
            return 1
        domains = [domain]

    label = input("Give this a short label (2-3 words), e.g. "
                   "'Discord messages': ").strip()
    if not label:
        label = process_name

    config.add_app(label=label, process_name=process_name, domains=domains)
    print(f"Added {label!r} ({process_name}) to the watch list.")
    if domains:
        print(
            "On macOS, allow the system Automation prompt so the guard can "
            "read the active tab hostname. No browser extension is used."
        )
    return 0


def cmd_list(args):
    apps = config.list_apps()
    if not apps:
        print("No apps are being watched yet. Run `manage.py add` to add one.")
        return 0
    for app in apps:
        domains = app.get("domains")
        if domains:
            scope = f" @ {', '.join(domains)}"
        elif config.is_browser_process(app["process_name"]):
            scope = " @ host required (inactive)"
        else:
            scope = ""
        print(f"- {app['label']}  ({app['process_name']}){scope}")
    return 0


def cmd_set_key(args):
    service = "Inception (Mercury)" if args.provider == "mercury" else "TypeSafe (Jev)"
    value = getpass.getpass(f"{service} API key (input hidden): ").strip()
    if not value:
        print("No key entered, nothing saved.")
        return 1
    api_key_store.set_api_key(value, provider=args.provider)
    print("Saved to the OS credential store.")
    return 0


def cmd_inspect_context(args):
    from core import context

    if sys.platform == "win32":
        from platform_backends import windows as backend
    elif sys.platform == "darwin":
        from platform_backends import macos as backend
    else:
        print(f"Unsupported platform: {sys.platform}")
        return 1

    for remaining in range(args.delay, 0, -1):
        print(f"Click into the text box you want to inspect... {remaining}", flush=True)
        time.sleep(1)
    control = backend.get_focused_control()
    if control is None:
        print("No focused control found.")
        return 1

    process = backend.get_process_name(backend.get_process_id(control))
    title = backend.get_window_title(control)
    ctx = context.extract(backend, control)
    report = [
        f"process: {process}",
        f"window title: {title}",
        f"writable text box: {backend.is_writable_text_control(control)}",
    ]
    if ctx is None:
        report.append("context: none found")
    else:
        report.append(
            f"context: source={ctx.source} place={ctx.place!r} "
            f"({ctx.nodes_visited} nodes, {ctx.elapsed_ms} ms)"
        )
        report += [f"  message: {message}" for message in ctx.previous_messages]
    print("\n".join(report))

    dump_dir = config.CONFIG_DIR / "context-dumps"
    dump_dir.mkdir(parents=True, exist_ok=True)
    safe_process = "".join(ch for ch in (process or "unknown") if ch.isalnum() or ch in "._-")
    path = dump_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{safe_process}.txt"
    lines = report + ["", "--- tree ---"] + context.describe_tree(backend, control)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nFull dump saved to {path}")
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
    set_key_parser = subparsers.add_parser("set-key", help="Store an API key in the OS credential store")
    set_key_parser.add_argument(
        "--provider", choices=["jev", "mercury"], default="jev",
        help="jev = TypeSafe key (default), mercury = Inception key",
    )
    set_key_parser.set_defaults(func=cmd_set_key)

    inspect_parser = subparsers.add_parser(
        "inspect-context",
        help="Show the conversation context found in a text box and save a tree dump",
    )
    inspect_parser.add_argument(
        "--delay", type=int, default=5, help="Seconds to switch to the text box (default 5)"
    )
    inspect_parser.set_defaults(func=cmd_inspect_context)

    remove_parser = subparsers.add_parser("remove", help="Stop watching an app")
    remove_parser.add_argument("identifier", help="Label or process name to remove")
    remove_parser.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
