"""后台任务：空闲后写入记忆、每天反思、启动时补跑（spec §3）。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from mira import clock
from mira.config import Settings
from mira.store import Store

log = logging.getLogger(__name__)

FAILURE_BACKOFF = timedelta(minutes=30)  # 失败后等一会儿再试，避免反复花钱调用模型


class Scheduler:
    def __init__(
        self,
        *,
        store: Store,
        writer,
        reflector,
        settings: Settings,
        now: Callable[[], datetime] = clock.now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._store = store
        self._writer = writer
        self._reflector = reflector
        self._settings = settings
        self._now = now
        self._sleep = sleep
        self._last_activity = now()
        self._lock = asyncio.Lock()
        self._not_before: dict[str, datetime] = {}

    def notify_activity(self) -> None:
        self._last_activity = self._now()

    async def startup(self) -> None:
        if self._store.unprocessed_messages():
            await self._run_writer()
        last = self._store.get_job_last_run("reflector")
        if last is None or self._now() - last > timedelta(hours=24):
            await self._run_reflector()

    async def tick(self) -> None:
        now = self._now()
        idle = now - self._last_activity >= timedelta(minutes=self._settings.idle_write_minutes)
        if idle and self._store.unprocessed_messages():
            await self._run_writer()
        today_at_hour = now.replace(hour=self._settings.reflect_hour, minute=0, second=0, microsecond=0)
        last = self._store.get_job_last_run("reflector")
        if now >= today_at_hour and (last is None or last < today_at_hour):
            await self._run_reflector()

    async def run_forever(self, interval: float = 30) -> None:
        while True:
            await self.tick()
            await self._sleep(interval)

    async def _run_writer(self) -> None:
        await self._run("writer", self._writer)

    async def _run_reflector(self) -> None:
        if await self._run("reflector", self._reflector):
            self._store.set_job_last_run("reflector", self._now())

    async def _run(self, name: str, job) -> bool:
        if self._now() < self._not_before.get(name, self._now()):
            return False
        async with self._lock:
            try:
                await job.run()
            except Exception:
                log.exception("%s 运行失败，%s 分钟后重试", name, int(FAILURE_BACKOFF.total_seconds() // 60))
                self._not_before[name] = self._now() + FAILURE_BACKOFF
                return False
        return True
