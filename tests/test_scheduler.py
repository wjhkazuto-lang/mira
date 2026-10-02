import asyncio
import logging
from datetime import datetime

import pytest

from mira.config import load_settings
from mira.scheduler import Scheduler
from tests.conftest import FakeClock

SETTINGS = load_settings({"MIRA_FAKE": "1"})


class Job:
    def __init__(self, fail=False):
        self.runs = 0
        self.fail = fail

    async def run(self):
        self.runs += 1
        if self.fail:
            raise RuntimeError("boom")


def make(store, clock, writer=None, reflector=None):
    writer, reflector = writer or Job(), reflector or Job()
    return Scheduler(store=store, writer=writer, reflector=reflector, settings=SETTINGS, now=clock.now), writer, reflector


def at(clock, iso):
    clock.t = datetime.fromisoformat(iso)


async def test_writer_after_idle(store, clock):
    s, w, _ = make(store, clock)
    store.add_message("user", "hi")
    clock.advance(10 * 60)
    await s.tick()
    assert w.runs == 1


async def test_no_writer_when_recent_activity(store, clock):
    s, w, _ = make(store, clock)
    store.add_message("user", "hi")
    clock.advance(9 * 60)
    s.notify_activity()
    clock.advance(9 * 60)
    await s.tick()
    assert w.runs == 0


async def test_no_writer_without_unprocessed(store, clock):
    s, w, _ = make(store, clock)
    clock.advance(3600)
    await s.tick()
    assert w.runs == 0


async def test_daily_reflect_once(store, clock):
    at(clock, "2026-10-02T04:05:00+08:00")
    store.set_job_last_run("reflector", datetime.fromisoformat("2026-10-01T04:01:00+08:00"))
    s, _, r = make(store, clock)
    await s.tick()
    assert r.runs == 1 and store.get_job_last_run("reflector") == clock.now()
    clock.advance(3600)
    await s.tick()
    assert r.runs == 1
    at(clock, "2026-10-03T03:59:00+08:00")
    await s.tick()
    assert r.runs == 1
    at(clock, "2026-10-03T04:00:00+08:00")
    await s.tick()
    assert r.runs == 2


async def test_startup_catchup(store, clock):
    store.add_message("user", "昨晚的话")
    store.set_job_last_run("reflector", datetime.fromisoformat("2026-10-01T15:00:00+08:00"))  # 30 小时前
    s, w, r = make(store, clock)
    await s.startup()
    assert (w.runs, r.runs) == (1, 1)


async def test_startup_skips_recent_reflect(store, clock):
    store.set_job_last_run("reflector", datetime.fromisoformat("2026-10-02T04:00:00+08:00"))
    s, w, r = make(store, clock)
    await s.startup()
    assert (w.runs, r.runs) == (0, 0)


async def test_startup_first_ever_reflect(store, clock):
    s, _, r = make(store, clock)
    await s.startup()
    assert r.runs == 1


async def test_job_failure_logged(store, clock, caplog):
    at(clock, "2026-10-02T05:00:00+08:00")
    s, _, r = make(store, clock, reflector=Job(fail=True))
    with caplog.at_level(logging.ERROR):
        await s.tick()
    assert r.runs == 1 and store.get_job_last_run("reflector") is None
    assert any("reflector" in rec.getMessage() for rec in caplog.records)


async def test_failed_job_backs_off_30_minutes(store, clock):
    at(clock, "2026-10-02T05:00:00+08:00")
    store.add_message("user", "hi")
    s, w, r = make(store, clock, writer=Job(fail=True), reflector=Job(fail=True))
    clock.advance(10 * 60)
    await s.tick()
    clock.advance(60)
    await s.tick()
    assert (w.runs, r.runs) == (1, 1)
    clock.advance(30 * 60)
    await s.tick()
    assert (w.runs, r.runs) == (2, 2)


async def test_run_forever_survives_tick_error(store, clock):
    calls = []

    async def sleep(_):
        if len(calls) >= 2:
            raise asyncio.CancelledError

    s = Scheduler(store=store, writer=Job(), reflector=Job(), settings=SETTINGS, now=clock.now, sleep=sleep)

    async def bad_tick():
        calls.append(1)
        raise RuntimeError("boom")

    s.tick = bad_tick
    with pytest.raises(asyncio.CancelledError):
        await s.run_forever()
    assert len(calls) == 2
