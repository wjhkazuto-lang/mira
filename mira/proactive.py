"""主动关心：便宜规则先判断，值得了才调模型开口（spec §3）。"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from mira import clock
from mira.config import Settings, parse_quiet_hours
from mira.context import format_memory

log = logging.getLogger(__name__)

MIN_USER_GAP = timedelta(hours=4)  # 距最后一条用户消息不足这么久就不开口（人在场）
EVAL_GAP = timedelta(hours=4)  # 两次"问模型值不值得说"之间至少隔这么久
IDLE_GAP = timedelta(days=2)  # 多久没聊算"好久没聊"
EPISODE_WINDOW = timedelta(hours=72)  # 情绪回访只看最近这么久的事件
GOAL_STALE = timedelta(days=14)  # 目标多久没动静算"搁置"
COMMITMENT_COOLDOWN = timedelta(days=3)  # 同一条承诺多久内不重复提
GOAL_COOLDOWN = timedelta(days=14)  # 同一个目标多久内不重复推

CANDIDATE_KINDS = ("commitment", "missing", "checkin", "goal")


def quiet_now(hours: str, now: datetime) -> bool:
    """当前是否处于安静时段。hours 形如 "22-8"（可跨夜）。"""
    start, end = parse_quiet_hours(hours)
    if start <= end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end


@dataclass
class Candidate:
    """一个可以考虑开口的理由。text 是给模型看的一行（含 #编号）。"""

    kind: str
    ref_type: str | None
    ref_id: int | None
    text: str


class ProactiveEngine:
    def __init__(
        self,
        *,
        store,
        llm,
        retriever,
        settings: Settings,
        engine,
        persona: str,
        rules: str,
        notifier=None,
        now: Callable[[], datetime] = clock.now,
    ):
        self._store = store
        self._llm = llm
        self._retriever = retriever
        self._settings = settings
        self._engine = engine  # ChatEngine：用 busy() 判断是不是正在聊天
        self._persona = persona
        self._rules = rules
        self._notifier = notifier
        self._now = now

    # ---------- 闸门（纯规则，不花钱） ----------

    def ready(self) -> bool:
        if not self._settings.proactive:
            return False
        now = self._now()
        if quiet_now(self._settings.proactive_quiet_hours, now):
            return False
        last_user = self._store.latest_user_message()
        if last_user is None:  # 全新安装，还没聊过天
            return False
        if now - last_user.created_at < MIN_USER_GAP:  # 人在场，聊天本身会说到该说的
            return False
        last_eval = self._store.get_job_last_run("proactive")
        if last_eval is not None and now - last_eval < EVAL_GAP:
            return False
        if self._store.proactive_count_on(now.date()) >= self._settings.proactive_max_per_day:
            return False
        last_spoke = self._store.last_proactive_at()
        if last_spoke is not None and last_spoke >= last_user.created_at:  # 说了没人应，不追问
            return False
        if self._engine.busy():  # 正在回复/重试，不插话
            return False
        return True

    # ---------- 候选（SQL，免费） ----------

    def collect(self) -> list[Candidate]:
        now = self._now()
        out: list[Candidate] = []

        for m in self._store.open_commitments():
            if m.due_at is None or m.due_at > now.date():
                continue
            last = self._store.last_proactive_ref("commitment", m.id)
            if last is not None and now - last < COMMITMENT_COOLDOWN:
                continue
            overdue = (now.date() - m.due_at).days
            when = "今天到期" if overdue == 0 else f"已逾期 {overdue} 天"
            out.append(Candidate("commitment", "commitment", m.id, f"{format_memory(m)}（{when}）"))

        last_user = self._store.latest_user_message()
        if last_user is not None and now - last_user.created_at >= IDLE_GAP:
            days = (now - last_user.created_at).days
            out.append(Candidate("missing", None, None, f"对方已经 {days} 天没说话了"))

        for m in self._store.list_memories("episode"):
            if now - m.created_at > EPISODE_WINDOW:
                continue
            if self._store.last_proactive_ref("episode", m.id) is not None:  # 每个事件只回访一次
                continue
            out.append(Candidate("checkin", "episode", m.id, format_memory(m)))

        for m in self._store.open_goals():
            if now - m.updated_at < GOAL_STALE:
                continue
            last = self._store.last_proactive_ref("goal", m.id)
            if last is not None and now - last < GOAL_COOLDOWN:
                continue
            out.append(Candidate("goal", "goal", m.id, format_memory(m)))

        return out
