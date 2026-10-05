from datetime import date, datetime

import numpy as np

from mira.config import load_settings
from mira.proactive import ProactiveEngine, quiet_now

V = np.ones(4, dtype=np.float32) / 2
SETTINGS = load_settings({"MIRA_FAKE": "1", "PROACTIVE": "1"})


def add(store, type="fact", content="喜欢火锅", **kw):
    return store.add_memory(type, content, vector=V, actor="writer", **kw)


def at(clock, iso):
    clock.t = datetime.fromisoformat(iso)


class Busy:
    def __init__(self, busy=False):
        self.value = busy

    def busy(self):
        return self.value


def make(store, clock, settings=SETTINGS, busy=False):
    engine = Busy(busy)
    proactive = ProactiveEngine(
        store=store, llm=None, retriever=None, settings=settings,
        engine=engine, persona="p", rules="r", now=clock.now,
    )
    return proactive, engine


def test_quiet_now_edges():
    def h(hour):
        return datetime.fromisoformat(f"2026-10-02T{hour:02d}:00:00+08:00")

    assert quiet_now("22-8", h(21)) is False
    assert quiet_now("22-8", h(22)) is True
    assert quiet_now("22-8", h(7)) is True
    assert quiet_now("22-8", h(8)) is False
    assert quiet_now("8-17", h(7)) is False
    assert quiet_now("8-17", h(12)) is True
    assert quiet_now("8-17", h(17)) is False


def test_ready_false_without_switch(store, clock):
    proactive, _ = make(store, clock, load_settings({"MIRA_FAKE": "1"}))  # 开发模式默认关
    at(clock, "2026-10-02T09:00:00+08:00")
    store.add_message("user", "hi")
    clock.advance(5 * 3600)
    assert proactive.ready() is False


def test_ready_false_in_quiet_hours(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-10-02T17:00:00+08:00")
    store.add_message("user", "hi")
    at(clock, "2026-10-02T23:00:00+08:00")
    assert proactive.ready() is False
    at(clock, "2026-10-03T09:00:00+08:00")
    assert proactive.ready() is True


def test_ready_false_without_any_user_message(store, clock):
    proactive, _ = make(store, clock)
    clock.advance(3 * 86400)
    assert proactive.ready() is False


def test_ready_false_when_user_recent(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-10-02T09:00:00+08:00")
    store.add_message("user", "hi")
    clock.advance(4 * 3600 - 1)
    assert proactive.ready() is False
    clock.advance(1)
    assert proactive.ready() is True


def test_ready_false_within_eval_gap(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-10-02T09:00:00+08:00")
    store.add_message("user", "hi")
    clock.advance(5 * 3600)
    store.set_job_last_run("proactive", clock.now())
    assert proactive.ready() is False
    clock.advance(4 * 3600)
    assert proactive.ready() is True


def test_ready_false_when_daily_cap_reached(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-10-02T09:00:00+08:00")
    store.add_message("user", "hi")
    clock.advance(5 * 3600)
    store.add_proactive_log("missing")
    clock.advance(60)
    store.add_message("user", "嗯")  # 用户回应了，排除"没人应"那条
    clock.advance(3600)
    assert proactive.ready() is False  # 今天已开口一次
    clock.advance(86400)
    assert proactive.ready() is True  # 第二天


def test_ready_false_when_last_proactive_unanswered(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-10-01T09:00:00+08:00")
    store.add_message("user", "hi")
    clock.advance(5 * 3600)
    store.add_proactive_log("missing")  # 10-01 14:00 开口
    at(clock, "2026-10-02T09:00:00+08:00")  # 第二天：每天上限不再是干扰
    assert proactive.ready() is False  # 说了没人应，不追问
    store.add_message("user", "在")
    clock.advance(5 * 3600)
    assert proactive.ready() is True


def test_ready_false_when_engine_busy(store, clock):
    proactive, engine = make(store, clock, busy=True)
    at(clock, "2026-10-02T09:00:00+08:00")
    store.add_message("user", "hi")
    clock.advance(5 * 3600)
    assert proactive.ready() is False
    engine.value = False
    assert proactive.ready() is True


def test_collect_commitment_due_and_cooldown(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-10-02T09:00:00+08:00")
    due = add(store, "commitment", "交材料", status="open", due_at=date(2026, 10, 2))
    add(store, "commitment", "下周的事", status="open", due_at=date(2026, 10, 9))
    add(store, "commitment", "没有日期", status="open")
    add(store, "commitment", "已完成", status="done", due_at=date(2026, 10, 2))

    got = [c for c in proactive.collect() if c.kind == "commitment"]
    assert [c.ref_id for c in got] == [due.id]
    assert "今天到期" in got[0].text and "交材料" in got[0].text

    store.add_proactive_log("commitment", "commitment", due.id)
    clock.advance(2 * 86400)  # 冷却期内
    assert [c for c in proactive.collect() if c.kind == "commitment"] == []
    clock.advance(2 * 86400)  # 共 4 天，冷却过了
    got = [c for c in proactive.collect() if c.kind == "commitment"]
    assert [c.ref_id for c in got] == [due.id]
    assert "已逾期 4 天" in got[0].text


def test_collect_missing_gap(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-10-02T09:00:00+08:00")
    store.add_message("user", "hi")
    clock.advance(86400)  # 1 天
    assert [c for c in proactive.collect() if c.kind == "missing"] == []
    clock.advance(86400)  # 2 天
    got = [c for c in proactive.collect() if c.kind == "missing"]
    assert len(got) == 1 and got[0].ref_id is None and "2 天" in got[0].text


def test_collect_episode_once(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-10-02T00:00:00+08:00")
    add(store, "episode", "很久前的事")  # 81 小时前
    at(clock, "2026-10-04T21:00:00+08:00")
    recent = add(store, "episode", "昨天难过的事")  # 60 小时前
    at(clock, "2026-10-05T09:00:00+08:00")

    assert [c.ref_id for c in proactive.collect() if c.kind == "checkin"] == [recent.id]
    store.add_proactive_log("checkin", "episode", recent.id)
    assert [c for c in proactive.collect() if c.kind == "checkin"] == []  # 每个事件只回访一次


def test_collect_goal_stale_and_cooldown(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-09-20T09:00:00+08:00")
    stale = add(store, "goal", "学吉他", status="open")
    at(clock, "2026-10-01T09:00:00+08:00")
    fresh = add(store, "goal", "新目标", status="open")
    at(clock, "2026-10-05T09:00:00+08:00")

    assert [c.ref_id for c in proactive.collect() if c.kind == "goal"] == [stale.id]
    store.add_proactive_log("goal", "goal", stale.id)
    clock.advance(10 * 86400)  # 10-15：stale 还在冷却里；fresh 满 14 天成新候选
    assert [c.ref_id for c in proactive.collect() if c.kind == "goal"] == [fresh.id]
    clock.advance(5 * 86400)  # 10-20：stale 冷却也过了
    assert [c.ref_id for c in proactive.collect() if c.kind == "goal"] == [stale.id, fresh.id]


def test_collect_empty(store, clock):
    proactive, _ = make(store, clock)
    at(clock, "2026-10-05T09:00:00+08:00")
    assert proactive.collect() == []
