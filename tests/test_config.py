import pytest

from mira.config import ConfigError, load_settings


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
    assert s.debounce_seconds == 2.5 and str(s.db_path) == "x/y.db"
