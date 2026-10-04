"""记忆写入器：把一段未处理的对话变成经过校验的记忆操作（spec §5.4）。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from mira import clock
from mira.config import Settings
from mira.context import format_memory
from mira.embedder import Embedder
from mira.llm import LLM
from mira.prompts import render
from mira.retriever import Retriever
from mira.store import Message, Store
from mira.textutil import clip_text

WRITER_TYPES = ("fact", "person", "idea", "goal", "commitment")
TRACKED_TYPES = ("goal", "commitment")  # 有状态（进行中/完成/放弃）的类型
WRITER_STATUSES = ("open", "done", "dropped")
WRITER_CHUNK_CHARS = 12000  # 每次最多处理这么多字的对话，剩下的下次再处理
WRITER_MESSAGE_CHARS = 2000  # 单条消息最多保留这么多字
_WEEKDAYS = "一二三四五六日"


@dataclass
class WriteResult:
    applied: int
    skipped: list[str]
    episode_id: int | None


def _importance(v) -> int:
    try:
        return min(max(int(v), 1), 5)
    except (TypeError, ValueError):
        return 3


def _due(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _content(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _new_fields(raw) -> tuple[dict | None, str | None]:
    """校验 add / supersede.new 的字段，返回 (规范化字段, 跳过原因)。"""
    if not isinstance(raw, dict):
        return None, "新记忆格式不对"
    if raw.get("type") not in WRITER_TYPES:
        return None, f"不能新增类型 {raw.get('type')!r}"
    content = _content(raw.get("content"))
    if not content:
        return None, "新记忆内容为空"
    fields = {
        "type": raw["type"],
        "content": content,
        "subject": _content(raw.get("subject")) or None,
        "importance": _importance(raw.get("importance", 3)),
        "due_at": _due(raw.get("due_at")) if raw["type"] == "commitment" else None,  # 只有承诺有截止日期
        "status": "open" if raw["type"] in TRACKED_TYPES else None,
    }
    return fields, None


def parse_id(v) -> int | None:
    """接受 12、"12"、"#12"（模型常把编号写成字符串）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("#").isdigit():
        return int(v.strip().lstrip("#"))
    return None


def _editable(store: Store, id) -> tuple[object, str | None]:
    pid = parse_id(id)
    m = store.get_memory(pid) if pid is not None else None
    if m is None:
        return None, f"记忆 #{id} 不存在"
    if m.superseded_by is not None:
        return None, f"记忆 #{id} 已过时"
    if m.type not in WRITER_TYPES:
        return None, f"记忆 #{id} 的类型 {m.type} 不能由写入器修改"
    return m, None


def validate_ops(ops: object, store: Store) -> tuple[list[dict], list[str]]:
    valid: list[dict] = []
    skipped: list[str] = []
    touched: set[int] = set()  # 这一批里已经改过的记忆，后面对同一条的操作跳过
    for op in ops if isinstance(ops, list) else []:
        kind = op.get("op") if isinstance(op, dict) else None
        if kind in ("update", "supersede", "set_status") and parse_id(op.get("id")) in touched:
            skipped.append(f"同一批里已经改过 #{parse_id(op.get('id'))}，跳过重复的操作")
            continue
        if kind == "add":
            fields, why = _new_fields(op)
            if why:
                skipped.append(why)
            else:
                valid.append({"op": "add", "fields": fields})
        elif kind in ("update", "supersede"):
            m, why = _editable(store, op.get("id"))
            if why is None and m.user_locked:
                why = f"记忆 #{m.id} 已被你锁定，不能修改"
            if why:
                skipped.append(why)
                continue
            if kind == "supersede":
                fields, why = _new_fields(op.get("new"))
                if why:
                    skipped.append(why)
                else:
                    valid.append({"op": "supersede", "id": m.id, "fields": fields})
                    touched.add(m.id)
                continue
            fields = {}
            if "content" in op:
                fields["content"] = _content(op["content"])
                if not fields["content"]:
                    skipped.append(f"更新 #{m.id} 的内容为空")
                    continue
            if "importance" in op:
                fields["importance"] = _importance(op["importance"])
            if "due_at" in op:
                fields["due_at"] = _due(op["due_at"])
            if "subject" in op:
                fields["subject"] = _content(op["subject"]) or None
            if not fields:
                skipped.append(f"更新 #{m.id} 没有任何改动")
            else:
                valid.append({"op": "update", "id": m.id, "fields": fields})
                touched.add(m.id)
        elif kind == "set_status":
            pid = parse_id(op.get("id"))
            m = store.get_memory(pid) if pid is not None else None
            if m is None or m.type not in TRACKED_TYPES:
                skipped.append(f"#{op.get('id')} 不是存在的目标或承诺")
            elif op.get("status") not in WRITER_STATUSES:
                skipped.append(f"状态 {op.get('status')!r} 不合法")
            else:
                valid.append({"op": "set_status", "id": m.id, "status": op["status"]})
                touched.add(m.id)
        else:
            skipped.append(f"未知操作 {kind!r}")
    return valid, skipped


def _line(m: Message) -> str:
    when = f"{m.created_at.strftime('%m-%d')} 周{_WEEKDAYS[m.created_at.weekday()]} {m.created_at.strftime('%H:%M')}"
    who = "我" if m.role == "user" else "Mira"
    return f"[{when}] {who}：{clip_text(m.content, WRITER_MESSAGE_CHARS)}"


class Writer:
    def __init__(
        self,
        store: Store,
        embedder: Embedder,
        retriever: Retriever,
        llm: LLM,
        settings: Settings,
        now: Callable[[], datetime] = clock.now,
    ):
        self._store = store
        self._embedder = embedder
        self._retriever = retriever
        self._llm = llm
        self._settings = settings
        self._now = now

    async def run(self) -> WriteResult | None:
        msgs = self._next_chunk()
        if not msgs:
            return None
        transcript = "\n".join(_line(m) for m in msgs)
        related = [s.memory for s in self._retriever.search(transcript, 15, WRITER_TYPES)]
        seen = {m.id for m in related}
        related += [m for m in self._store.open_goals() + self._store.open_commitments() if m.id not in seen]
        now = self._now()
        prompt = render(
            "writer",
            today=f"{now.strftime('%Y-%m-%d')} 周{_WEEKDAYS[now.weekday()]}",
            existing="\n".join(format_memory(m) for m in related) or "（无）",
            transcript=transcript,
        )
        data = await self._llm.complete_json(
            purpose="writer",
            model=self._settings.background_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8000,
        )

        ops, skipped = validate_ops(data.get("ops"), self._store)
        episode = data.get("episode") if isinstance(data.get("episode"), dict) else {}
        episode_text = _content(episode.get("content"))
        if not episode_text:
            skipped.append("episode 内容为空")

        texts = [op["fields"]["content"] for op in ops if "content" in op.get("fields", {})]
        if episode_text:
            texts.append(episode_text)
        vectors = iter(self._embedder.embed(texts)) if texts else iter(())
        source_ids = [m.id for m in msgs]
        episode_id = None

        with self._store.transaction():
            for op in ops:
                f = op.get("fields", {})
                if op["op"] in ("add", "supersede"):
                    new = self._store.add_memory(
                        f.pop("type"), f.pop("content"), vector=next(vectors), actor="writer",
                        source_message_ids=source_ids, **f,
                    )
                    if op["op"] == "supersede":
                        self._store.update_memory(op["id"], actor="writer", superseded_by=new.id)
                elif op["op"] == "update":
                    vec = next(vectors) if "content" in f else None
                    old = self._store.get_memory(op["id"])
                    sources = list(dict.fromkeys(old.source_message_ids + source_ids))
                    self._store.update_memory(op["id"], actor="writer", vector=vec, source_message_ids=sources, **f)
                else:
                    self._store.update_memory(op["id"], actor="writer", status=op["status"])
            if episode_text:
                episode_id = self._store.add_memory(
                    "episode", episode_text, vector=next(vectors), actor="writer",
                    importance=_importance(episode.get("importance", 2)), source_message_ids=source_ids,
                ).id
            self._store.mark_processed(source_ids)
        return WriteResult(applied=len(ops), skipped=skipped, episode_id=episode_id)

    def _next_chunk(self) -> list[Message]:
        """从最早的未处理消息开始，取不超过 WRITER_CHUNK_CHARS 字的一段（至少一条）。"""
        chunk: list[Message] = []
        size = 0
        for m in self._store.unprocessed_messages():
            cost = len(_line(m))
            if chunk and size + cost > WRITER_CHUNK_CHARS:
                break
            chunk.append(m)
            size += cost
        return chunk
