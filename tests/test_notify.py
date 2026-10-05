from mira.notify import BUNDLE_ID, NullNotifier, make_notifier


def test_null_when_disabled_or_fake():
    assert isinstance(make_notifier(notifications=False, fake=False), NullNotifier)
    assert isinstance(make_notifier(notifications=True, fake=True), NullNotifier)


def test_null_outside_mira_bundle(monkeypatch):
    import mira.notify as notify

    monkeypatch.setattr(notify, "_current_bundle_id", lambda: "com.apple.Terminal")
    assert isinstance(make_notifier(notifications=True, fake=False), NullNotifier)


def test_null_when_bundle_unavailable(monkeypatch):
    import mira.notify as notify

    def boom():
        raise ImportError("no Foundation")

    monkeypatch.setattr(notify, "_current_bundle_id", boom)
    assert isinstance(make_notifier(notifications=True, fake=False), NullNotifier)


def test_builds_mac_notifier_inside_bundle(monkeypatch):
    import mira.notify as notify

    made = {}

    class StubMac:
        def __init__(self, on_click=None):
            made["on_click"] = on_click

    monkeypatch.setattr(notify, "_current_bundle_id", lambda: BUNDLE_ID)
    monkeypatch.setattr(notify, "MacNotifier", StubMac)

    def click():
        pass

    out = make_notifier(notifications=True, fake=False, on_click=click)
    assert isinstance(out, StubMac) and made["on_click"] is click


def test_falls_back_when_mac_notifier_fails(monkeypatch):
    import mira.notify as notify

    def boom(**kw):
        raise RuntimeError("notifications unavailable")

    monkeypatch.setattr(notify, "_current_bundle_id", lambda: BUNDLE_ID)
    monkeypatch.setattr(notify, "MacNotifier", boom)
    assert isinstance(make_notifier(notifications=True, fake=False), NullNotifier)


def test_null_notifier_is_silent():
    NullNotifier().notify("Mira", "在吗")  # 不抛异常就算过
