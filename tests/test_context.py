from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from mira.context import Reply, build_chat_messages, format_gap, format_memory, parse_reply
from mira.prompts import render

V = np.zeros(4, dtype=np.float32)


def build(store, clock, history=(), new=("今天好累",), memories=(), commitments=(), profile="他是程序员"):
    hist = [store.add_message(r, c) for r, c in history]
    new_msgs = [store.add_message("user", c, batch_id="b") for c in new]
    return build_chat_messages(
        persona="我是 Mira", rules="规则", profile=profile, history=hist, new_messages=new_msgs,
        memories=list(memories), commitments=list(commitments), now=clock.now(), last_chat_at=None,
    )


def test_build_order_and_roles(store, clock):
    msgs = build(store, clock, history=[("user", "在吗"), ("assistant", "在")])
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert "我是 Mira" in msgs[0]["content"] and "【核心档案】" in msgs[0]["content"]
    assert "以下是你对对方的了解" in msgs[0]["content"] and msgs[0]["content"].endswith("他是程序员")
    last = msgs[-1]["content"]
    assert last.index("【背景】") < last.index("【新消息】") and last.endswith("今天好累")
    assert "现在：2026-10-02 周五 21:00" in last and "距离上次聊天：第一次聊天" in last


def test_no_profile_section_when_none(store, clock):
    assert "【核心档案】" not in build(store, clock, profile=None)[0]["content"]


def test_history_merges_same_role(store, clock):
    msgs = build(store, clock, history=[("user", "a"), ("user", "b"), ("assistant", "c")])
    assert [m["role"] for m in msgs[1:-1]] == ["user", "assistant"] and msgs[1]["content"] == "a\nb"


def test_trailing_user_history_merged_into_final(store, clock):
    msgs = build(store, clock, history=[("assistant", "嗨"), ("user", "上一条没回")])
    assert [m["role"] for m in msgs] == ["system", "assistant", "user"]
    assert msgs[-1]["content"].startswith("上一条没回")


def test_commitment_not_duplicated(store, clock):
    c = store.add_memory("commitment", "改简历", vector=V, actor="w", status="open")
    msgs = build(store, clock, memories=[c], commitments=[c])
    assert msgs[-1]["content"].count(f"#{c.id}") == 1


def test_empty_sections_say_none(store, clock):
    assert build(store, clock)[-1]["content"].count("（无）") == 2


def test_new_messages_joined(store, clock):
    assert build(store, clock, new=("一", "二"))[-1]["content"].endswith("【新消息】\n一\n二")


def test_format_memory_labels(store, clock):
    f = store.add_memory("fact", "在做前端", vector=V, actor="w")
    p = store.add_memory("person", "大学室友", vector=V, actor="w", subject="小林")
    c = store.add_memory("commitment", "改简历", vector=V, actor="w", status="overdue", due_at=date(2026, 10, 9))
    c2 = store.add_memory("commitment", "早睡", vector=V, actor="w", status="open")
    pat = store.add_memory("pattern", "压力大时熬夜", vector=V, actor="w", evidence=[1, 2])
    e = store.add_memory("episode", "聊了面试", vector=V, actor="w")
    assert format_memory(f) == f"#{f.id} [事实] 在做前端"
    assert format_memory(p) == f"#{p.id} [人物·小林] 大学室友"
    assert format_memory(c) == f"#{c.id} [承诺·逾期·截止 2026-10-09] 改简历"
    assert format_memory(c2) == f"#{c2.id} [承诺·进行中] 早睡"
    assert format_memory(pat) == f"#{pat.id} [模式·出现 2 次] 压力大时熬夜"
    assert format_memory(e) == f"#{e.id} [事件·10-02] 聊了面试"


def test_format_gap(clock):
    now = clock.now()
    assert format_gap(None, now) == "第一次聊天"
    assert format_gap(now - timedelta(minutes=10), now) == "刚刚还在聊"
    assert format_gap(now - timedelta(hours=5), now) == "5 小时"
    assert format_gap(now - timedelta(days=3, hours=2), now) == "3 天"


def test_parse_reply_valid():
    assert parse_reply({"mood_read": "累", "approach": "comfort", "messages": ["抱抱", "辛苦了"]}) == Reply(
        "累", "comfort", ["抱抱", "辛苦了"])


def test_parse_reply_unknown_approach():
    assert parse_reply({"approach": "lecture", "messages": ["嗯"]}).approach == "unknown"


def test_parse_reply_drops_empty_bubbles():
    assert parse_reply({"approach": "normal", "messages": ["  ", " 嗯 ", "", 3]}).messages == ["嗯"]
    assert parse_reply({"approach": "normal", "messages": ["  "]}) is None
    assert parse_reply({"approach": "normal", "messages": "不是列表"}) is None
    assert len(parse_reply({"approach": "normal", "messages": [str(i) for i in range(10)]}).messages) == 6
    assert parse_reply({"mood_read": 5, "approach": "normal", "messages": ["嗯"]}).mood_read == ""


def test_render_and_missing_var():
    text = render("chat_rules", crisis_resources="热线X")
    assert "热线X" in text and "json" in text.lower() and "{{" not in text
    with pytest.raises(KeyError):
        render("chat_rules")


def test_persona_file_present():
    assert "Mira" in Path("persona.md").read_text(encoding="utf-8")


def test_reflector_prompt_uses_third_person():
    text = render("reflector", today="t", episodes="e", patterns="p", commitments="c", profile="x")
    assert "第三人称" in text and "TA" in text
