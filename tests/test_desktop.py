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
    assert f"日志在：{tmp_path / 'logs' / 'mira.log'}" in no_gui[-1]["html_page"]


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
