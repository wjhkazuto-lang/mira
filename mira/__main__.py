"""启动 Mira：uv run python -m mira"""

import logging
import sys

import uvicorn

from mira.config import ConfigError, load_settings
from mira.embedder import EmbedderLoadError


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
        from mira.main import create_app

        app = create_app(settings)
    except (ConfigError, EmbedderLoadError) as e:
        print(f"\n启动失败：{e}\n", file=sys.stderr)
        sys.exit(1)
    mode = "（开发模式：假回复）" if settings.fake else ""
    print(f"\nMira 已启动{mode}，在浏览器打开 http://{settings.host}:{settings.port}\n", flush=True)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
