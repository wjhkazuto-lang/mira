from datetime import date

import pytest

from mira.config import load_settings
from mira.fakes import FakeLLM, HashEmbedder
from mira.llm import LLMError
from mira.retriever import Retriever
from mira.writer import Writer

EMB = HashEmbedder()
SETTINGS = load_settings({"MIRA_FAKE": "1"})


def make_writer(store, clock, script):
    llm = FakeLLM(script)
    return Writer(store, EMB, Retriever(store, EMB, now=clock.now), llm, SETTINGS, now=clock.now), llm


def mem(store, type, content, **kw):
    return store.add_memory(type, content, vector=EMB.embed([content])[0], actor="writer", **kw)


def chat(store):
    store.add_message("user", "周五我要面试，得先把简历改完")
    store.add_message("assistant", "加油！什么岗位？")


EPISODE = {"content": "聊了周五面试，有点紧张", "importance": 3}


async def test_no_unprocessed_returns_none(store, clock):
    w, llm = make_writer(store, clock, [])
    assert await w.run() is None and llm.calls == []


async def test_adds_commitment_and_episode(store, clock):
    chat(store)
    w, llm = make_writer(store, clock, [{
        "ops": [{"op": "add", "type": "commitment", "content": "周五面试前改完简历", "due_at": "2026-10-09"}],
        "episode": EPISODE,
    }])
    result = await w.run()
    [c] = store.list_memories("commitment")
    assert (c.status, c.due_at, c.importance) == ("open", date(2026, 10, 9), 3)
    assert c.source_message_ids == [m.id for m in store.list_messages()]
    [e] = store.list_memories("episode")
    assert e.content == EPISODE["content"] and result.episode_id == e.id and result.applied == 1
    assert store.unprocessed_messages() == []
    assert {entry.actor for entry in store.recent_log()} == {"writer"}
    assert llm.calls[0]["purpose"] == "writer" and llm.calls[0]["model"] == SETTINGS.background_model


async def test_prompt_has_transcript_and_ids(store, clock):
    old = mem(store, "fact", "在准备换工作")
    chat(store)
    w, llm = make_writer(store, clock, [{"ops": [], "episode": EPISODE}])
    await w.run()
    text = "\n".join(m["content"] for m in llm.calls[0]["messages"])
    assert "周五我要面试" in text and "Mira：加油" in text and f"#{old.id}" in text
    assert "2026-10-02" in text and "周五" in text


async def test_locked_memory_update_skipped(store, clock):
    m = mem(store, "fact", "喜欢猫", user_locked=True)
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [{"op": "update", "id": m.id, "content": "喜欢狗"}], "episode": EPISODE}])
    result = await w.run()
    assert store.get_memory(m.id).content == "喜欢猫" and any("锁定" in s for s in result.skipped)


async def test_unknown_id_skipped(store, clock):
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [{"op": "update", "id": 999, "content": "x"},
                                               {"op": "set_status", "id": 999, "status": "done"}],
                                       "episode": EPISODE}])
    result = await w.run()
    assert len(result.skipped) == 2 and result.applied == 0


async def test_cannot_add_pattern_or_episode(store, clock):
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [{"op": "add", "type": "pattern", "content": "总熬夜"},
                                               {"op": "add", "type": "episode", "content": "x"},
                                               {"op": "explode"}],
                                       "episode": EPISODE}])
    result = await w.run()
    assert store.list_memories("pattern") == [] and len(store.list_memories("episode")) == 1
    assert len(result.skipped) == 3


async def test_supersede_links_old_to_new(store, clock):
    old = mem(store, "fact", "在 A 公司上班")
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [{"op": "supersede", "id": old.id,
                                                "new": {"type": "fact", "content": "在 B 公司上班"}}],
                                       "episode": EPISODE}])
    await w.run()
    [new] = store.list_memories("fact", include_superseded=False)
    assert new.content == "在 B 公司上班" and store.get_memory(old.id).superseded_by == new.id


async def test_set_status_done_even_if_locked(store, clock):
    c = mem(store, "commitment", "改简历", status="open", user_locked=True)
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [{"op": "set_status", "id": c.id, "status": "done"},
                                               {"op": "set_status", "id": c.id, "status": "overdue"}],
                                       "episode": EPISODE}])
    result = await w.run()
    assert store.get_memory(c.id).status == "done" and len(result.skipped) == 1


async def test_dirty_fields_normalized(store, clock):
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [
        {"op": "add", "type": "fact", "content": "很重要", "importance": 9},
        {"op": "add", "type": "fact", "content": "不重要", "importance": 0},
        {"op": "add", "type": "commitment", "content": "下周五交", "due_at": "下周五"},
        {"op": "add", "type": "fact", "content": "   "},
        {"op": "add", "type": "fact", "content": "乱填", "importance": "很高"},
    ], "episode": {"content": "  "}}])
    result = await w.run()
    by = {m.content: m for m in store.list_memories()}
    assert by["很重要"].importance == 5 and by["不重要"].importance == 1 and by["乱填"].importance == 3
    assert by["下周五交"].due_at is None
    assert result.episode_id is None and len(result.skipped) == 2  # 空 content 的 add + 空 episode
    assert store.unprocessed_messages() == []


async def test_llm_error_leaves_unprocessed(store, clock):
    chat(store)
    w, _ = make_writer(store, clock, [LLMError("down")])
    with pytest.raises(LLMError):
        await w.run()
    assert len(store.unprocessed_messages()) == 2 and store.list_memories() == []


async def test_string_ids_accepted(store, clock):
    a = mem(store, "fact", "喜欢猫")
    c = mem(store, "commitment", "改简历", status="open")
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [{"op": "update", "id": f"#{a.id}", "importance": 5},
                                               {"op": "set_status", "id": str(c.id), "status": "done"}],
                                       "episode": EPISODE}])
    result = await w.run()
    assert result.skipped == [] and store.get_memory(a.id).importance == 5
    assert store.get_memory(c.id).status == "done"


async def test_large_backlog_processed_in_chunks(store, clock):
    from mira.writer import WRITER_CHUNK_CHARS

    for i in range(6):
        store.add_message("user", f"{i}" * 2900)
    w, llm = make_writer(store, clock, [{"ops": [], "episode": EPISODE}] * 6)
    await w.run()
    done = 6 - len(store.unprocessed_messages())
    assert 0 < done < 6 and len(llm.calls[0]["messages"][0]["content"]) < WRITER_CHUNK_CHARS + 6000
    while store.unprocessed_messages():
        await w.run()
    assert llm.calls[0]["max_tokens"] == 8000


async def test_huge_message_truncated_in_prompt(store, clock):
    store.add_message("user", "长" * 60000)
    w, llm = make_writer(store, clock, [{"ops": [], "episode": EPISODE}])
    await w.run()
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "省略" in prompt and len(prompt) < 10000 and store.unprocessed_messages() == []


async def test_transcript_dates_relative_to_message(store, clock):
    clock.t = clock.t.replace(day=1, hour=23, minute=50)  # 10-01 周四
    store.add_message("user", "明天面试")
    clock.advance(2 * 86400)
    w, llm = make_writer(store, clock, [{"ops": [], "episode": EPISODE}])
    await w.run()
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "[10-01 周四 23:50]" in prompt and "这条消息发出" in prompt


async def test_braces_in_chat_do_not_block_writer(store, clock):
    store.add_message("user", "我在学 Jinja，{{name}} 是什么意思？")
    w, llm = make_writer(store, clock, [{"ops": [], "episode": EPISODE}])
    await w.run()
    assert store.unprocessed_messages() == [] and "{{name}}" in llm.calls[0]["messages"][0]["content"]


async def test_update_adds_new_sources(store, clock):
    old_msg = store.add_message("user", "我养了一只猫")
    store.mark_processed([old_msg.id])
    m = mem(store, "fact", "养一只猫", source_message_ids=[old_msg.id])
    new_msg = store.add_message("user", "又领养了一只，现在两只猫了")
    w, _ = make_writer(store, clock, [{"ops": [{"op": "update", "id": m.id, "content": "养两只猫"}], "episode": EPISODE}])
    await w.run()
    assert store.get_memory(m.id).source_message_ids == [old_msg.id, new_msg.id]


async def test_adds_idea_and_goal(store, clock):
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [
        {"op": "add", "type": "idea", "content": "想学吉他", "due_at": "2026-10-09"},
        {"op": "add", "type": "goal", "content": "考研"},
    ], "episode": EPISODE}])
    await w.run()
    [i] = store.list_memories("idea")
    [g] = store.list_memories("goal")
    assert (i.status, i.due_at) == (None, None)  # 想法不带状态、不带截止日期
    assert g.status == "open"


async def test_goal_status_and_promotion(store, clock):
    g = mem(store, "goal", "把程序做到厂里能用", status="open")
    idea = mem(store, "idea", "想学吉他")
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [
        {"op": "supersede", "id": g.id, "new": {"type": "commitment", "content": "周五前搞定文字识别", "due_at": "2026-10-09"}},
        {"op": "set_status", "id": idea.id, "status": "done"},
    ], "episode": EPISODE}])
    result = await w.run()
    [c] = store.list_memories("commitment", include_superseded=False)
    assert store.get_memory(g.id).superseded_by == c.id and c.due_at == date(2026, 10, 9)
    assert len(result.skipped) == 1  # 想法没有状态


async def test_goal_set_status_done(store, clock):
    g = mem(store, "goal", "考研", status="open")
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [{"op": "set_status", "id": g.id, "status": "done"}], "episode": EPISODE}])
    await w.run()
    assert store.get_memory(g.id).status == "done"


async def test_writer_prompt_explains_intent_levels_and_self_judgment(store, clock):
    g = mem(store, "goal", "考研", status="open")
    chat(store)
    w, llm = make_writer(store, clock, [{"ops": [], "episode": EPISODE}])
    await w.run()
    prompt = llm.calls[0]["messages"][0]["content"]
    for word in ("idea", "goal", "commitment", "宁可往低一档", "自我评价"):
        assert word in prompt
    assert f"#{g.id}" in prompt


async def test_second_op_on_same_id_skipped(store, clock):
    m = mem(store, "fact", "喜欢猫")
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [
        {"op": "supersede", "id": m.id, "new": {"type": "fact", "content": "喜欢狗"}},
        {"op": "update", "id": m.id, "content": "喜欢兔子"},
    ], "episode": EPISODE}])
    result = await w.run()
    assert result.applied == 1 and any(f"同一批里已经改过 #{m.id}" in s for s in result.skipped)
    facts = [x for x in store.list_memories("fact")]
    assert {x.content for x in facts} == {"喜欢猫", "喜欢狗"}  # 没有孤儿“喜欢兔子”
    assert store.get_memory(m.id).content == "喜欢猫"


async def test_update_then_set_status_same_id_skipped(store, clock):
    c = mem(store, "commitment", "改简历", status="open")
    chat(store)
    w, _ = make_writer(store, clock, [{"ops": [
        {"op": "update", "id": c.id, "importance": 5},
        {"op": "set_status", "id": c.id, "status": "done"},
    ], "episode": EPISODE}])
    result = await w.run()
    assert result.applied == 1 and store.get_memory(c.id).status == "open"
    assert any("同一批里已经改过" in s for s in result.skipped)
