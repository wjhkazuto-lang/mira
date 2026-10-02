import numpy as np

from mira.chat import ChatEngine
from mira.config import load_settings
from mira.fakes import FakeLLM, HashEmbedder
from mira.llm import LLMBadJSON, LLMError
from mira.retriever import Retriever
from tests.conftest import settle

EMB = HashEmbedder()
SETTINGS = load_settings({"MIRA_FAKE": "1"})
ERROR = {"type": "error", "message": "Mira 暂时没连上，点重试再试一次"}


def reply(*msgs, approach="normal"):
    return {"mood_read": "还行", "approach": approach, "messages": list(msgs)}


class Outbox:
    def __init__(self):
        self.events = []
        self.activity = []

    async def __call__(self, ev):
        self.events.append(ev)

    def kinds(self):
        return [e["type"] for e in self.events]


def make(store, clock, script, attach=True):
    llm = FakeLLM(script)
    box = Outbox()
    engine = ChatEngine(store=store, retriever=Retriever(store, EMB, now=clock.now), llm=llm, settings=SETTINGS,
                        persona="我是 Mira", rules="规则", on_activity=lambda: box.activity.append(clock.now()),
                        now=clock.now, sleep=clock.sleep)
    if attach:
        engine.attach(box)
    return engine, llm, box


def final_user(call):
    return call["messages"][-1]["content"]


def assistants(store):
    return [m for m in store.list_messages() if m.role == "assistant"]


async def test_blank_message_ignored(mstore, mclock):
    engine, llm, _ = make(mstore, mclock, [])
    assert await engine.on_user_message("  \n ") is None
    await mclock.advance(10)
    assert mstore.list_messages() == [] and llm.calls == []


async def test_waits_debounce_then_replies(mstore, mclock):
    engine, llm, box = make(mstore, mclock, [reply("嗨", "怎么啦", approach="comfort")])
    user = await engine.on_user_message("在吗")
    await mclock.advance(2.9)
    assert llm.calls == []
    await mclock.advance(0.2)
    assert len(llm.calls) == 1 and llm.calls[0]["purpose"] == "chat"
    assert llm.calls[0]["model"] == SETTINGS.chat_model
    await mclock.advance(10)
    assert box.kinds() == ["typing", "bubble", "typing", "bubble"]
    assert [e["text"] for e in box.events if e["type"] == "bubble"] == ["嗨", "怎么啦"]
    a1, a2 = assistants(mstore)
    assert a1.batch_id == a2.batch_id == user.batch_id
    assert a1.meta == {"mood_read": "还行", "approach": "comfort"} and a2.meta == {}


async def test_bubble_delay_scales_with_length(mstore, mclock):
    engine, _, box = make(mstore, mclock, [reply("短", "长" * 100)])
    await engine.on_user_message("hi")
    await mclock.advance(3.0)
    await mclock.advance(0.64)
    assert box.kinds() == ["typing"]  # 第一条延迟 0.6 + 0.05*1 = 0.65 秒，还没到
    await mclock.advance(0.02)
    assert box.kinds() == ["typing", "bubble", "typing"]
    await mclock.advance(3.9)
    assert box.kinds().count("bubble") == 1  # 长消息最多等 4 秒
    await mclock.advance(0.2)
    assert box.kinds().count("bubble") == 2


async def test_typing_extends_wait(mstore, mclock):
    engine, llm, _ = make(mstore, mclock, [reply("嗯")])
    await engine.on_user_message("我跟你说")
    await mclock.advance(2.5)
    await engine.on_typing()
    await mclock.advance(0.6)
    assert llm.calls == []
    await mclock.advance(2.5)
    assert len(llm.calls) == 1


async def test_max_wait_caps(mstore, mclock):
    engine, llm, _ = make(mstore, mclock, [reply("嗯")])
    await engine.on_user_message("我要说很长一段")
    for _ in range(29):
        await mclock.advance(2)
        await engine.on_typing()
    assert llm.calls == []
    await mclock.advance(2.1)
    assert len(llm.calls) == 1


async def test_consecutive_messages_one_batch(mstore, mclock):
    engine, llm, _ = make(mstore, mclock, [reply("嗯")])
    await engine.on_user_message("第一条")
    await mclock.advance(1)
    await engine.on_user_message("第二条")
    await mclock.advance(10)
    assert len(llm.calls) == 1 and final_user(llm.calls[0]).endswith("第一条\n第二条")


async def test_interrupt_before_first_bubble_merges(mstore, mclock):
    engine, llm, _ = make(mstore, mclock, [reply("回第一条"), reply("回两条")])
    await engine.on_user_message("第一条")
    await mclock.advance(3.1)
    assert len(llm.calls) == 1
    await engine.on_user_message("补充一句")
    await mclock.advance(10)
    assert [m.content for m in assistants(mstore)] == ["回两条"]
    assert final_user(llm.calls[1]).endswith("第一条\n补充一句")


async def test_interrupt_after_first_bubble_drops_rest(mstore, mclock):
    engine, llm, _ = make(mstore, mclock, [reply("一", "二", "三"), reply("好")])
    first = await engine.on_user_message("在吗")
    await mclock.advance(3.0 + 0.66)
    assert [m.content for m in assistants(mstore)] == ["一"]
    second = await engine.on_user_message("等等")
    await mclock.advance(10)
    assert [m.content for m in assistants(mstore)] == ["一", "好"]
    assert second.batch_id != first.batch_id
    assert final_user(llm.calls[1]).endswith("【新消息】\n等等")


async def test_llm_error_then_retry(mstore, mclock):
    engine, llm, box = make(mstore, mclock, [LLMError("down"), reply("我回来了")])
    await engine.on_user_message("在吗")
    await mclock.advance(3.1)
    assert box.events == [ERROR]
    await engine.retry()
    await mclock.advance(5)
    assert box.kinds()[-1] == "bubble" and assistants(mstore)[0].content == "我回来了"


async def test_bad_json_raw_as_bubble(mstore, mclock):
    engine, _, box = make(mstore, mclock, [LLMBadJSON(raw=" 嗯嗯 ")])
    await engine.on_user_message("hi")
    await mclock.advance(10)
    [a] = assistants(mstore)
    assert a.content == "嗯嗯" and a.meta["approach"] == "unknown"


async def test_bad_structure_sends_error(mstore, mclock):
    engine, _, box = make(mstore, mclock, [{"messages": []}, LLMBadJSON(raw="")])
    await engine.on_user_message("hi")
    await mclock.advance(10)
    await engine.retry()
    await mclock.advance(10)
    assert box.events == [ERROR, ERROR] and assistants(mstore) == []


async def test_detached_still_stores_reply(mstore, mclock):
    engine, _, box = make(mstore, mclock, [reply("你好", "在呢")])
    await engine.on_user_message("hi")
    await mclock.advance(3.5)
    engine.detach(box)
    await mclock.advance(10)
    assert [m.content for m in assistants(mstore)] == ["你好", "在呢"]


async def test_two_outboxes_and_dead_one_removed(mstore, mclock):
    engine, _, box = make(mstore, mclock, [reply("一", "二")])
    dead_calls = []

    async def dead(ev):
        dead_calls.append(ev)
        raise ConnectionError("closed")

    engine.attach(dead)
    await engine.on_user_message("hi")
    await mclock.advance(10)
    assert box.kinds() == ["typing", "bubble", "typing", "bubble"] and len(dead_calls) == 1


async def test_retry_after_restart(mstore, mclock):
    mstore.add_message("user", "上次没回我", batch_id="old")
    engine, llm, box = make(mstore, mclock, [reply("抱歉刚才掉线了")])
    await engine.retry()
    await mclock.advance(5)
    assert final_user(llm.calls[0]).endswith("上次没回我")
    assert assistants(mstore)[0].batch_id == "old"


async def test_retry_without_unanswered_does_nothing(mstore, mclock):
    engine, llm, _ = make(mstore, mclock, [])
    await engine.retry()
    await settle()
    assert llm.calls == []


async def test_memories_in_context_and_touched(mstore, mclock):
    m = mstore.add_memory("commitment", "周五面试", vector=EMB.embed(["周五面试"])[0], actor="w", status="open")
    f = mstore.add_memory("fact", "养了一只猫", vector=EMB.embed(["养了一只猫"])[0], actor="w")
    engine, llm, _ = make(mstore, mclock, [reply("加油")])
    await engine.on_user_message("周五的面试好紧张")
    await mclock.advance(10)
    text = final_user(llm.calls[0])
    assert f"#{m.id}" in text and f"#{f.id}" in text
    assert mstore.get_memory(m.id).last_recalled_at is not None


async def test_on_activity_called(mstore, mclock):
    engine, _, box = make(mstore, mclock, [reply("嗯")])
    await engine.on_user_message("hi")
    assert len(box.activity) == 1
    await mclock.advance(10)
    assert len(box.activity) == 2  # 用户发消息时一次，回复发完后一次
