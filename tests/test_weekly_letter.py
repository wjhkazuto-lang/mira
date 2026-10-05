import asyncio
import contextlib
from datetime import datetime

from mira.chat import ChatEngine
from mira.config import load_settings
from mira.fakes import EchoLLM, FakeLLM, HashEmbedder
from mira.llm import LLMBadJSON
from mira.proactive import ProactiveEngine, weekly_target
from mira.retriever import Retriever

EMB = HashEmbedder()
SETTINGS = load_settings({"MIRA_FAKE": "1", "PROACTIVE": "1"})


async def instant(_seconds):
    await asyncio.sleep(0)


class Outbox:
    def __init__(self):
        self.events = []

    async def __call__(self, ev):
        self.events.append(ev)

    def kinds(self):
        return [e["type"] for e in self.events]


def at(clock, iso):
    clock.t = datetime.fromisoformat(iso)


def make(store, clock, script, settings=SETTINGS):
    llm = FakeLLM(script)
    box = Outbox()
    chat = ChatEngine(store=store, retriever=Retriever(store, EMB, now=clock.now), llm=llm, settings=settings,
                      persona="我是 Mira", rules="规则", now=clock.now, sleep=instant)
    chat.attach(box)
    proactive = ProactiveEngine(store=store, llm=llm, retriever=Retriever(store, EMB, now=clock.now),
                                settings=settings, engine=chat, persona="我是 Mira", rules="规则", now=clock.now)
    return proactive, llm, box, chat


def t(iso):
    return datetime.fromisoformat(iso)


def test_weekly_target_math():
    # 周日（10-04）19:59 → 上一周日的 20:00
    assert weekly_target(t("2026-10-04T19:59:00+08:00"), 6, 20) == t("2026-09-27T20:00:00+08:00")
    # 周日 20:01 → 今天 20:00
    assert weekly_target(t("2026-10-04T20:01:00+08:00"), 6, 20) == t("2026-10-04T20:00:00+08:00")
    # 周一/周三 → 昨天/上周日
    assert weekly_target(t("2026-10-05T09:00:00+08:00"), 6, 20) == t("2026-10-04T20:00:00+08:00")
    assert weekly_target(t("2026-10-07T10:00:00+08:00"), 6, 20) == t("2026-10-04T20:00:00+08:00")


def test_weekly_ready_first_time_and_next_week(store, clock):
    proactive, _, _, _ = make(store, clock, [])
    at(clock, "2026-10-07T10:00:00+08:00")  # 周三、从没发过 → 第一封该发了
    assert proactive.weekly_ready() is True
    store.set_job_last_run("weekly_letter", clock.now())
    assert proactive.weekly_ready() is False  # 本轮已发
    at(clock, "2026-10-11T20:01:00+08:00")  # 下周日过了点
    assert proactive.weekly_ready() is True


def test_weekly_ready_catchup_after_sleeping_through_sunday(store, clock):
    proactive, _, _, _ = make(store, clock, [])
    store.set_job_last_run("weekly_letter", t("2026-09-27T20:05:00+08:00"))  # 上一次是上上周日
    at(clock, "2026-10-05T09:00:00+08:00")  # 10-04 周日晚 Mac 在睡觉，周一开机补发
    assert proactive.weekly_ready() is True


def test_weekly_ready_no_repeat_after_sunday_send(store, clock):
    proactive, _, _, _ = make(store, clock, [])
    store.set_job_last_run("weekly_letter", t("2026-10-04T20:05:00+08:00"))
    at(clock, "2026-10-05T09:00:00+08:00")
    assert proactive.weekly_ready() is False


def test_weekly_ready_false_when_proactive_off(store, clock):
    proactive, _, _, _ = make(store, clock, [], settings=load_settings({"MIRA_FAKE": "1"}))  # 开发模式默认关
    at(clock, "2026-10-07T10:00:00+08:00")
    assert proactive.weekly_ready() is False


async def test_run_weekly_sends_and_logs(store, clock):
    at(clock, "2026-10-07T10:00:00+08:00")
    proactive, llm, box, _ = make(store, clock, [
        {"messages": ["这周你连着三天早起了。", "下周材料的事，记得留一天余量。"], "expression": "gentle"},
    ])

    assert await proactive.run_weekly() is True

    assert [c["purpose"] for c in llm.calls] == ["weekly"]
    assert llm.calls[0]["model"] == SETTINGS.chat_model
    msgs = [m for m in store.list_messages() if m.role == "assistant"]
    assert [m.content for m in msgs] == ["这周你连着三天早起了。", "下周材料的事，记得留一天余量。"]
    assert msgs[0].meta["proactive"]["kind"] == "weekly_letter" and msgs[1].meta == {}
    assert box.kinds() == ["typing", "bubble", "typing", "bubble"]
    # 每周信进日志，但不算"每天一条"的额度
    assert store.last_proactive_at() == clock.now()
    assert store.proactive_count_on(clock.now().date()) == 0


async def test_run_weekly_defers_while_user_just_spoke(store, clock):
    at(clock, "2026-10-07T10:00:00+08:00")
    store.add_message("user", "在吗")  # 刚刚
    proactive, llm, _, _ = make(store, clock, [])

    assert await proactive.run_weekly() is False  # 下轮再试
    assert llm.calls == []  # 连模型都不叫


async def test_run_weekly_defers_while_replying(store, clock):
    at(clock, "2026-10-07T10:00:00+08:00")
    proactive, llm, _, chat = make(store, clock, [])

    async def hang():
        await asyncio.Event().wait()

    chat._task = asyncio.create_task(hang())  # 模拟正在回复
    try:
        assert await proactive.run_weekly() is False
    finally:
        chat._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await chat._task
    assert llm.calls == []


async def test_run_weekly_bad_json_skips_this_week(store, clock):
    at(clock, "2026-10-07T10:00:00+08:00")
    proactive, _, _, _ = make(store, clock, [LLMBadJSON(raw="x")])

    assert await proactive.run_weekly() is not False  # 本轮结束（会记 job_state），本周不再试
    assert [m for m in store.list_messages() if m.role == "assistant"] == []


async def test_run_weekly_empty_messages_skips_this_week(store, clock):
    at(clock, "2026-10-07T10:00:00+08:00")
    proactive, _, _, _ = make(store, clock, [{"messages": ["", "  "]}])

    assert await proactive.run_weekly() is not False
    assert [m for m in store.list_messages() if m.role == "assistant"] == []


async def test_echo_llm_weekly_branch():
    data = await EchoLLM().complete_json(purpose="weekly", model="m", messages=[], max_tokens=1)
    assert data["messages"]
