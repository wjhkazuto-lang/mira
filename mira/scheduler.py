"""后台任务：空闲后写入记忆、每天反思、启动时补跑（spec §3）。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

from mira import clock
from mira.backup import BackupError
from mira.config import Settings
from mira.store import Store
from mira.llm import LLMError, LLMBadJSON

log = logging.getLogger(__name__)

FAILURE_BACKOFF = timedelta(minutes=30)  # 失败后等一会儿再试，避免反复花钱调用模型
BACKUP_INTERVAL = timedelta(hours=24)
BACKUP_JOBS = ("backup", "backup_manual")  # 自动 / 手动；只有自动的会影响 24 小时间隔
REFLECT_MIN_GAP = timedelta(hours=12)  # 两次反思之间至少隔这么久（启动补跑后不再马上凌晨又跑）


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
        backup: Callable[[bool], Path] | None = None,  # 同步函数（参数=是否手动），放进线程执行；None 表示不备份
    ):
        self._store = store
        self._writer = writer
        self._reflector = reflector
        self._settings = settings
        self._now = now
        self._sleep = sleep
        self._backup = backup
        self._last_activity = now()
        self._lock = asyncio.Lock()
        self._backup_lock = asyncio.Lock()  # 备份不用等模型任务
        self._not_before: dict[str, datetime] = {}
        self._running: set[str] = set()
        self._errors: dict[str, str] = {}

    def status(self) -> dict:
        pending = self._store.pending_message_count()
        now = self._now()
        due = self._last_activity + timedelta(minutes=self._settings.idle_write_minutes)
        retry = self._not_before.get("writer")
        if retry is not None:
            due = max(due, retry)
        runs = [t for t in (self._store.get_job_last_run(j) for j in BACKUP_JOBS) if t]
        last_backup = max(runs) if runs else None
        backup_err_job = next((j for j in reversed(BACKUP_JOBS) if j in self._errors), None)  # 两个都失败时先显示手动的
        state = "running" if "writer" in self._running else (
            "retrying" if "writer" in self._errors else ("waiting" if pending else "idle")
        )
        return {
            "state": state,
            "pending_messages": pending,
            "next_run_at": max(now, due).isoformat() if pending and state != "running" else None,
            "last_success_at": self._store.get_job_last_run("writer"),
            "error": self._errors.get("writer"),
            "reflector_running": "reflector" in self._running,
            "reflector_error": self._errors.get("reflector"),
            "last_backup_at": last_backup.isoformat() if last_backup else None,
            "backup_running": any(j in self._running for j in BACKUP_JOBS),
            "backup_error": self._errors.get(backup_err_job) if backup_err_job else None,
            "backup_error_manual": backup_err_job == "backup_manual",  # 手动失败不会自动重试
        }

    def notify_activity(self) -> None:
        self._last_activity = self._now()

    def _backup_due(self) -> bool:
        if self._backup is None:
            return False
        last = self._store.get_job_last_run("backup")
        return last is None or self._now() - last >= BACKUP_INTERVAL

    async def startup(self) -> None:
        if self._backup_due():  # 先留快照，再让模型改记忆
            await self._run_backup()
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
        # 离上次至少 12 小时才跑凌晨这次；Mac 凌晨在睡觉时，满 24 小时也补跑，保证每天一次
        if now >= today_at_hour and (last is None or (last < today_at_hour and (
                today_at_hour - last >= REFLECT_MIN_GAP or now - last >= timedelta(hours=24)))):
            await self._run_reflector()
        if self._backup_due():
            await self._run_backup()

    async def backup_now(self) -> Path:
        """立即备份（忽略间隔和退避）；失败时抛 BackupError。"""
        if self._backup is None:
            raise RuntimeError("备份未启用")
        path: Path | None = None

        async def job():
            nonlocal path
            path = await asyncio.to_thread(self._backup, True)

        if not await self._run("backup_manual", job, self._backup_lock, force=True):
            raise BackupError(self._errors.get("backup_manual", "备份失败，请查看运行日志"))
        assert path is not None
        return path

    async def run_forever(self, interval: float = 30) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("后台任务检查出错，%s 秒后继续", interval)
            await self._sleep(interval)

    async def _run_writer(self) -> None:
        await self._run("writer", self._writer.run)

    async def _run_reflector(self) -> None:
        await self._run("reflector", self._reflector.run)

    async def _run_backup(self) -> None:
        await self._run("backup", lambda: asyncio.to_thread(self._backup, False), self._backup_lock)

    async def _run(self, name: str, job: Callable[[], Awaitable], lock: asyncio.Lock | None = None, force: bool = False) -> bool:
        not_before = self._not_before.get(name)
        if not force and not_before is not None and self._now() < not_before:
            return False
        async with lock or self._lock:
            self._running.add(name)
            try:
                await job()
                self._store.set_job_last_run(name, self._now())
                self._errors.pop(name, None)
                self._not_before.pop(name, None)
                if name in BACKUP_JOBS:  # 任一次备份成功，旧的备份错误就不用再显示
                    for j in BACKUP_JOBS:
                        self._errors.pop(j, None)
            except Exception as e:
                log.exception("%s 运行失败，%s 分钟后重试", name, int(FAILURE_BACKOFF.total_seconds() // 60))
                self._not_before[name] = self._now() + FAILURE_BACKOFF
                # 不把可能含凭证或私人内容的第三方异常正文发送到页面。
                self._errors[name] = (
                    "模型返回格式不正确" if isinstance(e, LLMBadJSON) else
                    e.user_message if isinstance(e, (LLMError, BackupError)) else
                    "备份失败，请查看运行日志" if name in BACKUP_JOBS else "后台处理出错，请查看运行终端"
                )
                return False
            finally:
                self._running.discard(name)
        return True
