"""后台任务：空闲后写入记忆、每天反思、启动时补跑（spec §3）。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from mira import clock
from mira.config import Settings
from mira.store import Store
from mira.llm import LLMError, LLMBadJSON

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
        self._running: str | None = None
        self._errors: dict[str, str] = {}

    def status(self) -> dict:
        pending = self._store.pending_message_count()
        now = self._now()
        due = self._last_activity + timedelta(minutes=self._settings.idle_write_minutes)
        retry = self._not_before.get("writer")
        if retry is not None:
            due = max(due, retry)
        state = "running" if self._running == "writer" else (
            "retrying" if "writer" in self._errors else ("waiting" if pending else "idle")
        )
        return {
            "state": state,
            "pending_messages": pending,
            "next_run_at": max(now, due).isoformat() if pending and state != "running" else None,
            "last_success_at": self._store.get_job_last_run("writer"),
            "error": self._errors.get("writer"),
            "reflector_running": self._running == "reflector",
            "reflector_error": self._errors.get("reflector"),
        }

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
            try:
                await self.tick()
            except Exception:
                log.exception("后台任务检查出错，%s 秒后继续", interval)
            await self._sleep(interval)

    async def _run_writer(self) -> None:
        await self._run("writer", self._writer)

    async def _run_reflector(self) -> None:
        await self._run("reflector", self._reflector)

    async def _run(self, name: str, job) -> bool:
        not_before = self._not_before.get(name)
        if not_before is not None and self._now() < not_before:
            return False
        async with self._lock:
            self._running = name
            try:
                await job.run()
                self._store.set_job_last_run(name, self._now())
                self._errors.pop(name, None)
                self._not_before.pop(name, None)
            except Exception as e:
                log.exception("%s 运行失败，%s 分钟后重试", name, int(FAILURE_BACKOFF.total_seconds() // 60))
                self._not_before[name] = self._now() + FAILURE_BACKOFF
                # 不把可能含凭证或私人内容的第三方异常正文发送到页面。
                self._errors[name] = (
                    "模型返回格式不正确" if isinstance(e, LLMBadJSON) else
                    e.user_message if isinstance(e, LLMError) else "后台处理出错，请查看运行终端"
                )
                return False
            finally:
                self._running = None
        return True
