from mira.theme import EXPRESSIONS, Theme


def make_theme(tmp_path, expressions=(), backgrounds=()):
    for name in expressions:
        (tmp_path / "mira").mkdir(exist_ok=True)
        (tmp_path / "mira" / f"{name}.png").write_bytes(b"x")
    for name in backgrounds:
        (tmp_path / "background").mkdir(exist_ok=True)
        (tmp_path / "background" / name).write_bytes(b"x")
    return Theme(tmp_path)


def test_empty_or_missing_theme(tmp_path):
    t = Theme(tmp_path / "nope")
    assert t.expressions() == [] and t.backgrounds() == {} and t.pick("happy", "normal") is None


def test_discovers_known_expressions_only(tmp_path):
    t = make_theme(tmp_path, ["calm", "happy", "weird"])
    assert t.expressions() == ["calm", "happy"]  # 按 EXPRESSIONS 的顺序，只认识的名字
    assert t.urls()["happy"] == "/theme/mira/happy.png"


def test_backgrounds_any_image_extension(tmp_path):
    t = make_theme(tmp_path, backgrounds=["day.jpg", "night.webp", "notes.txt"])
    assert t.backgrounds() == {"day": "/theme/background/day.jpg", "night": "/theme/background/night.webp"}


def test_pick_prefers_requested_then_approach_then_calm(tmp_path):
    t = make_theme(tmp_path, ["calm", "gentle", "happy"])
    assert t.pick("happy", "normal") == "happy"
    assert t.pick("surprised", "comfort") == "gentle"  # 没有惊讶 → 按安慰的默认
    assert t.pick(None, "raise_issue") == "calm"
    assert t.pick("nonsense", "unknown") == "calm"


def test_pick_falls_back_to_first_available(tmp_path):
    assert make_theme(tmp_path, ["smile"]).pick("happy", "normal") == "smile"


def test_prompt_lists_only_available(tmp_path):
    text = make_theme(tmp_path, ["calm", "happy"]).prompt_hint()
    assert "calm" in text and "happy" in text and "worried" not in text
    assert "expression" not in Theme(tmp_path / "nope").prompt_hint()


def test_expression_names_have_chinese_labels():
    assert {"calm", "talk", "annoyed", "surprised", "worried", "gentle", "happy", "smile"} == set(EXPRESSIONS)
