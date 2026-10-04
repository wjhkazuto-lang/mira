import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mira.backup import BackupError
from mira.config import load_settings
from mira.scheduler import Scheduler
from tests.conftest import FakeClock

SETTINGS = load_settings({"MIRA_FAKE": "1"})


async def test_first_run_with_advancing_clock(store):
    current = datetime.fromisoformat("2026-10-02T20:00:00+08:00")

    def now():
        nonlocal current
        current += timedelta(microseconds=1)
        return current

    writer, reflector = Job(), Job()
    scheduler = Scheduler(store=store, writer=writer, reflector=reflector, settings=SETTINGS, now=now)
    store.add_message("user", "测试消息")
    await scheduler.startup()
    assert writer.runs == 1
    assert reflector.runs == 1
    assert store.get_job_last_run("reflector") is not None


class Job:
    def __init__(self, fail=False):
        self.runs = 0
        self.fail = fail

    async def run(self):
        self.runs += 1
        if self.fail:
            raise RuntimeError("boom")


def make(store, clock, writer=None, reflector=None, backup=None):
    writer, reflector = writer or Job(), reflector or Job()
    return Scheduler(store=store, writer=writer, reflector=reflector, settings=SETTINGS, now=clock.now, backup=backup), writer, reflector


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


async def test_status_failure_backoff_then_recovery(store, clock):
    class RecoveringJob:
        fail = True

        async def run(self):
            if self.fail:
                raise RuntimeError("secret-key-and-private-content")
            store.mark_processed([m.id for m in store.unprocessed_messages()])

    job = RecoveringJob()
    scheduler, _, _ = make(store, clock, writer=job)
    store.add_message("user", "私密正文不应出现在状态中")
    first = scheduler.status()
    assert first["state"] == "waiting"
    clock.advance(60)
    scheduler.notify_activity()
    assert scheduler.status()["next_run_at"] > first["next_run_at"]
    clock.advance(600)
    await scheduler.tick()
    failed = scheduler.status()
    assert failed["state"] == "retrying"
    assert "secret" not in str(failed) and "私密" not in str(failed)
    assert failed["last_success_at"] is None
    assert datetime.fromisoformat(failed["next_run_at"]) == clock.now() + timedelta(minutes=30)
    job.fail = False
    clock.advance(1800)
    await scheduler.tick()
    success = scheduler.status()
    assert success["state"] == "idle"
    assert success["error"] is None
    assert success["last_success_at"] == clock.now()


async def test_status_while_writer_in_flight(store, clock):
    started, release = asyncio.Event(), asyncio.Event()

    class WaitingJob:
        async def run(self):
            started.set()
            await release.wait()

    scheduler, _, _ = make(store, clock, writer=WaitingJob())
    store.add_message("user", "测试")
    task = asyncio.create_task(scheduler.startup())
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert scheduler.status()["state"] == "running"
        assert scheduler.status()["next_run_at"] is None
    finally:
        release.set()
        await task


class Backup:
    def __init__(self, fail=False):
        self.runs = 0
        self.fail = fail

    def __call__(self):
        self.runs += 1
        if self.fail:
            raise BackupError("备份失败：磁盘已满")
        return Path(f"/tmp/mira-{self.runs}.db")


async def test_startup_backs_up_when_never_done(store, clock):
    b = Backup()
    s, _, _ = make(store, clock, backup=b)
    await s.startup()
    assert b.runs == 1
    assert store.get_job_last_run("backup") == clock.now()
    assert s.status()["last_backup_at"] == clock.now().isoformat()


async def test_startup_backs_up_before_writer(store, clock):
    order = []
    b = Backup()
    orig = b.__call__

    class W(Job):
        async def run(self):
            order.append("writer")

    def backup():
        order.append("backup")
        return orig()

    s, _, _ = make(store, clock, writer=W(), backup=backup)
    store.add_message("user", "hi")
    await s.startup()
    assert order == ["backup", "writer"]


async def test_backup_not_repeated_within_24h(store, clock):
    b = Backup()
    s, _, _ = make(store, clock, backup=b)
    await s.startup()
    clock.advance(23 * 3600)
    await s.tick()
    assert b.runs == 1


async def test_backup_after_24h(store, clock):
    b = Backup()
    s, _, _ = make(store, clock, backup=b)
    await s.startup()
    clock.advance(24 * 3600)
    await s.tick()
    assert b.runs == 2


async def test_backup_failure_backs_off_30min_and_reports(store, clock):
    b = Backup(fail=True)
    s, _, _ = make(store, clock, backup=b)
    await s.startup()
    assert b.runs == 1 and s.status()["backup_error"] == "备份失败：磁盘已满"
    assert store.get_job_last_run("backup") is None
    clock.advance(29 * 60)
    await s.tick()
    assert b.runs == 1
    clock.advance(2 * 60)
    await s.tick()
    assert b.runs == 2


async def test_backup_unexpected_error_is_generic(store, clock):
    def boom():
        raise OSError("/secret/path")

    s, _, _ = make(store, clock, backup=boom)
    await s.startup()
    assert s.status()["backup_error"] == "备份失败，请查看运行日志"


async def test_backup_does_not_wait_for_model_lock(store, clock):
    release = asyncio.Event()

    class Blocked(Job):
        async def run(self):
            await release.wait()

    b = Backup()
    s, _, _ = make(store, clock, writer=Blocked(), backup=b)
    store.add_message("user", "hi")
    clock.advance(10 * 60)
    task = asyncio.create_task(s.tick())
    try:
        await asyncio.sleep(0.05)  # 写入器已经占着模型锁
        assert await asyncio.wait_for(s.backup_now(), 1)
        assert b.runs == 1
    finally:
        release.set()
        await task


async def test_backup_now_ignores_interval_and_raises_on_failure(store, clock):
    b = Backup()
    s, _, _ = make(store, clock, backup=b)
    await s.startup()
    await s.backup_now()
    assert b.runs == 2
    b.fail = True
    with pytest.raises(BackupError):
        await s.backup_now()
    assert s.status()["backup_error"] == "备份失败：磁盘已满"
    b.fail = False
    await s.backup_now()  # 失败退避也不拦手动备份
    assert b.runs == 4 and s.status()["backup_error"] is None


async def test_status_has_backup_fields(store, clock):
    s, _, _ = make(store, clock, backup=Backup())
    assert {"last_backup_at", "backup_running", "backup_error"} <= set(s.status())


async def test_no_backup_callable_means_disabled(store, clock):
    s, _, _ = make(store, clock)
    await s.startup()
    clock.advance(48 * 3600)
    await s.tick()
    st = s.status()
    assert st["last_backup_at"] is None and st["backup_running"] is False and st["backup_error"] is None
    with pytest.raises(RuntimeError):
        await s.backup_now()


async def test_no_4am_reflect_within_12h_of_startup_run(store, clock):
    at(clock, "2026-10-03T22:00:00+08:00")
    s, _, r = make(store, clock)
    await s.startup()
    assert r.runs == 1
    at(clock, "2026-10-04T04:05:00+08:00")
    await s.tick()
    assert r.runs == 1


async def test_4am_reflect_after_12h_gap(store, clock):
    at(clock, "2026-10-03T15:00:00+08:00")
    s, _, r = make(store, clock)
    await s.startup()
    at(clock, "2026-10-04T04:05:00+08:00")
    await s.tick()
    assert r.runs == 2
