from core.idle_watcher import IdleWatcher


def test_does_not_fire_before_idle_window_elapses():
    t = [0.0]
    w = IdleWatcher(idle_seconds=1.0, now=lambda: t[0])
    w.observe("h")
    assert w.due() is None


def test_fires_once_idle_window_elapses():
    t = [0.0]
    w = IdleWatcher(idle_seconds=1.0, now=lambda: t[0])
    w.observe("hello")
    t[0] = 1.1
    assert w.due() == "hello"


def test_does_not_refire_same_text():
    t = [0.0]
    w = IdleWatcher(idle_seconds=1.0, now=lambda: t[0])
    w.observe("hello")
    t[0] = 1.1
    assert w.due() == "hello"
    t[0] = 2.2
    assert w.due() is None


def test_new_keystroke_resets_the_window():
    t = [0.0]
    w = IdleWatcher(idle_seconds=1.0, now=lambda: t[0])
    w.observe("h")
    t[0] = 0.5
    w.observe("he")
    assert w.due() is None
    t[0] = 1.6  # 1.1s since the 0.5s keystroke, past the 1.0s window
    assert w.due() == "he"


def test_reset_clears_state_so_refocus_does_not_fire_on_stale_text():
    t = [0.0]
    w = IdleWatcher(idle_seconds=1.0, now=lambda: t[0])
    w.observe("hello")
    t[0] = 1.1
    assert w.due() == "hello"
    w.reset()
    assert w.due() is None


def test_initial_none_observation_never_becomes_due():
    # observe(None) doesn't count as a "change" from the initial None state,
    # so _last_change_at never gets set and due() can never fire from it.
    t = [0.0]
    w = IdleWatcher(idle_seconds=1.0, now=lambda: t[0])
    w.observe(None)
    t[0] = 1.1
    assert w.due() is None
