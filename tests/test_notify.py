from mira.notify import DockNotifier, NullNotifier, make_notifier


def test_null_when_disabled_or_fake():
    assert isinstance(make_notifier(notifications=False, fake=False), NullNotifier)
    assert isinstance(make_notifier(notifications=True, fake=True), NullNotifier)


def test_dock_notifier_when_enabled():
    assert isinstance(make_notifier(notifications=True, fake=False), DockNotifier)


def test_notify_bounces_and_badges_then_clears(monkeypatch):
    import mira.notify as notify

    calls = []
    monkeypatch.setattr(notify, "_on_main", lambda f: f())
    monkeypatch.setattr(notify, "_dock_attention", lambda: calls.append("attention"))
    monkeypatch.setattr(notify, "_dock_clear_badge", lambda: calls.append("clear"))

    n = DockNotifier()
    n.notify("Mira", "在吗")
    assert calls == ["attention"]
    n.clear()
    assert calls == ["attention", "clear"]
    n.clear()  # 没提醒过就不用清，也不会再调 AppKit
    assert calls == ["attention", "clear"]


def test_notify_failure_is_swallowed(monkeypatch):
    import mira.notify as notify

    def boom(func):
        raise RuntimeError("no GUI")

    monkeypatch.setattr(notify, "_on_main", boom)
    DockNotifier().notify("Mira", "在吗")  # 不抛异常
    DockNotifier().clear()  # 没提醒过，直接返回，也不抛


def test_dock_attention_failure_is_swallowed(monkeypatch):
    import mira.notify as notify

    monkeypatch.setattr(notify, "_on_main", lambda f: f())

    def boom():
        raise RuntimeError("no dock")

    monkeypatch.setattr(notify, "_dock_attention", boom)
    DockNotifier().notify("Mira", "在吗")  # 不抛异常


def test_null_notifier_is_silent():
    NullNotifier().notify("Mira", "在吗")  # 不抛异常就算过
