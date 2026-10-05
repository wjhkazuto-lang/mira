"""主动关心：便宜规则先判断，值得了才调模型开口（spec §3）。"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from mira import clock
from mira.config import Settings, parse_quiet_hours
from mira.context import format_gap, format_memory
from mira.llm import LLMBadJSON
from mira.prompts import render
from mira.textutil import clip_text

log = logging.getLogger(__name__)

MAX_BUBBLES = 3  # 主动消息最多几条气泡
BUBBLE_CHARS = 300  # 单条气泡最长多少字（模型偶尔会写长）
TRANSCRIPT_LINES = 20  # 给模型看的最近对话条数
_WEEKDAYS = "一二三四五六日"

MIN_USER_GAP = timedelta(hours=4)  # 距最后一条用户消息不足这么久就不开口（人在场）
EVAL_GAP = timedelta(hours=4)  # 两次"问模型值不值得说"之间至少隔这么久
IDLE_GAP = timedelta(days=2)  # 多久没聊算"好久没聊"
EPISODE_WINDOW = timedelta(hours=72)  # 情绪回访只看最近这么久的事件
GOAL_STALE = timedelta(days=14)  # 目标多久没动静算"搁置"
COMMITMENT_COOLDOWN = timedelta(days=3)  # 同一条承诺多久内不重复提
GOAL_COOLDOWN = timedelta(days=14)  # 同一个目标多久内不重复推

CANDIDATE_KINDS = ("commitment", "missing", "checkin", "goal")
REF_TYPES = {"commitment": "commitment", "missing": None, "checkin": "episode", "goal": "goal"}


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


@dataclass
class Decision:
    speak: bool
    kind: str
    reason: str
    ref_id: int | None
    messages: list[str]
    expression: str | None


def parse_decision(data: dict, candidates: list[Candidate]) -> Decision | None:
    """校验模型输出。任何对不上候选的地方都按沉默处理（None）。"""
    if not isinstance(data, dict) or not data.get("speak"):
        return None
    raw = data.get("messages")
    if not isinstance(raw, list):
        return None
    messages = [clip_text(m.strip(), BUBBLE_CHARS) for m in raw if isinstance(m, str) and m.strip()]
    messages = messages[:MAX_BUBBLES]
    if not messages:
        return None
    kind = data.get("kind")
    ref_raw = data.get("ref_id")
    ref_id = ref_raw if type(ref_raw) is int else None  # 不接受 bool/float/字符串
    if not any(c.kind == kind and c.ref_id == ref_id for c in candidates):
        return None
    reason = data.get("reason")
    expression = data.get("expression")
    return Decision(
        speak=True,
        kind=kind,
        reason=reason if isinstance(reason, str) else "",
        ref_id=ref_id,
        messages=messages,
        expression=expression if isinstance(expression, str) else None,
    )


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

    # ---------- 模型决策（唯一花钱的一步） ----------

    async def decide(self) -> Decision | None:
        """有候选时才调用一次模型，问它值不值得开口、怎么说。"""
        candidates = self.collect()
        if not candidates:
            return None
        now = self._now()
        last_user = self._store.latest_user_message()
        recent = self._store.recent_messages(self._settings.recent_history_tokens)
        query = "\n".join([c.text for c in candidates] + [m.content for m in recent[-6:]])
        memories = [s.memory for s in self._retriever.search(query, self._settings.retrieve_top_k)]
        profile = self._store.current_profile()
        prompt = render(
            "proactive",
            now=f"{now.strftime('%Y-%m-%d')} 周{_WEEKDAYS[now.weekday()]} {now.strftime('%H:%M')}",
            gap=format_gap(last_user.created_at if last_user else None, now),
            transcript="\n".join(
                f"{'你' if m.role == 'user' else 'Mira'}：{clip_text(m.content, BUBBLE_CHARS)}"
                for m in recent[-TRANSCRIPT_LINES:]
            ) or "（无）",
            candidates="\n".join(f"- {c.text}" for c in candidates),
            memories="\n".join(f"- {format_memory(m)}" for m in memories) or "（无）",
        )
        if profile:
            prompt += f"\n\n【核心档案】（文中的\"TA\"或\"你\"都指对方，不是你自己）\n{profile.content}"
        messages = [
            {"role": "system", "content": f"{self._persona}\n\n{self._rules}"},
            {"role": "user", "content": prompt},
        ]
        try:
            data = await self._llm.complete_json(
                purpose="proactive",
                model=self._settings.chat_model,
                messages=messages,
                max_tokens=1200,
                temperature=self._settings.chat_temperature,
            )
        except LLMBadJSON:
            log.warning("主动消息：模型返回的 JSON 解析不了，这次按沉默处理")
            return None
        return parse_decision(data, candidates)

    # ---------- 开口 ----------

    def _recheck(self) -> bool:
        """模型调用花了几秒，开口前把"人在场/没回应/在聊"再查一遍。"""
        now = self._now()
        last_user = self._store.latest_user_message()
        if last_user is None or now - last_user.created_at < MIN_USER_GAP:
            return False
        last_spoke = self._store.last_proactive_at()
        if last_spoke is not None and last_spoke >= last_user.created_at:
            return False
        return not self._engine.busy()

    async def run(self) -> None:
        """后台任务入口：判断 → 开口或沉默。沉默也正常结束（调度器会记下评估时间）。"""
        decision = await self.decide()
        if decision is None:
            return
        if not self._recheck():
            log.info("主动消息：刚要开口时用户回来了，放弃（kind=%s）", decision.kind)
            return
        meta = {"proactive": {"kind": decision.kind, "reason": decision.reason, "ref_id": decision.ref_id}}
        if not await self._engine.announce_proactive(decision.messages, meta=meta, expression=decision.expression):
            log.info("主动消息：发出前发现有正在进行的回复，放弃")
            return
        ref_type = REF_TYPES[decision.kind]
        self._store.add_proactive_log(decision.kind, ref_type, decision.ref_id)
        log.info("主动开口（%s）：%s", decision.kind, decision.reason)
        if self._notifier is not None and self._settings.notifications:
            try:
                self._notifier.notify("Mira", clip_text(decision.messages[0], 120))
            except Exception:
                log.warning("发送提醒失败", exc_info=True)
