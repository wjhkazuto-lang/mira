"""对话编排：等用户说完 → 检索 → 组装上下文 → 调模型 → 分条发送（spec §4）。"""

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import uuid4

from mira import clock
from mira.config import Settings
from mira.context import Reply, build_chat_messages, parse_reply
from mira.llm import LLM, LLMBadJSON, LLMError
from mira.retriever import Retriever
from mira.store import Message, Store

log = logging.getLogger(__name__)

Send = Callable[[dict], Awaitable[None]]
ERROR_EVENT = {"type": "error", "message": "Mira 暂时没连上，点重试再试一次"}
HISTORY_TAIL_FOR_QUERY = 6


_JSON_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def salvage_messages(raw: str) -> list[str]:
    """从被截断的 JSON 里捞出 messages 数组中已经完整的字符串。"""
    start = re.search(r'"messages"\s*:\s*\[', raw)
    if not start:
        return []
    out: list[str] = []
    pos = start.end()
    while True:
        m = _JSON_STRING.search(raw, pos)
        # 两个字符串之间只能是空白和逗号；遇到 ] 或别的东西说明数组结束了
        if not m or raw[pos:m.start()].strip() not in ("", ","):
            return out
        try:
            text = json.loads(f'"{m.group(1)}"').strip()
        except json.JSONDecodeError:
            return out
        if text:
            out.append(text)
        pos = m.end()


def bubble_delay(text: str) -> float:
    return min(0.6 + 0.05 * len(text), 4.0)


class ChatEngine:
    def __init__(
        self,
        *,
        store: Store,
        retriever: Retriever,
        llm: LLM,
        settings: Settings,
        persona: str,
        rules: str,
        on_activity: Callable[[], None] = lambda: None,
        now: Callable[[], datetime] = clock.now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._store = store
        self._retriever = retriever
        self._llm = llm
        self._settings = settings
        self._persona = persona
        self._rules = rules
        self._on_activity = on_activity
        self._now = now
        self._sleep = sleep
        self._outboxes: list[Send] = []
        self._task: asyncio.Task | None = None
        # 收消息和重试都要先取消旧任务再开新任务，中间有 await；加锁防止两路请求交错，产生重复回复
        self._lock = asyncio.Lock()
        # 当前"打开"的批次：发出第一个气泡时关闭
        self._batch_id: str | None = None
        self._batch_started_at: datetime | None = None
        self._last_input_at: datetime | None = None
        self._last_typing_at: datetime | None = None

    # ---------- 连接 ----------

    def attach(self, send: Send) -> None:
        self._outboxes.append(send)

    def detach(self, send: Send) -> None:
        if send in self._outboxes:
            self._outboxes.remove(send)

    async def _broadcast(self, event: dict) -> None:
        for send in list(self._outboxes):
            try:
                await send(event)
            except Exception:
                log.info("连接已断开，移除")
                self.detach(send)

    # ---------- 输入 ----------

    async def on_user_message(self, text: str, client_id: str | None = None) -> Message | None:
        text = text.strip()
        if not text:
            return None
        async with self._lock:
            return await self._accept_message(text, client_id)

    async def _accept_message(self, text: str, client_id: str | None) -> Message:
        now = self._now()
        was_running = await self._cancel_task()
        if self._batch_id is None:
            self._batch_id = uuid4().hex
        if self._batch_started_at is None or not was_running:
            self._batch_started_at = now
        msg = self._store.add_message("user", text, batch_id=self._batch_id)
        # 告诉所有页面这条消息的编号：发送的页面用 client_id 认领，其他标签页直接显示
        await self._broadcast(
            {"type": "user_message", "id": msg.id, "text": text, "created_at": msg.created_at.isoformat(),
             "client_id": client_id}
        )
        self._last_input_at = now
        self._on_activity()
        self._task = asyncio.create_task(self._wait_then_respond(self._batch_id))
        return msg

    async def on_typing(self) -> None:
        self._last_typing_at = self._now()

    async def retry(self) -> None:
        async with self._lock:
            await self._retry()

    async def _retry(self) -> None:
        batch_id = self._store.last_unanswered_batch()
        if batch_id is None:
            return
        await self._cancel_task()
        self._batch_id = batch_id
        self._batch_started_at = self._now()
        self._task = asyncio.create_task(self._respond(batch_id))

    async def wait_idle(self) -> None:
        if self._task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _cancel_task(self) -> bool:
        """取消正在进行的等待/回复；返回它是否还在运行。"""
        task = self._task
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True

    # ---------- 处理 ----------

    async def _wait_then_respond(self, batch_id: str) -> None:
        debounce = timedelta(seconds=self._settings.debounce_seconds)
        max_wait = timedelta(seconds=self._settings.max_wait_seconds)
        while True:
            last = max(t for t in (self._last_input_at, self._last_typing_at) if t is not None)
            deadline = min(last + debounce, self._batch_started_at + max_wait)
            now = self._now()
            if now >= deadline:
                break
            await self._sleep((deadline - now).total_seconds())
        await self._respond(batch_id)

    async def _respond(self, batch_id: str) -> None:
        try:
            await self._respond_inner(batch_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("回复时出错")
            await self._broadcast(ERROR_EVENT)

    async def _respond_inner(self, batch_id: str) -> None:
        new = self._store.messages_in_batch(batch_id)
        history = self._store.recent_messages(self._settings.recent_history_tokens, exclude_batch=batch_id)
        query = "\n".join(m.content for m in history[-HISTORY_TAIL_FOR_QUERY:] + new)
        memories = [s.memory for s in self._retriever.search(query, self._settings.retrieve_top_k)]
        commitments = self._store.open_commitments()
        profile = self._store.current_profile()
        previous = self._store.latest_message(exclude_batch=batch_id)
        messages = build_chat_messages(
            persona=self._persona,
            rules=self._rules,
            profile=profile.content if profile else None,
            history=history,
            new_messages=new,
            memories=memories,
            commitments=commitments,
            now=self._now(),
            last_chat_at=previous.created_at if previous else None,
        )
        try:
            data = await self._llm.complete_json(
                purpose="chat", model=self._settings.chat_model, messages=messages, max_tokens=4000
            )
            reply = parse_reply(data)
        except LLMBadJSON as e:
            raw = e.raw.strip()
            if raw.startswith("{"):
                salvaged = salvage_messages(raw)  # 多半是输出被截断了
                reply = Reply("", "unknown", salvaged) if salvaged else None
            else:
                reply = Reply("", "unknown", [raw]) if raw else None
        except LLMError as e:
            log.exception("聊天调用失败")
            await self._broadcast({"type": "error", "message": e.user_message})
            return
        if reply is None:
            await self._broadcast(ERROR_EVENT)
            return
        self._store.touch_recalled({m.id for m in memories} | {c.id for c in commitments})
        await self._deliver(batch_id, reply)

    async def _deliver(self, batch_id: str, reply: Reply) -> None:
        for i, text in enumerate(reply.messages):
            await self._broadcast({"type": "typing"})
            await self._sleep(bubble_delay(text))
            meta = {"mood_read": reply.mood_read, "approach": reply.approach} if i == 0 else None
            msg = self._store.add_message("assistant", text, batch_id=batch_id, meta=meta)
            if i == 0 and self._batch_id == batch_id:
                self._batch_id = None  # 发出第一条，这一批就算回复了
                self._batch_started_at = None
            await self._broadcast(
                {"type": "bubble", "id": msg.id, "text": text, "created_at": msg.created_at.isoformat()}
            )
        self._on_activity()
