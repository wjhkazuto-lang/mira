"""启动 Mira：uv run python -m mira"""

import logging
import socket
import sys

import uvicorn

from mira.config import ConfigError, load_settings
from mira.embedder import EmbedderLoadError


def port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex((host, port)) == 0


def fail(message: str) -> None:
    print(f"\n{message}\n", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
    except ConfigError as e:
        fail(f"启动失败：{e}")
    # 先查端口，免得重复启动时还要白等模型加载
    if port_in_use(settings.host, settings.port):
        fail(
            f"端口 {settings.port} 已经被占用，Mira 可能已经在运行了。\n"
            f"先在浏览器打开 http://{settings.host}:{settings.port} 看看；"
            "如果打不开，找到之前运行 Mira 的终端窗口按 Ctrl+C 停掉，再重新启动。"
        )
    try:
        from mira.main import create_app

        app = create_app(settings)
    except EmbedderLoadError as e:
        fail(f"启动失败：{e}")
    mode = "（开发模式：假回复）" if settings.fake else ""
    print(f"\nMira 已启动{mode}，在浏览器打开 http://{settings.host}:{settings.port}\n", flush=True)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
