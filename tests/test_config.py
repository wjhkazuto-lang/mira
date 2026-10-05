import pytest

from pathlib import Path

from mira.config import PROJECT_ROOT, ConfigError, load_settings


def test_defaults_match_spec():
    s = load_settings({"DEEPSEEK_API_KEY": "k"})
    assert (s.chat_model, s.background_model, s.reflect_model) == (
        "deepseek-v4-pro",
        "deepseek-flash",
        "deepseek-v4-pro",
    )
    assert (s.debounce_seconds, s.max_wait_seconds, s.idle_write_minutes, s.reflect_hour) == (3, 60, 10, 4)
    assert (s.recent_history_tokens, s.retrieve_top_k) == (4000, 8)
    assert (s.host, s.port) == ("127.0.0.1", 8000)
    assert s.deepseek_base_url == "https://api.deepseek.com"
    assert "12356" in s.crisis_resources and s.fake is False


def test_env_overrides():
    s = load_settings({"DEEPSEEK_API_KEY": "k", "DEBOUNCE_SECONDS": "5", "CHAT_MODEL": "x"})
    assert s.debounce_seconds == 5.0 and s.chat_model == "x"


def test_missing_key_raises_chinese_hint():
    with pytest.raises(ConfigError) as e:
        load_settings({})
    assert "DEEPSEEK_API_KEY" in str(e.value) and ".env" in str(e.value)


def test_fake_mode_needs_no_key():
    assert load_settings({"MIRA_FAKE": "1"}).fake is True


def test_float_settings_accept_decimals():
    s = load_settings({"DEEPSEEK_API_KEY": "k", "DEBOUNCE_SECONDS": "2.5", "DB_PATH": "x/y.db"})
    assert s.debounce_seconds == 2.5 and s.db_path == PROJECT_ROOT / "x" / "y.db"


def test_paths_resolved_against_project_root():
    s = load_settings({"DEEPSEEK_API_KEY": "k", "PERSONA_PATH": "personas/zhengyou.md"})  # 不受你本地的 persona.local.md 影响
    assert s.db_path == PROJECT_ROOT / "data" / "mira.db"
    assert s.persona_path == PROJECT_ROOT / "personas" / "zhengyou.md" and s.persona_path.exists()
    assert load_settings({"DEEPSEEK_API_KEY": "k", "DB_PATH": "/tmp/x.db"}).db_path == Path("/tmp/x.db")


def test_fake_mode_defaults_to_separate_db():
    assert load_settings({"MIRA_FAKE": "1"}).db_path == PROJECT_ROOT / "data" / "dev.db"
    assert load_settings({"MIRA_FAKE": "1", "DB_PATH": "data/a.db"}).db_path == PROJECT_ROOT / "data" / "a.db"


def test_reflect_hour_validated():
    with pytest.raises(ConfigError):
        load_settings({"DEEPSEEK_API_KEY": "k", "REFLECT_HOUR": "24"})


def test_chat_temperature_default_and_override():
    assert load_settings({"DEEPSEEK_API_KEY": "k"}).chat_temperature == 1.3
    assert load_settings({"DEEPSEEK_API_KEY": "k", "CHAT_TEMPERATURE": "1.0"}).chat_temperature == 1.0


def test_local_persona_preferred_when_present(tmp_path, monkeypatch):
    import mira.config as config

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / "personas").mkdir()
    (tmp_path / "personas" / "zhengyou.md").write_text("公开版")
    assert config.load_settings({"MIRA_FAKE": "1"}).persona_path == tmp_path / "personas" / "zhengyou.md"
    (tmp_path / "persona.local.md").write_text("我的版本")
    assert config.load_settings({"MIRA_FAKE": "1"}).persona_path == tmp_path / "persona.local.md"
    assert config.load_settings({"MIRA_FAKE": "1", "PERSONA_PATH": "x.md"}).persona_path == tmp_path / "x.md"


def test_theme_dir_default():
    assert load_settings({"MIRA_FAKE": "1"}).theme_dir == PROJECT_ROOT / "theme"


def test_fake_mode_never_uses_real_db():
    # .env 里常常写着 DB_PATH=data/mira.db；开发模式不能因此碰到真实聊天记录
    s = load_settings({"MIRA_FAKE": "1", "DB_PATH": "data/mira.db"})
    assert s.db_path == PROJECT_ROOT / "data" / "dev.db"
    s = load_settings({"MIRA_FAKE": "1", "DB_PATH": str(PROJECT_ROOT / "data" / "mira.db")})
    assert s.db_path == PROJECT_ROOT / "data" / "dev.db"


def test_backup_defaults_and_paths():
    s = load_settings({"DEEPSEEK_API_KEY": "k"})
    assert s.backup_dir == PROJECT_ROOT / "data" / "backups" and s.backup_keep == 14
    assert load_settings({"DEEPSEEK_API_KEY": "k", "BACKUP_DIR": "x/b"}).backup_dir == PROJECT_ROOT / "x" / "b"


@pytest.mark.parametrize("v", ["0", "-1", "abc"])
def test_backup_keep_invalid(v):
    with pytest.raises(ConfigError):
        load_settings({"DEEPSEEK_API_KEY": "k", "BACKUP_KEEP": v})


def test_proactive_defaults():
    s = load_settings({"DEEPSEEK_API_KEY": "k"})
    assert s.proactive is True and s.proactive_max_per_day == 1
    assert s.proactive_quiet_hours == "22-8" and s.notifications is True
    assert (s.weekly_letter_weekday, s.weekly_letter_hour) == (6, 20)


def test_proactive_fake_default_and_explicit_switch():
    assert load_settings({"MIRA_FAKE": "1"}).proactive is False
    assert load_settings({"MIRA_FAKE": "1", "PROACTIVE": "1"}).proactive is True
    assert load_settings({"DEEPSEEK_API_KEY": "k", "PROACTIVE": "0"}).proactive is False
    assert load_settings({"DEEPSEEK_API_KEY": "k", "NOTIFICATIONS": "0"}).notifications is False


def test_proactive_env_overrides():
    s = load_settings({
        "DEEPSEEK_API_KEY": "k", "PROACTIVE_MAX_PER_DAY": "2", "PROACTIVE_QUIET_HOURS": "23-7",
        "WEEKLY_LETTER_WEEKDAY": "0", "WEEKLY_LETTER_HOUR": "9",
    })
    assert (s.proactive_max_per_day, s.proactive_quiet_hours) == (2, "23-7")
    assert (s.weekly_letter_weekday, s.weekly_letter_hour) == (0, 9)


@pytest.mark.parametrize("env", [
    {"PROACTIVE_QUIET_HOURS": "25-8"},
    {"PROACTIVE_QUIET_HOURS": "abc"},
    {"PROACTIVE_QUIET_HOURS": "8"},
    {"PROACTIVE_MAX_PER_DAY": "0"},
    {"WEEKLY_LETTER_WEEKDAY": "7"},
    {"WEEKLY_LETTER_HOUR": "24"},
])
def test_proactive_invalid_values(env):
    with pytest.raises(ConfigError):
        load_settings({"DEEPSEEK_API_KEY": "k", **env})
