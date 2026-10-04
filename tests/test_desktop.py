import http.server
import logging
import socket
import threading

import pytest
import uvicorn

import mira.desktop as desktop
from mira.config import ConfigError, load_settings
from mira.desktop import ServerThread, message_page, probe_port, setup_logging
from mira.main import create_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_settings(tmp_path, **extra):
    env = {
        "MIRA_FAKE": "1",
        "DB_PATH": str(tmp_path / "dev.db"),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "THEME_DIR": str(tmp_path / "theme"),
        "PORT": str(free_port()),
    }
    env.update(extra)
    return load_settings(env)


def test_probe_free_port():
    assert probe_port("127.0.0.1", free_port()) == "free"


def test_probe_detects_running_mira(tmp_path):
    settings = fake_settings(tmp_path)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=settings.port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(150):
            if server.started:
                break
            threading.Event().wait(0.1)
        assert server.started
        assert probe_port("127.0.0.1", settings.port) == "mira"
    finally:
        server.should_exit = True
        t.join(10)
    assert not t.is_alive()


def test_probe_other_program():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_error(404)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        assert probe_port("127.0.0.1", httpd.server_address[1]) == "other"
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(5)


def test_probe_rejects_json_string_state():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'"state"'  # JSON 字符串里也“包含” state，但不是 Mira
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        assert probe_port("127.0.0.1", httpd.server_address[1]) == "other"
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(5)


def test_message_page_has_drag_strip():
    # 加载页、出错页也能拖动窗口（标题栏已合进页面）
    page = message_page("Mira 正在醒来…", "稍等")
    assert "pywebview-drag-region" in page and "padding-top" in page


def test_message_page_escapes_html():
    assert "<script>" not in message_page("<script>", "a & b") and "&amp;" in message_page("x", "a & b")


def test_server_thread_reports_chinese_error_on_bad_persona(tmp_path):
    settings = fake_settings(tmp_path, PERSONA_PATH=str(tmp_path / "missing.md"))
    thread = ServerThread(settings)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()
    assert not thread.ready.is_set()
    assert thread.error and "启动失败" in thread.error
    assert f"找不到文件：{tmp_path / 'missing.md'}" in thread.error  # 不是英文的 Errno 原文


def test_server_thread_starts_and_stops(tmp_path, caplog):
    settings = fake_settings(tmp_path)
    thread = ServerThread(settings)
    thread.start()
    try:
        assert thread.ready.wait(15)
        assert thread.error is None
        assert probe_port("127.0.0.1", settings.port) == "mira"
    finally:
        thread.stop()
    assert not thread.is_alive()
    assert probe_port("127.0.0.1", settings.port) == "free"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_server_thread_file_not_found_without_filename(tmp_path, monkeypatch):
    import mira.main

    def boom(settings):
        raise FileNotFoundError("缺了什么")

    monkeypatch.setattr(mira.main, "create_app", boom)
    thread = ServerThread(fake_settings(tmp_path))
    thread.start()
    thread.join(10)
    assert thread.error == "启动失败：找不到需要的文件"  # 不会出现“找不到文件：None”


def test_setup_logging_is_idempotent(tmp_path):
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    before = list(root.handlers)
    log_file = tmp_path / "logs" / "mira.log"
    try:
        setup_logging(log_file)
        setup_logging(log_file)
        added = [h for h in root.handlers if h not in before and isinstance(h, RotatingFileHandler)]
        assert len(added) == 1
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_setup_logging_writes_file(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    log_file = tmp_path / "logs" / "mira.log"
    try:
        setup_logging(log_file)
        logging.getLogger("mira.test").warning("你好 desktop log")
        for h in root.handlers:
            h.flush()
        assert log_file.exists()
        assert "你好 desktop log" in log_file.read_text(encoding="utf-8")
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


class FakeWindow:
    def __init__(self):
        self.events = type("E", (), {"closed": threading.Event()})()
        self.html = None

    def load_html(self, page):
        self.html = page


class FakeServer:
    def __init__(self, error):
        self.ready = threading.Event()
        self.error = error


def test_watch_error_page_shows_log_path(tmp_path):
    window = FakeWindow()
    log_file = tmp_path / "日志 目录" / "mira.log"
    desktop._watch(window, FakeServer("启动失败：坏了 <b>"), "http://x/", tmp_path / "none.png", log_file)
    assert "Mira 没能启动" in window.html and "启动失败：坏了 &lt;b&gt;" in window.html
    assert f"日志在：{log_file}" in window.html


@pytest.fixture
def no_gui(monkeypatch):
    """main() 里不开真窗口、不写真日志，记下每次 _open_window 的参数。"""
    calls = []
    monkeypatch.setattr(desktop, "_open_window", lambda **kw: calls.append(kw))
    monkeypatch.setattr(desktop, "setup_logging", lambda path: calls.append({"log": path}))
    return calls


def test_config_error_page_shows_log_path(no_gui, monkeypatch):
    def bad():
        raise ConfigError("PORT 必须是数字")

    monkeypatch.setattr(desktop, "load_settings", bad)
    desktop.main()
    log_file = desktop.PROJECT_ROOT / "data" / "logs" / "mira.log"
    page = no_gui[-1]["html_page"]
    assert "PORT 必须是数字" in page and f"日志在：{log_file}" in page


def test_port_taken_page_shows_log_path(tmp_path, no_gui, monkeypatch):
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(desktop, "load_settings", lambda: settings)
    monkeypatch.setattr(desktop, "probe_port", lambda h, p: "other")
    desktop.main()
    log_file = desktop.PROJECT_ROOT / "data" / "logs" / "mira.log"  # 和启动器提示、README 说的位置一致
    assert no_gui[0] == {"log": log_file}
    assert f"日志在：{log_file}" in no_gui[-1]["html_page"]


def test_log_location_same_for_custom_db_path(tmp_path, no_gui, monkeypatch):
    settings = fake_settings(tmp_path, DB_PATH=str(tmp_path / "别处" / "x.db"))
    monkeypatch.setattr(desktop, "load_settings", lambda: settings)
    monkeypatch.setattr(desktop, "probe_port", lambda h, p: "mira")
    desktop.main()
    assert no_gui[0] == {"log": desktop.PROJECT_ROOT / "data" / "logs" / "mira.log"}


def test_main_returns_normally_when_window_closed(tmp_path, no_gui, monkeypatch):
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(desktop, "load_settings", lambda: settings)
    monkeypatch.setattr(desktop, "probe_port", lambda h, p: "mira")
    desktop.main()  # 关窗口 = webview.start() 返回，不能抛 SystemExit，进程退出码才是 0
    assert no_gui[-1]["url"] == f"http://127.0.0.1:{settings.port}/"


def test_main_logs_crash_and_exits_nonzero(tmp_path, no_gui, monkeypatch, caplog):
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(desktop, "load_settings", lambda: settings)
    monkeypatch.setattr(desktop, "probe_port", lambda h, p: "mira")

    def boom(**kw):
        raise ImportError("No module named 'webview'")

    monkeypatch.setattr(desktop, "_open_window", boom)
    with pytest.raises(SystemExit) as e:
        desktop.main()
    assert e.value.code == 1
    rec = [r for r in caplog.records if r.levelno >= logging.ERROR][-1]
    assert rec.exc_info and "webview" in str(rec.exc_info[1])


def test_main_exits_nonzero_when_logging_setup_fails(tmp_path, monkeypatch, caplog):
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(desktop, "load_settings", lambda: settings)

    def no_perm(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(desktop, "setup_logging", no_perm)
    monkeypatch.setattr(desktop, "_open_window", lambda **kw: pytest.fail("不该开窗口"))
    with pytest.raises(SystemExit) as e:
        desktop.main()
    assert e.value.code == 1
    assert any(r.exc_info for r in caplog.records)  # 没有日志文件时 log.exception 会落到 stderr


class FakeView:
    def __init__(self, name, color_settable=True):
        self.name = name
        self.color = "white"
        self.frame = None
        self.children = []
        self.parent = None
        self.color_settable = color_settable

    def isEqual_(self, other):
        return self is other

    def respondsToSelector_(self, sel):
        return sel == "setBackgroundColor:" and self.color_settable

    def setBackgroundColor_(self, color):
        self.color = color

    def superview(self):
        return self.parent

    def subviews(self):
        return self.children

    def setFrame_(self, rect):
        self.frame = rect


class FakeNSWindow:
    def __init__(self):
        from AppKit import NSMakeRect

        self.mask = 15
        self.transparent = False
        self.title_visibility = 0
        self.content = FakeView("content", color_settable=False)
        self.titlebar = FakeView("titlebar")
        theme = FakeView("theme")
        theme.children = [self.content, self.titlebar]
        self.content.parent = theme
        self._frame = NSMakeRect(0, 0, 1280, 860)

    def styleMask(self):
        return self.mask

    def setStyleMask_(self, mask):
        self.mask = mask

    def setTitlebarAppearsTransparent_(self, value):
        self.transparent = value

    def setTitleVisibility_(self, value):
        self.title_visibility = value

    def contentView(self):
        return self.content

    def frame(self):
        return self._frame

    def contentRectForFrameRect_(self, rect):
        return rect


def test_window_chrome_makes_titlebar_transparent_and_fills_window():
    from AppKit import NSColor, NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden

    native = FakeNSWindow()
    assert desktop._apply_window_chrome(native)
    assert native.mask & NSWindowStyleMaskFullSizeContentView and native.mask & 15 == 15  # 原来的按钮、可缩放都保留
    assert native.transparent and native.title_visibility == NSWindowTitleHidden
    assert native.titlebar.color == NSColor.clearColor() and native.content.color == "white"
    assert (native.content.frame.size.width, native.content.frame.size.height) == (1280, 860)


def test_window_chrome_failure_only_logs(caplog):
    class Broken:
        def styleMask(self):
            raise RuntimeError("坏了")

    with caplog.at_level(logging.WARNING, logger="mira.desktop"):
        assert desktop._apply_window_chrome(Broken()) is False
    assert "标题栏" in caplog.text


def test_install_window_chrome_registers_handlers(monkeypatch):
    applied = []
    monkeypatch.setattr(desktop, "_apply_window_chrome", lambda native: applied.append(native))

    class Hook:
        def __init__(self):
            self.items = []

        def __iadd__(self, fn):
            self.items.append(fn)
            return self

    window = type("W", (), {})()
    window.native = "ns"
    window.events = type("E", (), {})()
    window.events.before_show, window.events.loaded = Hook(), Hook()
    desktop._install_window_chrome(window)
    assert len(window.events.before_show.items) == 1 and len(window.events.loaded.items) == 1
    window.events.before_show.items[0]()  # 测试跑在主线程上，直接生效
    assert applied == ["ns"]


def make_hooked_window(native="ns"):
    class Hook:
        def __init__(self):
            self.items = []

        def __iadd__(self, fn):
            self.items.append(fn)
            return self

    window = type("W", (), {})()
    window.native = native
    window.events = type("E", (), {})()
    window.events.before_show, window.events.loaded = Hook(), Hook()
    return window


def test_loaded_handler_applies_chrome_on_main_thread(monkeypatch):
    from PyObjCTools import AppHelper

    applied = []
    monkeypatch.setattr(desktop, "_apply_window_chrome", lambda native: applied.append(native))
    monkeypatch.setattr(AppHelper, "callAfter", lambda fn, *a, **kw: fn(*a, **kw))  # 不进事件循环，直接调用
    window = make_hooked_window()
    desktop._install_window_chrome(window)
    window.events.loaded.items[0]()
    assert applied == ["ns"]


def test_chrome_skipped_when_native_window_missing(monkeypatch):
    from PyObjCTools import AppHelper

    applied = []
    monkeypatch.setattr(desktop, "_apply_window_chrome", lambda native: applied.append(native))
    monkeypatch.setattr(AppHelper, "callAfter", lambda fn, *a, **kw: fn(*a, **kw))
    window = make_hooked_window(native=None)
    desktop._install_window_chrome(window)
    window.events.before_show.items[0]()
    window.events.loaded.items[0]()
    assert applied == []


class SignalWindow:
    """假窗口：shown 是真的 threading.Event，destroy 只记次数。"""

    def __init__(self):
        self.shown = threading.Event()
        self.on_shown = []
        self.destroyed = 0

        class Shown:
            def __iadd__(hook, fn):
                self.on_shown.append(fn)
                return hook

            def is_set(hook):
                return self.shown.is_set()

            def wait(hook, timeout=None):
                return self.shown.wait(timeout)

        self.events = type("E", (), {})()
        self.events.shown = Shown()

    def show(self, between=None):
        """和 pywebview 一样：先在另一个线程里跑 shown 钩子，再把 shown 设为已发生。
        between 在钩子跑过之后、shown 设好之前调用，用来模拟这中间到达的信号。"""
        t = threading.Thread(target=lambda: [fn() for fn in self.on_shown])
        t.start()
        t.join(0.2)
        if between:
            between()
        self.shown.set()
        t.join(5)
        assert not t.is_alive()

    def destroy(self):
        assert self.shown.is_set(), "窗口还没显示时 destroy 会卡 20 秒再报错"
        self.destroyed += 1


def test_signal_after_shown_closes_window():
    window = SignalWindow()
    handle = desktop._signal_handler(window)
    window.show()
    handle(15)
    assert window.destroyed == 1


def test_signal_before_shown_closes_once_shown():
    window = SignalWindow()
    handle = desktop._signal_handler(window)
    handle(15)  # 不能卡住，也不能抛异常
    assert window.destroyed == 0
    window.show()
    assert window.destroyed == 1
    handle(2)  # 已经在关了，不重复关
    assert window.destroyed == 1


def test_signal_between_shown_hooks_and_event_still_closes():
    window = SignalWindow()
    handle = desktop._signal_handler(window)
    window.show(between=lambda: handle(15))
    assert window.destroyed == 1


def test_window_shown_without_signal_stays_open():
    window = SignalWindow()
    desktop._signal_handler(window)
    window.show()
    assert window.destroyed == 0


@pytest.fixture
def fake_cocoa(monkeypatch):
    """不碰真的信号和通知中心：记下注册了什么。"""
    import Foundation
    from PyObjCTools import AppHelper, MachSignals

    got = {"signals": {}, "observers": []}
    monkeypatch.setattr(MachSignals, "signal", lambda sig, fn: got["signals"].__setitem__(sig, fn))
    monkeypatch.setattr(AppHelper, "callAfter", lambda fn, *a, **kw: fn(*a, **kw))

    class Center:
        @staticmethod
        def defaultCenter():
            return Center()

        def addObserver_selector_name_object_(self, obs, sel, name, obj):
            got["observers"].append((obs, sel, name))

    monkeypatch.setattr(Foundation, "NSNotificationCenter", Center)
    monkeypatch.setattr(desktop, "_observers", [])
    return got


class StopRecorder:
    def __init__(self):
        self.timeouts = []

    def stop(self, timeout=10):
        self.timeouts.append(timeout)


def test_quit_observer_stops_server_quickly(fake_cocoa):
    server = StopRecorder()
    desktop._on_quit(SignalWindow(), server)
    obs, sel, name = fake_cocoa["observers"][-1]
    assert name == "NSApplicationWillTerminateNotification"
    obs.appWillTerminate_(None)
    assert server.timeouts == [3]  # ⌘Q 时最多等 3 秒，别让界面卡住


def test_sigterm_and_sigint_share_handler(fake_cocoa):
    import signal

    window = SignalWindow()
    desktop._on_quit(window, None)
    sigs = fake_cocoa["signals"]
    assert set(sigs) == {signal.SIGTERM, signal.SIGINT}
    assert sigs[signal.SIGTERM] is sigs[signal.SIGINT]
    fake_cocoa["signals"].clear()
    window.show()  # pywebview 启动事件循环前会换掉 Ctrl+C 的处理，显示后要再装一次
    handler = sigs.get(signal.SIGINT)
    assert handler is not None
    handler(signal.SIGINT)
    assert window.destroyed == 1
