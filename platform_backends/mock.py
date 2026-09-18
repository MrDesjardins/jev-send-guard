"""Fake backend implementing the same interface as windows.py/macos.py, so
core/watch_loop.py's decision logic (allowlist matching, idle debounce,
browser-host gating, password/read-only skipping) can be exercised in
tests without a live GUI or accessibility API. Not used by any real
entrypoint — only tests import this.
"""

ADD_FLOW_TIMEOUT_SEC = 15


class MockControl:
    def __init__(
        self,
        process_name,
        text="",
        is_password=False,
        is_writable=True,
        bounding_rect=None,
        runtime_id=None,
        pid=1234,
    ):
        self.process_name = process_name
        self.text = text
        self.is_password = is_password
        self.is_writable = is_writable
        self.bounding_rect = bounding_rect
        self.pid = pid
        self.runtime_id = runtime_id if runtime_id is not None else (pid, id(self))


class MockBackend:
    """Scriptable backend: tests set `.focused_control` (or None, for "focus
    left every watched app") and `.browser_hosts` between polls."""

    def __init__(self):
        self.focused_control = None
        self.browser_hosts = {}  # process_name -> hostname

    def get_focused_control(self):
        return self.focused_control

    def safe_runtime_id(self, control):
        return control.runtime_id if control else None

    def get_process_id(self, control):
        return control.pid if control else None

    def get_process_name(self, pid):
        control = self.focused_control
        if control is not None and control.pid == pid:
            return control.process_name
        return None

    def is_password_field(self, control):
        return bool(control and control.is_password)

    def is_writable_text_control(self, control):
        return bool(control is None or control.is_writable)

    def get_control_text(self, control):
        return control.text if control else None

    def get_bounding_rect(self, control):
        return control.bounding_rect if control else None

    def get_active_browser_host(self, process_name):
        return self.browser_hosts.get(process_name)

    def run_add_flow(self, prompt=input, output=print):
        return None
