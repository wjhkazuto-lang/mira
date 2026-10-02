import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    chat_model: str = "deepseek-v4-pro"
    background_model: str = "deepseek-flash"
    reflect_model: str = "deepseek-v4-pro"
    debounce_seconds: float = 3
    max_wait_seconds: float = 60
    idle_write_minutes: float = 10
    reflect_hour: int = 4
    recent_history_tokens: int = 4000
    retrieve_top_k: int = 8
    crisis_resources: str = "全国心理援助热线 12356；希望24热线 400-161-9995；紧急情况 120/110"
    db_path: Path = Path("data/mira.db")
    persona_path: Path = Path("persona.md")
    host: str = "127.0.0.1"
    port: int = 8000
    fake: bool = False


_MISSING_KEY_HINT = (
    "缺少 DEEPSEEK_API_KEY。\n"
    "请把项目根目录下的 .env.example 复制一份，命名为 .env，"
    "然后在 DEEPSEEK_API_KEY= 后面填入你的 DeepSeek API key。\n"
    "如果只是想先试用界面，可以设置 MIRA_FAKE=1（开发模式，使用假回复）。"
)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        load_dotenv()
        env = os.environ

    values: dict = {}
    for f in fields(Settings):
        if f.name == "fake":
            values["fake"] = env.get("MIRA_FAKE", "") == "1"
            continue
        raw = env.get(f.name.upper())
        if raw is None or raw == "":
            continue
        try:
            values[f.name] = f.type(raw)
        except ValueError as e:
            raise ConfigError(f"配置项 {f.name.upper()} 的值无效：{raw!r}") from e

    if values.get("fake") and "db_path" not in values:
        values["db_path"] = Path("data/dev.db")  # 开发模式默认用单独的数据库，免得假数据混进真记忆
    for key in ("db_path", "persona_path"):
        path = values.get(key, getattr(Settings, key))
        values[key] = path if path.is_absolute() else PROJECT_ROOT / path
    settings = Settings(**values)
    if not 0 <= settings.reflect_hour <= 23:
        raise ConfigError(f"REFLECT_HOUR 必须是 0 到 23：{settings.reflect_hour}")
    if not settings.fake and not settings.deepseek_api_key:
        raise ConfigError(_MISSING_KEY_HINT)
    return settings
