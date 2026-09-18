"""Tkinter settings window: the GUI equivalent of manage.py's add/list/
remove/set-key commands, for people who'd rather not use a console. Launched
by the tray icon's "Settings..." menu item (see tray_app.py).

Builds and runs its own Tk root + mainloop. On macOS, `settings_app.py`
launches this in a separate process from the Cocoa-based tray application.
"""

import logging
import sys
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog

from core import api_key as api_key_store
from core import config

log = logging.getLogger("jev-send-guard")


def _get_backend():
    if sys.platform == "win32":
        from platform_backends import windows as backend

        return backend
    if sys.platform == "darwin":
        from platform_backends import macos as backend

        return backend
    return None


def open_settings_window(on_change=None):
    """Blocking until the window is closed. `on_change`, if given, is
    called (no args) whenever the watched-app list changes."""
    root = tk.Tk()
    root.title("Jev Send Guard — Settings")
    root.geometry("380x460")
    root.resizable(False, False)

    tk.Label(root, text="Watched apps", font=("Segoe UI", 10, "bold")).pack(
        anchor="w", padx=12, pady=(12, 4)
    )

    list_frame = tk.Frame(root)
    list_frame.pack(fill="both", expand=True, padx=12)
    listbox = tk.Listbox(list_frame, height=8)
    listbox.pack(side="left", fill="both", expand=True)
    scrollbar = tk.Scrollbar(list_frame, command=listbox.yview)
    scrollbar.pack(side="right", fill="y")
    listbox.config(yscrollcommand=scrollbar.set)

    def refresh_list():
        listbox.delete(0, tk.END)
        for app in config.list_apps():
            domains = app.get("domains")
            if domains:
                scope = f" @ {', '.join(domains)}"
            elif config.is_browser_process(app["process_name"]):
                scope = " @ host required (inactive)"
            else:
                scope = ""
            listbox.insert(tk.END, f"{app['label']}  ({app['process_name']}){scope}")

    refresh_list()

    add_status_var = tk.StringVar()

    def do_remove():
        selection = listbox.curselection()
        if not selection:
            return
        app = config.list_apps()[selection[0]]
        if messagebox.askyesno("Remove app", f"Stop watching {app['label']!r}?", parent=root):
            config.remove_app(app["process_name"])
            refresh_list()
            if on_change:
                on_change()

    def do_add():
        backend = _get_backend()
        if backend is None:
            messagebox.showerror("Unsupported", f"Unsupported platform: {sys.platform}", parent=root)
            return

        proceed = messagebox.askokcancel(
            "Add an app",
            "After clicking OK, switch to the window you want to watch and "
            f"type a couple of words there. You'll have {backend.ADD_FLOW_TIMEOUT_SEC}s.",
            parent=root,
        )
        if not proceed:
            return

        add_status_var.set("Waiting for you to type in the target app...")
        root.update_idletasks()

        def detect():
            try:
                return backend.run_add_flow(
                    prompt=lambda _msg: None, output=lambda _msg: None
                )
            except Exception:
                log.exception("add-app detection failed")
                return None

        def finish_detection(detected):
            add_status_var.set("")
            if not detected:
                messagebox.showwarning(
                    "Nothing detected",
                    "Didn't detect any typing in a new window. Try again.",
                    parent=root,
                )
                return
            domains = None
            if config.is_browser_process(detected["process_name"]):
                domain = simpledialog.askstring(
                    "Allow one website",
                    "This is a browser, so it must be limited to one website.\n\n"
                    "Enter an exact host or URL, e.g. docs.google.com:",
                    parent=root,
                )
                normalized_domain = config.normalize_domain(domain)
                if normalized_domain is None:
                    messagebox.showwarning(
                        "Browser not added",
                        "Enter a valid website host. The guard never watches every "
                        "page in a browser.",
                        parent=root,
                    )
                    return
                domains = [normalized_domain]
            label = simpledialog.askstring(
                "Label this app",
                f"Detected process: {detected['process_name']}\n\n"
                "Give it a short label (2-3 words), e.g. 'Discord messages':",
                parent=root,
            )
            if not label:
                label = detected["process_name"]
            config.add_app(
                label=label,
                process_name=detected["process_name"],
                domains=domains,
            )
            if domains:
                messagebox.showinfo(
                    "Browser limited to one website",
                    "The guard is limited to " + domains[0] + ".\n\n"
                    "On macOS, allow the system Automation prompt so the guard can "
                    "read the active tab hostname. No browser extension is used.",
                    parent=root,
                )
            refresh_list()
            if on_change:
                on_change()

        if sys.platform == "darwin":
            # AX queries can return kAXErrorCannotComplete from a Tk worker
            # thread on macOS. Running this small, finite selection flow on
            # Tk's main thread is reliable; the user can still Cmd-Tab to the
            # target app while the Settings window waits.
            root.after(100, lambda: finish_detection(detect()))
            return

        result_holder = {}

        def detect_in_thread():
            result_holder["detected"] = detect()

        thread = threading.Thread(target=detect_in_thread, daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                root.after(200, poll)
                return
            finish_detection(result_holder.get("detected"))

        root.after(200, poll)

    button_row = tk.Frame(root)
    button_row.pack(fill="x", padx=12, pady=6)
    tk.Button(button_row, text="Add app...", command=do_add).pack(side="left")
    tk.Button(button_row, text="Remove selected", command=do_remove).pack(side="left", padx=(8, 0))

    tk.Label(root, textvariable=add_status_var, fg="#5f6368").pack(anchor="w", padx=12)

    tk.Label(root, text="TypeSafe API key", font=("Segoe UI", 10, "bold")).pack(
        anchor="w", padx=12, pady=(16, 4)
    )

    key_frame = tk.Frame(root)
    key_frame.pack(fill="x", padx=12)
    key_entry = tk.Entry(key_frame, show="•")
    key_entry.pack(side="left", fill="x", expand=True)

    existing_key = api_key_store.get_api_key()
    if existing_key:
        key_entry.insert(0, existing_key)

    key_status_var = tk.StringVar()

    def do_save_key():
        value = key_entry.get().strip()
        if not value:
            return
        api_key_store.set_api_key(value)
        key_status_var.set("Saved.")
        root.after(2000, lambda: key_status_var.set(""))

    tk.Button(key_frame, text="Save", command=do_save_key).pack(side="left", padx=(8, 0))
    tk.Label(root, textvariable=key_status_var, fg="#81c995").pack(anchor="w", padx=12)

    tk.Button(root, text="Close", command=root.destroy).pack(pady=16)

    root.mainloop()
