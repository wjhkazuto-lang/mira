from datetime import date

import pytest

from mira.config import load_settings
from mira.fakes import FakeLLM, HashEmbedder
from mira.llm import LLMError
from mira.reflector import Reflector

EMB = HashEmbedder()
SETTINGS = load_settings({"MIRA_FAKE": "1"})


def make(store, clock, script):
    llm = FakeLLM(script)
    return Reflector(store, EMB, llm, SETTINGS, now=clock.now), llm


def mem(store, type, content, **kw):
    return store.add_memory(type, content, vector=EMB.embed([content])[0], actor="writer", **kw)


def episodes(store, n=2):
    return [mem(store, "episode", f"压力大，熬夜刷手机到三点，第二天自责 {i}") for i in range(n)]


def pattern_skips(result):
    return [s for s in result.skipped if "档案" not in s]


def out(patterns=(), profile=""):
    return {"patterns": list(patterns), "profile": profile}


async def test_marks_overdue(store, clock):
    late = mem(store, "commitment", "昨天该交", status="open", due_at=date(2026, 10, 1))
    future = mem(store, "commitment", "明天交", status="open", due_at=date(2026, 10, 3))
    today = mem(store, "commitment", "今天交", status="open", due_at=date(2026, 10, 2))
    done = mem(store, "commitment", "交过了", status="done", due_at=date(2026, 9, 1))
    r, _ = make(store, clock, [])
    result = await r.run()
    assert store.get_memory(late.id).status == "overdue" and result.overdue_marked == 1
    assert [store.get_memory(m.id).status for m in (future, today, done)] == ["open", "open", "done"]


async def test_overdue_marked_even_if_llm_fails(store, clock):
    late = mem(store, "commitment", "昨天该交", status="open", due_at=date(2026, 10, 1))
    episodes(store)
    r, _ = make(store, clock, [LLMError("down")])
    with pytest.raises(LLMError):
        await r.run()
    assert store.get_memory(late.id).status == "overdue"


async def test_skips_llm_without_recent_episodes(store, clock):
    mem(store, "episode", "很久以前")
    clock.advance(8 * 86400)
    r, llm = make(store, clock, [])
    await r.run()
    assert llm.calls == []


async def test_prompt_and_model(store, clock):
    e1, e2 = episodes(store)
    store.add_profile("旧档案内容", "reflector")
    r, llm = make(store, clock, [out()])
    await r.run()
    call = llm.calls[0]
    text = "\n".join(m["content"] for m in call["messages"])
    assert call["purpose"] == "reflector" and call["model"] == SETTINGS.reflect_model
    assert f"#{e1.id}" in text and "旧档案内容" in text


async def test_add_pattern_needs_two_episodes(store, clock):
    e1, e2 = episodes(store)
    r, _ = make(store, clock, [out([
        {"op": "add", "content": "只出现一次", "evidence": [e1.id, e1.id]},
        {"op": "add", "content": "压力大时熬夜，第二天自责", "evidence": [e1.id, f"#{e2.id}", 999]},
    ])])
    result = await r.run()
    [p] = store.list_memories("pattern")
    assert p.content == "压力大时熬夜，第二天自责" and p.evidence == [e1.id, e2.id]
    assert result.applied == 1 and len(pattern_skips(result)) == 1


async def test_add_evidence_unions(store, clock):
    e1, e2, e3 = episodes(store, 3)
    p = mem(store, "pattern", "熬夜", evidence=[e1.id, e2.id])
    r, _ = make(store, clock, [out([{"op": "add_evidence", "id": p.id, "evidence": [e2.id, e3.id]}])])
    await r.run()
    assert store.get_memory(p.id).evidence == [e1.id, e2.id, e3.id]


async def test_merge_patterns(store, clock):
    e1, e2, e3 = episodes(store, 3)
    keep = mem(store, "pattern", "熬夜", evidence=[e1.id, e2.id])
    drop = mem(store, "pattern", "晚睡", evidence=[e2.id, e3.id])
    r, _ = make(store, clock, [out([{"op": "merge", "keep_id": keep.id, "drop_ids": [drop.id]}])])
    await r.run()
    assert store.get_memory(drop.id).superseded_by == keep.id
    assert store.get_memory(keep.id).evidence == [e1.id, e2.id, e3.id]


async def test_merge_locked_skipped(store, clock):
    e1, e2 = episodes(store)
    keep = mem(store, "pattern", "熬夜", evidence=[e1.id, e2.id])
    drop = mem(store, "pattern", "晚睡", evidence=[e1.id, e2.id], user_locked=True)
    r, _ = make(store, clock, [out([{"op": "merge", "keep_id": keep.id, "drop_ids": [drop.id]},
                                    {"op": "merge", "keep_id": keep.id, "drop_ids": [keep.id]}])])
    result = await r.run()
    assert store.get_memory(drop.id).superseded_by is None and len(pattern_skips(result)) == 2


async def test_profile_versioning_and_oversize(store, clock):
    episodes(store)
    r, _ = make(store, clock, [out(profile="你是一个程序员，最近在找工作。")])
    assert (await r.run()).profile_updated is True
    assert store.current_profile().source == "reflector"
    r, _ = make(store, clock, [out(profile="字" * 3000)])
    result = await r.run()
    assert result.profile_updated is False and store.current_profile().content.startswith("你是一个程序员")
    assert any("档案" in s for s in result.skipped)


async def test_dangling_evidence_ignored(store, clock):
    e1, e2, e3 = episodes(store, 3)
    p = mem(store, "pattern", "熬夜", evidence=[e1.id, e2.id, e3.id])
    store.delete_memory(e3.id, actor="user")
    r, llm = make(store, clock, [out([{"op": "add_evidence", "id": p.id, "evidence": [e3.id]}])])
    await r.run()
    text = "\n".join(m["content"] for m in llm.calls[0]["messages"])
    assert f"#{e3.id}" not in text
    assert store.get_memory(p.id).evidence == [e1.id, e2.id, e3.id]  # 原有证据不改写，只是展示时过滤


class MidCallLLM:
    """模型调用期间执行一段操作（模拟用户在这时改了数据），再返回结果。"""

    def __init__(self, during, result):
        self.during, self.result = during, result

    async def complete_json(self, **kw):
        self.during()
        return self.result


async def test_user_profile_edit_during_reflection_wins(store, clock):
    episodes(store)
    store.add_profile("TA 在 A 公司", "reflector")
    llm = MidCallLLM(lambda: store.add_profile("TA 已经离职了", "user"), out(profile="TA 在 A 公司上班，很忙"))
    result = await Reflector(store, EMB, llm, SETTINGS, now=clock.now).run()
    assert store.current_profile().content == "TA 已经离职了" and result.profile_updated is False


async def test_evidence_rechecked_after_llm_call(store, clock):
    e1, e2, e3 = episodes(store, 3)
    llm = MidCallLLM(lambda: store.delete_memory(e2.id, actor="user"),
                     out([{"op": "add", "content": "熬夜", "evidence": [e1.id, e2.id]}]))
    await Reflector(store, EMB, llm, SETTINGS, now=clock.now).run()
    assert store.list_memories("pattern") == []
