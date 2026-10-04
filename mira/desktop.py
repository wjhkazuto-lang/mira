"""桌面窗口版 Mira：uv run python -m mira.desktop（关掉窗口，Mira 就停下）"""

import html
import json
import logging
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Literal

import uvicorn

from mira.__main__ import port_in_use, setup_logging
from mira.config import PROJECT_ROOT, ConfigError, Settings, load_settings
from mira.embedder import EmbedderLoadError

__all__ = ["LOADING_PAGE", "ServerThread", "main", "message_page", "probe_port", "setup_logging"]

log = logging.getLogger("mira.desktop")  # 用 -m 运行时 __name__ 是 __main__


def probe_port(host: str, port: int) -> Literal["free", "mira", "other"]:
    """端口空着 / 已经有一个 Mira 在跑 / 被别的程序占了。"""
    if not port_in_use(host, port):
        return "free"
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/memory-status", timeout=2) as resp:
            body = json.loads(resp.read())
            if resp.status == 200 and isinstance(body, dict) and "state" in body:
                return "mira"
    except Exception:
        pass
    return "other"


_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Mira</title>
<style>
:root {{ --bg: #f5f2e9; --text: #39414a; --muted: #8a9096; --gold: #c2a974; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #131a25; --text: #e5e8ec; --muted: #8e98a6; --gold: #cdb57d; color-scheme: dark; }}
}}
html, body {{ height: 100%; margin: 0; }}
body {{ display: flex; align-items: center; justify-content: center; background: var(--bg); color: var(--text);
  box-sizing: border-box; padding-top: 28px;  /* 桌面窗口的红黄绿按钮浮在顶上，内容往下让 */
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }}
main {{ max-width: 32em; padding: 24px; text-align: center; }}
h1 {{ font-family: "Songti SC", "STSong", "Noto Serif SC", Georgia, serif; font-weight: normal; color: var(--gold); }}
p {{ color: var(--muted); line-height: 1.7; white-space: pre-wrap; }}
</style></head>
<body><div class="pywebview-drag-region" style="position:fixed;top:0;left:0;right:0;height:28px"></div><main><h1>{title}</h1><p>{body}</p></main></body></html>
"""


def message_page(title: str, body: str) -> str:
    return _PAGE.format(title=html.escape(title), body=html.escape(body))


def error_page(body: str, log_file: Path) -> str:
    return message_page("Mira 没能启动", f"{body}\n日志在：{log_file}")


LOADING_PAGE = message_page("Mira 正在醒来…", "第一次启动需要下载约 90MB 的模型，请稍等")


class _Server(uvicorn.Server):
    """真正开始监听端口后再通知窗口。"""

    def __init__(self, config: uvicorn.Config, ready: threading.Event):
        super().__init__(config)
        self._ready = ready

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets)
        if self.started:
            self._ready.set()


class ServerThread(threading.Thread):
    """在后台线程里跑 Mira 服务。不在主线程，uvicorn 不会接管信号，靠 stop() 停下。"""

    def __init__(self, settings: Settings):
        super().__init__(name="mira-server", daemon=True)
        self.settings = settings
        self.ready = threading.Event()
        self.error: str | None = None
        self._server: _Server | None = None
        self._stopping = False

    def run(self) -> None:
        try:
            from mira.main import create_app

            app = create_app(self.settings)  # 第一次会下载模型，可能要好几分钟
            config = uvicorn.Config(
                app, host=self.settings.host, port=self.settings.port,
                log_level="warning", log_config=None, access_log=False,  # 日志交给根 logger，进同一个文件
            )
            self._server = _Server(config, self.ready)
            if self._stopping:  # 模型还没加载完窗口就关了
                return
            self._server.run()
        except EmbedderLoadError as e:
            log.exception("模型加载失败")
            self.error = str(e)
        except FileNotFoundError as e:
            log.exception("服务启动失败")
            self.error = f"启动失败：找不到文件：{e.filename}" if e.filename else "启动失败：找不到需要的文件"
        except SystemExit:  # uvicorn 绑定端口失败时会 sys.exit
            log.exception("服务没能启动")
            self.error = f"启动失败：服务没能在端口 {self.settings.port} 上开始运行，详情见日志。"
        except Exception as e:
            log.exception("服务启动失败")
            self.error = f"启动失败：{e}"
        if not self.ready.is_set() and self.error is None and not self._stopping:
            self.error = "启动失败：服务意外停止了，详情见日志。"

    def stop(self, timeout: float = 10) -> None:
        self._stopping = True
        if self._server is not None:
            self._server.should_exit = True
        if self.is_alive():
            self.join(timeout)


def _port_taken_text(port: int) -> str:
    return (
        f"端口 {port} 已经被别的程序占用了。\n"
        "可以在项目根目录的 .env 文件里加一行 PORT=8002（换成任意一个没被占用的端口），"
        "保存后重新打开 Mira。"
    )


def _set_app_name() -> None:
    """让 Dock 和菜单栏显示 Mira 而不是 Python。"""
    try:
        from Foundation import NSBundle

        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Mira"
    except Exception:
        log.warning("设置应用名字失败", exc_info=True)


def _set_dock_icon(path: Path) -> None:
    if not path.exists():
        return
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSData
        from PyObjCTools import AppHelper

        from mira.icon import rounded_icon_png

        image = None
        with tempfile.TemporaryDirectory() as tmp:
            rounded = Path(tmp) / "icon.png"
            if rounded_icon_png(path, rounded):  # 圆角留白；失败就用原图
                image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(
                    rounded.read_bytes(), rounded.stat().st_size))  # 读进内存，临时文件随后会删
        if image is None:
            image = NSImage.alloc().initWithContentsOfFile_(str(path))
        if image is not None:
            AppHelper.callAfter(NSApplication.sharedApplication().setApplicationIconImage_, image)
    except Exception:
        log.warning("设置 Dock 图标失败", exc_info=True)


def _apply_window_chrome(native) -> bool:
    """标题栏变透明、页面铺到窗口最顶上，红黄绿按钮浮在页面上。失败只记日志，不影响启动。"""
    try:
        from AppKit import NSColor, NSMakeRect, NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden

        native.setStyleMask_(native.styleMask() | NSWindowStyleMaskFullSizeContentView)
        native.setTitlebarAppearsTransparent_(True)
        native.setTitleVisibility_(NSWindowTitleHidden)  # 标题还是 Mira，只是不画出来（调度中心、窗口菜单仍会用）
        content = native.contentView()
        frame_view = content.superview() if content is not None else None
        if frame_view is not None:
            # pywebview 给标题栏涂了底色，不清掉会留下一条色带
            for view in frame_view.subviews():
                if not view.isEqual_(content) and view.respondsToSelector_("setBackgroundColor:"):
                    view.setBackgroundColor_(NSColor.clearColor())
            # 网页视图要铺满整个窗口（包括原来标题栏那一块）
            full = native.contentRectForFrameRect_(native.frame())
            content.setFrame_(NSMakeRect(0, 0, full.size.width, full.size.height))
        return True
    except Exception:
        log.warning("设置窗口标题栏样式失败", exc_info=True)
        return False


def _install_window_chrome(window) -> None:
    """窗口显示前先设一次；每次页面加载完再设一次（第一次加载时 pywebview 才把网页视图放进窗口）。"""
    try:
        from Foundation import NSThread
        from PyObjCTools import AppHelper

        def apply() -> None:
            if window.native is not None:
                _apply_window_chrome(window.native)

        def before_show() -> None:
            if NSThread.isMainThread():
                apply()  # 在主线程上就直接设，免得先闪一下白色标题栏
            else:
                AppHelper.callAfter(apply)

        def loaded() -> None:
            AppHelper.callAfter(apply)  # 窗口只能在主线程上改

        window.events.before_show += before_show
        window.events.loaded += loaded
    except Exception:
        log.warning("注册窗口标题栏样式失败", exc_info=True)


_observers: list = []  # 留住引用，免得被回收
_quit_observer_class = None  # Objective-C 类只能定义一次


def _quit_observer(server: ServerThread):
    global _quit_observer_class
    if _quit_observer_class is None:
        from Foundation import NSObject

        class QuitObserver(NSObject):
            def appWillTerminate_(self, _note):
                log.info("退出 Mira（⌘Q）")
                self.server.stop(timeout=3)  # 只等 3 秒，免得点了退出界面还卡着

        _quit_observer_class = QuitObserver
    observer = _quit_observer_class.alloc().init()
    observer.server = server
    return observer


def _signal_handler(window):
    """kill（SIGTERM）或终端里 Ctrl+C（SIGINT）时关掉窗口。
    窗口还没显示时 destroy 会先卡 20 秒再报错，所以只记下来，等显示了马上关。"""
    lock = threading.Lock()
    state = {"wanted": False, "closed": False}

    def close() -> None:
        with lock:
            if state["closed"]:
                return
            state["closed"] = True
        window.destroy()

    def on_shown() -> None:
        # pywebview 先在另一个线程跑钩子、再标记“已显示”；等它标好再看，免得中间来的信号两边都漏掉
        window.events.shown.wait(5)
        if state["wanted"]:
            close()

    def handle(_signum) -> None:
        state["wanted"] = True
        if window.events.shown.is_set():
            close()
        else:
            log.info("窗口还没显示，显示后马上关掉")

    window.events.shown += on_shown
    return handle


def _on_quit(window, server: ServerThread | None) -> None:
    """⌘Q 会直接结束进程、webview.start() 不会返回，所以在退出通知里先把服务停好；
    kill（SIGTERM）或 Ctrl+C 时关掉窗口，走正常的收尾流程。"""
    try:
        import signal

        from Foundation import NSNotificationCenter
        from PyObjCTools import AppHelper, MachSignals

        handle = _signal_handler(window)
        MachSignals.signal(signal.SIGTERM, handle)
        MachSignals.signal(signal.SIGINT, handle)
        # pywebview 开始事件循环前会把 Ctrl+C 换成它自己的处理，等事件循环跑起来再装回来
        window.events.shown += lambda: AppHelper.callAfter(MachSignals.signal, signal.SIGINT, handle)
        if server is None:
            return

        observer = _quit_observer(server)
        _observers.append(observer)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            observer, "appWillTerminate:", "NSApplicationWillTerminateNotification", None
        )
    except Exception:
        log.warning("注册退出处理失败", exc_info=True)


def _watch(window, server: ServerThread | None, url: str, icon: Path, log_file: Path) -> None:
    """在 pywebview 的线程里跑：等服务就绪（或出错）后切换页面。"""
    _set_dock_icon(icon)
    if server is None:
        return
    while not window.events.closed.is_set():
        if server.ready.wait(0.2):
            window.load_url(url)
            return
        if server.error is not None:
            window.load_html(error_page(server.error, log_file))
            return


def _open_window(log_file: Path, html_page: str | None = None, url: str = "", server: ServerThread | None = None,
                 icon: Path | None = None) -> None:
    """有 html_page 就先显示它（url 是服务就绪后要切过去的地址），否则直接打开 url。"""
    import webview

    window = webview.create_window(
        "Mira", url=None if html_page else url, html=html_page, width=1280, height=860, min_size=(420, 600),
    )
    _set_app_name()
    _install_window_chrome(window)
    _on_quit(window, server)
    icon = icon or PROJECT_ROOT / "theme" / "mira" / "avatar.png"
    webview.start(func=_watch, args=(window, server, url, icon, log_file))


def main() -> None:
    """正常关窗口时返回（退出码 0）；出了意外就记进日志并以 1 退出，好让 Mira.app 弹出提示。"""
    try:
        _run()
    except Exception:
        log.exception("Mira 意外退出")  # 日志文件还没配好时，这条会打到终端
        sys.exit(1)


def _run() -> None:
    log_file = PROJECT_ROOT / "data" / "logs" / "mira.log"  # 固定在这里，和启动器提示、README 一致
    try:
        settings = load_settings()
    except ConfigError as e:
        setup_logging(log_file)
        log.error("配置出错：%s", e)
        _open_window(html_page=error_page(str(e), log_file), log_file=log_file)
        return
    setup_logging(log_file)
    icon = settings.theme_dir / "mira" / "avatar.png"
    url = f"http://{settings.host}:{settings.port}/"
    state = probe_port(settings.host, settings.port)
    log.info("桌面版启动，端口 %s 状态：%s", settings.port, state)
    if state == "other":
        _open_window(html_page=error_page(_port_taken_text(settings.port), log_file), icon=icon, log_file=log_file)
    elif state == "mira":
        _open_window(url=url, icon=icon, log_file=log_file)  # 已经有一个 Mira 在跑，只开窗口，不另起服务
    else:
        server = ServerThread(settings)
        server.start()
        try:
            _open_window(html_page=LOADING_PAGE, url=url, server=server, icon=icon, log_file=log_file)
        finally:
            server.stop()
            log.info("Mira 已停止")


if __name__ == "__main__":
    main()
