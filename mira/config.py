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
    chat_temperature: float = 1.3  # DeepSeek 官方给通用对话的推荐值
    debounce_seconds: float = 3
    max_wait_seconds: float = 60
    idle_write_minutes: float = 10
    reflect_hour: int = 4
    recent_history_tokens: int = 4000
    retrieve_top_k: int = 8
    crisis_resources: str = "全国心理援助热线 12356；希望24热线 400-161-9995；紧急情况 120/110"
    db_path: Path = Path("data/mira.db")
    persona_path: Path = Path("personas/zhengyou.md")
    theme_dir: Path = Path("theme")
    backup_dir: Path = Path("data/backups")
    backup_keep: int = 14
    proactive: bool = True  # 主动关心总开关
    proactive_max_per_day: int = 1  # 每天最多几条主动消息（每周信不算）
    proactive_quiet_hours: str = "22-8"  # 安静时段，格式 开始-结束，可跨夜
    weekly_letter_weekday: int = 6  # 每周信：0=周一 … 6=周日
    weekly_letter_hour: int = 20  # 每周信几点发（24 小时制）
    notifications: bool = True  # 0 = 只进聊天、不弹系统通知
    host: str = "127.0.0.1"
    port: int = 8000
    fake: bool = False


_MISSING_KEY_HINT = (
    "缺少 DEEPSEEK_API_KEY。\n"
    "请把项目根目录下的 .env.example 复制一份，命名为 .env，"
    "然后在 DEEPSEEK_API_KEY= 后面填入你的 DeepSeek API key。\n"
    "如果只是想先试用界面，可以设置 MIRA_FAKE=1（开发模式，使用假回复）。"
)


def parse_quiet_hours(hours: str) -> tuple[int, int]:
    """把 "22-8" 解析成 (22, 8)；格式不对或越界就抛 ConfigError。"""
    parts = hours.split("-")
    try:
        start, end = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        raise ConfigError(f"PROACTIVE_QUIET_HOURS 的格式应为 开始-结束（如 22-8）：{hours!r}") from None
    if len(parts) != 2 or not (0 <= start <= 23 and 0 <= end <= 23):
        raise ConfigError(f"PROACTIVE_QUIET_HOURS 的格式应为 开始-结束（如 22-8）：{hours!r}")
    return start, end


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
        if f.name in ("proactive", "notifications"):  # bool("0") 是 True，得按 "1"/"0" 解析
            values[f.name] = raw == "1"
            continue
        try:
            values[f.name] = f.type(raw)
        except ValueError as e:
            raise ConfigError(f"配置项 {f.name.upper()} 的值无效：{raw!r}") from e

    # 本地专用的人设（可能写了很多关于你的事）优先，它不会被 git 提交
    if "persona_path" not in values and (PROJECT_ROOT / "persona.local.md").exists():
        values["persona_path"] = Path("persona.local.md")
    if values.get("fake") and "db_path" not in values:
        values["db_path"] = Path("data/dev.db")  # 开发模式默认用单独的数据库，免得假数据混进真记忆
    if values.get("fake") and "proactive" not in values:
        values["proactive"] = False  # 开发模式默认不主动开口（要用就显式 PROACTIVE=1）
    for key in ("db_path", "persona_path", "theme_dir", "backup_dir"):
        path = values.get(key, getattr(Settings, key))
        values[key] = path if path.is_absolute() else PROJECT_ROOT / path
    real_db = PROJECT_ROOT / "data" / "mira.db"
    if values.get("fake") and values["db_path"].resolve() == real_db.resolve():
        # 开发模式用假回复，绝不能写进真实聊天记录（.env 里的 DB_PATH 常常就是它）
        values["db_path"] = PROJECT_ROOT / "data" / "dev.db"
    settings = Settings(**values)
    if not 0 <= settings.reflect_hour <= 23:
        raise ConfigError(f"REFLECT_HOUR 必须是 0 到 23：{settings.reflect_hour}")
    if settings.backup_keep < 1:
        raise ConfigError(f"BACKUP_KEEP 必须是大于等于 1 的整数：{settings.backup_keep}")
    if settings.proactive_max_per_day < 1:
        raise ConfigError(f"PROACTIVE_MAX_PER_DAY 必须是大于等于 1 的整数：{settings.proactive_max_per_day}")
    if not 0 <= settings.weekly_letter_weekday <= 6:
        raise ConfigError(f"WEEKLY_LETTER_WEEKDAY 必须是 0 到 6（0=周一）：{settings.weekly_letter_weekday}")
    if not 0 <= settings.weekly_letter_hour <= 23:
        raise ConfigError(f"WEEKLY_LETTER_HOUR 必须是 0 到 23：{settings.weekly_letter_hour}")
    parse_quiet_hours(settings.proactive_quiet_hours)  # 格式不对时抛 ConfigError
    if not settings.fake and not settings.deepseek_api_key:
        raise ConfigError(_MISSING_KEY_HINT)
    return settings
