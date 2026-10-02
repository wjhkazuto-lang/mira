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
    s = load_settings({"DEEPSEEK_API_KEY": "k"})
    assert s.db_path == PROJECT_ROOT / "data" / "mira.db"
    assert s.persona_path == PROJECT_ROOT / "persona.md" and s.persona_path.exists()
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
    (tmp_path / "persona.md").write_text("公开版")
    assert config.load_settings({"MIRA_FAKE": "1"}).persona_path == tmp_path / "persona.md"
    (tmp_path / "persona.local.md").write_text("我的版本")
    assert config.load_settings({"MIRA_FAKE": "1"}).persona_path == tmp_path / "persona.local.md"
    assert config.load_settings({"MIRA_FAKE": "1", "PERSONA_PATH": "x.md"}).persona_path == tmp_path / "x.md"
