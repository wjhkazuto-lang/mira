from evals.style_stats import style_stats


def test_style_stats_counts_turns():
    rows = [
        ("user", "a", "在吗"),
        ("assistant", "a", "在"), ("assistant", "a", "说说看，怎么了？"), ("assistant", "a", "你现在什么感受？"),
        ("user", "b", "累"),
        ("assistant", "b", "哈哈辛苦了"),
    ]
    s = style_stats(rows)
    assert s["turns"] == 2
    assert s["bubbles"] == {1: 1, 3: 1}
    assert s["ends_with_question"] == 1 and s["multi_question"] == 1 and s["shuo_shuo_kan"] == 1


def test_style_stats_empty():
    assert style_stats([])["turns"] == 0
