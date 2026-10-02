from pathlib import Path

import pytest

from mira.config import PROJECT_ROOT, load_settings

TEMPLATES = ["zhengyou", "listener", "sunyou", "study-buddy", "genki"]


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_follows_shared_rules(name):
    text = (PROJECT_ROOT / "personas" / f"{name}.md").read_text(encoding="utf-8")
    assert text.startswith("# 你是")
    assert "AI" in text and "TA" in text  # 坦诚是 AI；用 TA 指用户
    assert "示范台词" in text
    # 回复格式、回复方式这些由程序统一管理，模板里写了会和规则打架
    for forbidden in ("json", "messages", "approach", "mood_read", "expression"):
        assert forbidden not in text.lower(), forbidden


def test_default_persona_is_zhengyou_template():
    s = load_settings({"MIRA_FAKE": "1", "PERSONA_PATH": ""})
    assert s.persona_path in (PROJECT_ROOT / "personas" / "zhengyou.md", PROJECT_ROOT / "persona.local.md")
    assert load_settings({"MIRA_FAKE": "1", "PERSONA_PATH": "personas/sunyou.md"}).persona_path.exists()
    assert not Path(PROJECT_ROOT / "persona.md").exists()  # 旧的默认人设已并入 personas/
