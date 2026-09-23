from core import notifier


def test_macos_replacement_waits_for_the_old_helper_to_exit(monkeypatch):
    class PreviousHelper:
        def __init__(self):
            self.terminated = False
            self.wait_timeouts = []

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)

    class NewHelper:
        pid = 12345

    previous = PreviousHelper()
    new = NewHelper()
    monkeypatch.setattr(notifier, "_popup_process", previous)
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_args, **_kwargs: new)

    notifier._show_macos_panel("results", [], None, 6000)

    assert previous.terminated is True
    assert previous.wait_timeouts == [0.25]
    assert notifier._popup_process is new
