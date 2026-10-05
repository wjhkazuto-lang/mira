import asyncio
import contextlib
from datetime import date, datetime

import numpy as np

from mira.chat import ChatEngine
from mira.config import load_settings
from mira.fakes import FakeLLM, HashEmbedder
from mira.proactive import ProactiveEngine
from mira.retriever import Retriever

EMB = HashEmbedder()
SETTINGS = load_settings({"MIRA_FAKE": "1", "PROACTIVE": "1"})
NO_NOTIFY_SETTINGS = load_settings({"MIRA_FAKE": "1", "PROACTIVE": "1", "NOTIFICATIONS": "0"})
V = np.ones(4, dtype=np.float32) / 2


class FakeNotifier:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def notify(self, title, body):
        self.calls.append((title, body))
        if self.fail:
            raise RuntimeError("boom")


async def instant(_seconds):
    await asyncio.sleep(0)


class Outbox:
    def __init__(self):
        self.events = []

    async def __call__(self, ev):
        self.events.append(ev)

    def kinds(self):
        return [e["type"] for e in self.events]


def add(store, type="fact", content="喜欢火锅", **kw):
    return store.add_memory(type, content, vector=V, actor="writer", **kw)


def at(clock, iso):
    clock.t = datetime.fromisoformat(iso)


def make(store, clock, script):
    llm = FakeLLM(script)
    box = Outbox()
    chat = ChatEngine(
        store=store, retriever=Retriever(store, EMB, now=clock.now), llm=llm, settings=SETTINGS,
        persona="我是 Mira", rules="规则", now=clock.now, sleep=instant,
    )
    chat.attach(box)
    return llm, chat, box


def make_proactive(store, clock, llm, chat, notifier=None, settings=SETTINGS):
    return ProactiveEngine(
        store=store, llm=llm, retriever=Retriever(store, EMB, now=clock.now), settings=settings,
        engine=chat, persona="我是 Mira", rules="规则", notifier=notifier, now=clock.now,
    )


def seed_due_promise(store, clock):
    """两天前说过话 + 一条到期承诺（跑完整 run() 的前提条件）。"""
    at(clock, "2026-10-03T09:00:00+08:00")
    store.add_message("user", "材料我还没写")
    add(store, "commitment", "交材料", status="open", due_at=date(2026, 10, 2))
    at(clock, "2026-10-05T09:00:00+08:00")


def speak_script(messages):
    return [{"speak": True, "kind": "commitment", "ref_id": 1, "reason": "逾期了",
             "messages": messages if isinstance(messages, list) else [messages]}]


def assistants(store):
    return [m for m in store.list_messages() if m.role == "assistant"]


async def test_announce_broadcasts_typing_then_bubbles(store, clock):
    _, chat, box = make(store, clock, [])
    ok = await chat.announce_proactive(["一", "二"], meta={"proactive": {"kind": "missing"}})
    assert ok
    assert box.kinds() == ["typing", "bubble", "typing", "bubble"]
    msgs = assistants(store)
    assert [m.content for m in msgs] == ["一", "二"]
    assert msgs[0].meta == {"proactive": {"kind": "missing"}} and msgs[1].meta == {}


async def test_announce_skips_when_busy(store, clock):
    _, chat, box = make(store, clock, [])

    async def hang():
        await asyncio.Event().wait()

    chat._task = asyncio.create_task(hang())  # 模拟正在回复
    try:
        assert await chat.announce_proactive(["在吗"], meta={}) is False
    finally:
        chat._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await chat._task
    assert store.list_messages() == [] and box.events == []


async def test_run_full_path(store, clock):
    at(clock, "2026-10-03T09:00:00+08:00")  # 两天前说过话（通过了“人在场”的闸门）
    store.add_message("user", "材料我还没写")
    add(store, "commitment", "交材料", status="open", due_at=date(2026, 10, 2))
    at(clock, "2026-10-05T09:00:00+08:00")
    llm = FakeLLM([{"speak": True, "kind": "commitment", "ref_id": 1, "reason": "逾期了",
                    "messages": ["材料的事，怎么样了？"]}])
    box = Outbox()
    chat = ChatEngine(store=store, retriever=Retriever(store, EMB, now=clock.now), llm=llm,
                      settings=SETTINGS, persona="我是 Mira", rules="规则", now=clock.now, sleep=instant)
    chat.attach(box)
    proactive = make_proactive(store, clock, llm, chat)

    await proactive.run()

    assert [c["purpose"] for c in llm.calls] == ["proactive"]
    msgs = assistants(store)
    assert len(msgs) == 1 and msgs[0].content == "材料的事，怎么样了？"
    assert msgs[0].meta["proactive"]["kind"] == "commitment"
    assert box.kinds() == ["typing", "bubble"]
    assert store.last_proactive_at() == clock.now()
    # 主动消息不进"待整理"：待整理的只有那条用户消息
    assert store.pending_user_message_count() == 1
    assert [m.role for m in store.unprocessed_user_messages()] == ["user"]


async def test_run_silent_without_candidates(store, clock):
    at(clock, "2026-10-05T09:00:00+08:00")
    store.add_message("user", "hi")
    llm, chat, box = make(store, clock, [])
    proactive = make_proactive(store, clock, llm, chat)

    await proactive.run()

    assert llm.calls == [] and assistants(store) == [] and box.events == []
    assert store.last_proactive_at() is None


async def test_run_drops_when_user_returns_midcall(store, clock):
    at(clock, "2026-10-03T09:00:00+08:00")
    store.add_message("user", "材料我还没写")
    add(store, "commitment", "交材料", status="open", due_at=date(2026, 10, 2))
    at(clock, "2026-10-05T09:00:00+08:00")

    class InterruptLLM:
        """模型调用期间用户突然回来了。"""

        def __init__(self):
            self.calls = 0

        async def complete_json(self, *, purpose, model, messages, max_tokens=2000, temperature=None):
            self.calls += 1
            store.add_message("user", "我回来了")
            return {"speak": True, "kind": "commitment", "ref_id": 1, "reason": "x", "messages": ["在吗"]}

    llm = InterruptLLM()
    box = Outbox()
    chat = ChatEngine(store=store, retriever=Retriever(store, EMB, now=clock.now), llm=llm,
                      settings=SETTINGS, persona="我是 Mira", rules="规则", now=clock.now, sleep=instant)
    chat.attach(box)
    proactive = make_proactive(store, clock, llm, chat)

    await proactive.run()

    assert llm.calls == 1  # 模型确实问了，但最后放弃了
    assert assistants(store) == [] and box.events == []
    assert store.last_proactive_at() is None


async def test_run_notifies_with_first_bubble(store, clock):
    seed_due_promise(store, clock)
    llm, chat, _ = make(store, clock, speak_script("材料的事，怎么样了？"))
    notifier = FakeNotifier()
    proactive = make_proactive(store, clock, llm, chat, notifier=notifier)

    await proactive.run()

    assert notifier.calls == [("Mira", "材料的事，怎么样了？")]


async def test_notify_body_is_clipped(store, clock):
    seed_due_promise(store, clock)
    llm, chat, _ = make(store, clock, speak_script("长" * 400))
    notifier = FakeNotifier()
    proactive = make_proactive(store, clock, llm, chat, notifier=notifier)

    await proactive.run()

    body = notifier.calls[0][1]
    assert "省略" in body and len(body) < 200


async def test_run_skips_notify_when_disabled(store, clock):
    seed_due_promise(store, clock)
    llm, chat, _ = make(store, clock, speak_script("在吗"))
    notifier = FakeNotifier()
    proactive = make_proactive(store, clock, llm, chat, notifier=notifier, settings=NO_NOTIFY_SETTINGS)

    await proactive.run()

    assert notifier.calls == []
    assert assistants(store) and store.last_proactive_at() is not None  # 消息照常进聊天


async def test_notifier_failure_keeps_message(store, clock):
    seed_due_promise(store, clock)
    llm, chat, _ = make(store, clock, speak_script("在吗"))
    notifier = FakeNotifier(fail=True)
    proactive = make_proactive(store, clock, llm, chat, notifier=notifier)

    await proactive.run()  # 通知失败不能影响已经说出口的话

    assert notifier.calls == [("Mira", "在吗")]
    assert assistants(store) and store.last_proactive_at() is not None
