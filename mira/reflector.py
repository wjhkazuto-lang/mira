"""每日反思：标记逾期承诺、维护模式、重写核心档案（spec §5.5）。"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from mira import clock
from mira.config import Settings
from mira.context import format_memory
from mira.embedder import Embedder
from mira.llm import LLM
from mira.prompts import render
from mira.store import Memory, Store
from mira.textutil import estimate_tokens
from mira.writer import parse_id

PROFILE_MAX_TOKENS = 2500
RECENT_DAYS = 7
_WEEKDAYS = "一二三四五六日"


@dataclass
class ReflectResult:
    applied: int
    skipped: list[str]
    overdue_marked: int
    profile_updated: bool


class Reflector:
    def __init__(
        self,
        store: Store,
        embedder: Embedder,
        llm: LLM,
        settings: Settings,
        now: Callable[[], datetime] = clock.now,
    ):
        self._store = store
        self._embedder = embedder
        self._llm = llm
        self._settings = settings
        self._now = now

    async def run(self) -> ReflectResult:
        now = self._now()
        overdue = self._mark_overdue(now)
        recent = [
            m
            for m in self._store.list_memories("episode", include_superseded=False)
            if now - m.created_at <= timedelta(days=RECENT_DAYS)
        ]
        if not recent:
            return ReflectResult(0, [], overdue, False)

        episode_ids = {m.id for m in self._store.list_memories("episode")}
        patterns = [
            replace(p, evidence=[e for e in p.evidence if e in episode_ids])
            for p in self._store.list_memories("pattern", include_superseded=False)
        ]
        profile = self._store.current_profile()
        prompt = render(
            "reflector",
            today=f"{now.strftime('%Y-%m-%d')} 周{_WEEKDAYS[now.weekday()]}",
            episodes="\n".join(format_memory(m) for m in sorted(recent, key=lambda m: m.id)),
            patterns="\n".join(f"{format_memory(p)}（证据：{' '.join(f'#{e}' for e in p.evidence)}）" for p in patterns)
            or "（无）",
            commitments="\n".join(format_memory(c) for c in self._store.open_commitments()) or "（无）",
            profile=profile.content if profile else "（还没有）",
        )
        data = await self._llm.complete_json(
            purpose="reflector",
            model=self._settings.reflect_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000,
        )

        applied = 0
        skipped: list[str] = []
        raw_ops = data.get("patterns")
        with self._store.transaction():
            # 模型调用期间用户可能删了事件，证据要按此刻的数据重新核对
            episode_ids = {m.id for m in self._store.list_memories("episode")}
            for op in raw_ops if isinstance(raw_ops, list) else []:
                why = self._apply(op, episode_ids) if isinstance(op, dict) else "模式操作格式不对"
                if why:
                    skipped.append(why)
                else:
                    applied += 1
            new_profile = data.get("profile").strip() if isinstance(data.get("profile"), str) else ""
            profile_updated = False
            latest = self._store.current_profile()
            if (latest.id if latest else None) != (profile.id if profile else None):
                skipped.append("反思期间核心档案被修改过，保留新版，不覆盖")
            elif not new_profile:
                skipped.append("没有给出新的核心档案")
            elif estimate_tokens(new_profile) > PROFILE_MAX_TOKENS:
                skipped.append(f"新的核心档案太长（{len(new_profile)} 字），保留旧版")
            else:
                self._store.add_profile(new_profile, "reflector")
                profile_updated = True
        return ReflectResult(applied, skipped, overdue, profile_updated)

    def _mark_overdue(self, now: datetime) -> int:
        today = now.date()
        marked = 0
        with self._store.transaction():
            for c in self._store.open_commitments():
                if c.status == "open" and c.due_at and c.due_at < today:
                    self._store.update_memory(c.id, actor="reflector", status="overdue")
                    marked += 1
        return marked

    def _pattern(self, id) -> Memory | None:
        pid = parse_id(id)
        m = self._store.get_memory(pid) if pid is not None else None
        return m if m and m.type == "pattern" and m.superseded_by is None else None

    def _apply(self, op: dict, episode_ids: set[int]) -> str | None:
        """执行一个模式操作；不合法时返回跳过原因。"""

        def evidence(raw) -> list[int]:
            ids = [parse_id(e) for e in raw] if isinstance(raw, list) else []
            return list(dict.fromkeys(e for e in ids if e in episode_ids))

        kind = op.get("op")
        if kind == "add":
            content = op.get("content").strip() if isinstance(op.get("content"), str) else ""
            ev = evidence(op.get("evidence"))
            if not content:
                return "新模式内容为空"
            if len(ev) < 2:
                return f"模式「{content}」的证据不足 2 个事件"
            self._store.add_memory(
                "pattern", content, vector=self._embedder.embed([content])[0], actor="reflector", evidence=ev
            )
            return None
        if kind == "add_evidence":
            p = self._pattern(op.get("id"))
            if p is None:
                return f"#{op.get('id')} 不是现存的模式"
            merged = list(dict.fromkeys(p.evidence + evidence(op.get("evidence"))))
            if merged == p.evidence:
                return f"模式 #{p.id} 没有新证据"
            self._store.update_memory(p.id, actor="reflector", evidence=merged)
            return None
        if kind == "merge":
            keep = self._pattern(op.get("keep_id"))
            drops = [self._pattern(d) for d in op.get("drop_ids", [])] if isinstance(op.get("drop_ids"), list) else []
            if keep is None or not drops or any(d is None for d in drops):
                return "合并的对象不是现存的模式"
            if any(d.id == keep.id for d in drops):
                return "不能把模式合并到它自己"
            if keep.user_locked or any(d.user_locked for d in drops):
                return "合并涉及你锁定的模式，已跳过"
            merged = list(keep.evidence)
            for d in drops:
                merged += d.evidence
                self._store.update_memory(d.id, actor="reflector", superseded_by=keep.id)
            self._store.update_memory(keep.id, actor="reflector", evidence=list(dict.fromkeys(merged)))
            return None
        return f"未知的模式操作 {kind!r}"
