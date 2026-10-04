import http.server
import logging
import socket
import threading

import uvicorn

from mira.config import load_settings
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
