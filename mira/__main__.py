"""启动 Mira：uv run python -m mira"""

import logging
import os
import socket
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from mira.config import ConfigError, load_settings
from mira.embedder import EmbedderLoadError


def port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex((host, port)) == 0


def fail(message: str) -> None:
    print(f"\n{message}\n", file=sys.stderr)
    sys.exit(1)


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(log_file: Path | None) -> None:
    """None：输出到终端；给了路径：写到文件，满 1MB 轮换，留 3 份旧的。"""
    if log_file is None:
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
        return
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    path = os.path.abspath(log_file)
    if any(isinstance(h, RotatingFileHandler) and h.baseFilename == path for h in root.handlers):
        return  # 已经在写这个文件了，别重复加（否则每行日志写两遍）
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)


def main() -> None:
    setup_logging(None)
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
