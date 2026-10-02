"""组装发给聊天模型的上下文，并解析它的回复（spec §4 第 4、5 步）。"""

from dataclasses import dataclass
from datetime import datetime

from mira.store import APPROACHES, Memory, Message

MAX_BUBBLES = 6
_WEEKDAYS = "一二三四五六日"
_STATUS = {"open": "进行中", "done": "完成", "dropped": "放弃", "overdue": "逾期"}


def format_gap(last: datetime | None, now: datetime) -> str:
    if last is None:
        return "第一次聊天"
    seconds = (now - last).total_seconds()
    if seconds < 3600:
        return "刚刚还在聊"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时"
    return f"{int(seconds // 86400)} 天"


def format_memory(m: Memory) -> str:
    if m.type == "person":
        label = f"人物·{m.subject}" if m.subject else "人物"
    elif m.type == "commitment":
        label = f"承诺·{_STATUS.get(m.status or 'open', m.status)}"
        if m.due_at:
            label += f"·截止 {m.due_at.isoformat()}"
    elif m.type == "pattern":
        label = f"模式·出现 {len(m.evidence)} 次"
    elif m.type == "episode":
        label = f"事件·{m.created_at.strftime('%m-%d')}"
    else:
        label = "事实"
    return f"#{m.id} [{label}] {m.content}"


def _format_now(now: datetime) -> str:
    return f"{now.strftime('%Y-%m-%d')} 周{_WEEKDAYS[now.weekday()]} {now.strftime('%H:%M')}"


def _lines(items: list[Memory]) -> str:
    return "\n".join(f"- {format_memory(m)}" for m in items) if items else "（无）"


def build_chat_messages(
    *,
    persona: str,
    rules: str,
    profile: str | None,
    history: list[Message],
    new_messages: list[Message],
    memories: list[Memory],
    commitments: list[Memory],
    now: datetime,
    last_chat_at: datetime | None,
) -> list[dict]:
    system = f"{persona}\n\n{rules}"
    if profile:
        system += f"\n\n【核心档案】\n{profile}"
    out: list[dict] = [{"role": "system", "content": system}]

    def append(role: str, content: str) -> None:
        if out[-1]["role"] == role:
            out[-1]["content"] += "\n" + content
        else:
            out.append({"role": role, "content": content})

    for m in history:
        append(m.role, m.content)

    commitment_ids = {c.id for c in commitments}
    related = [m for m in memories if m.id not in commitment_ids]
    final = (
        "【背景】\n"
        f"现在：{_format_now(now)}\n"
        f"距离上次聊天：{format_gap(last_chat_at, now)}\n"
        f"相关记忆：\n{_lines(related)}\n"
        f"进行中的承诺：\n{_lines(commitments)}\n\n"
        "【新消息】\n" + "\n".join(m.content for m in new_messages)
    )
    append("user", final)
    return out


@dataclass
class Reply:
    mood_read: str
    approach: str
    messages: list[str]


def parse_reply(data: dict) -> Reply | None:
    raw = data.get("messages")
    if not isinstance(raw, list):
        return None
    messages = [m.strip() for m in raw if isinstance(m, str) and m.strip()][:MAX_BUBBLES]
    if not messages:
        return None
    approach = data.get("approach")
    mood = data.get("mood_read")
    return Reply(
        mood_read=mood if isinstance(mood, str) else "",
        approach=approach if approach in APPROACHES else "unknown",
        messages=messages,
    )
