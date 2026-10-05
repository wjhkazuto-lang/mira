from datetime import date, datetime

import numpy as np

from mira.config import load_settings
from mira.fakes import FakeLLM, HashEmbedder
from mira.llm import LLMBadJSON
from mira.proactive import MAX_BUBBLES, Candidate, Decision, ProactiveEngine, parse_decision
from mira.retriever import Retriever

V = np.ones(4, dtype=np.float32) / 2
SETTINGS = load_settings({"MIRA_FAKE": "1", "PROACTIVE": "1"})
CANDS = [
    Candidate("commitment", "commitment", 12, "#12 [承诺·进行中·截止 2026-10-02] 交材料（已逾期 3 天）"),
    Candidate("checkin", "episode", 5, "#5 [事件·10-04] 面试没过很沮丧"),
]
BASE = {"speak": True, "kind": "commitment", "ref_id": 12, "messages": ["在吗"]}


def add(store, type="fact", content="喜欢火锅", **kw):
    return store.add_memory(type, content, vector=V, actor="writer", **kw)


def at(clock, iso):
    clock.t = datetime.fromisoformat(iso)


class Busy:
    def busy(self):
        return False


def make(store, clock, llm):
    return ProactiveEngine(
        store=store, llm=llm, retriever=Retriever(store, HashEmbedder()), settings=SETTINGS,
        engine=Busy(), persona="人设", rules="规则", now=clock.now,
    )


# ---------- parse_decision ----------

def test_parse_valid():
    d = parse_decision(
        {"speak": True, "kind": "commitment", "ref_id": 12, "reason": "到期了",
         "messages": ["材料的事怎么样了？"], "expression": "gentle"},
        CANDS,
    )
    assert d == Decision(True, "commitment", "到期了", 12, ["材料的事怎么样了？"], "gentle")


def test_parse_limits_to_three_bubbles():
    d = parse_decision({**BASE, "messages": ["一", "二", "三", "四"]}, CANDS)
    assert d.messages == ["一", "二", "三"] and len(d.messages) == MAX_BUBBLES


def test_parse_clips_long_bubble():
    d = parse_decision({**BASE, "messages": ["很长" * 500]}, CANDS)
    assert "省略" in d.messages[0] and len(d.messages[0]) < 400


def test_parse_rejections():
    assert parse_decision({**BASE, "speak": False}, CANDS) is None
    assert parse_decision({**BASE, "messages": []}, CANDS) is None
    assert parse_decision({**BASE, "messages": [None, 3, "  "]}, CANDS) is None
    assert parse_decision({**BASE, "kind": "letter"}, CANDS) is None  # kind 不在候选里
    assert parse_decision({**BASE, "ref_id": 99}, CANDS) is None  # id 对不上
    assert parse_decision({**BASE, "ref_id": None}, CANDS) is None  # 承诺必须有编号
    assert parse_decision({**BASE, "ref_id": True}, CANDS) is None  # bool 不算 int
    assert parse_decision({**BASE, "ref_id": 12.0}, CANDS) is None  # float 也不行
    assert parse_decision({}, CANDS) is None
    assert parse_decision(None, CANDS) is None


def test_parse_missing_kind_allows_no_ref():
    cands = [Candidate("missing", None, None, "对方已经 3 天没说话了")]
    d = parse_decision({"speak": True, "kind": "missing", "ref_id": None, "messages": ["好久不见"]}, cands)
    assert d is not None and d.kind == "missing" and d.ref_id is None and d.reason == ""


# ---------- decide ----------

async def test_decide_speaks_with_chat_model(store, clock):
    at(clock, "2026-10-05T09:00:00+08:00")
    store.add_message("user", "材料我还没写")
    add(store, "commitment", "交材料", status="open", due_at=date(2026, 10, 2))
    llm = FakeLLM([{"speak": True, "kind": "commitment", "ref_id": 1, "reason": "逾期了",
                    "messages": ["材料的事，怎么样了？"]}])

    d = await make(store, clock, llm).decide()

    assert d is not None and d.ref_id == 1 and d.messages == ["材料的事，怎么样了？"]
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["purpose"] == "proactive" and call["model"] == SETTINGS.chat_model
    assert call["temperature"] == SETTINGS.chat_temperature
    assert call["messages"][0]["role"] == "system" and "人设" in call["messages"][0]["content"]
    assert "交材料" in call["messages"][1]["content"]  # 候选写进了提示词


async def test_decide_without_candidates_makes_no_call(store, clock):
    at(clock, "2026-10-05T09:00:00+08:00")
    store.add_message("user", "hi")
    llm = FakeLLM([])  # 若被调用会 IndexError
    assert await make(store, clock, llm).decide() is None
    assert llm.calls == []


async def test_decide_with_model_silence(store, clock):
    at(clock, "2026-10-05T09:00:00+08:00")
    store.add_message("user", "hi")
    add(store, "commitment", "交材料", status="open", due_at=date(2026, 10, 2))
    llm = FakeLLM([{"speak": False}])
    assert await make(store, clock, llm).decide() is None
    assert len(llm.calls) == 1


async def test_decide_bad_json_is_silent(store, clock):
    at(clock, "2026-10-05T09:00:00+08:00")
    store.add_message("user", "hi")
    add(store, "commitment", "交材料", status="open", due_at=date(2026, 10, 2))
    llm = FakeLLM([LLMBadJSON(raw="<html>")])
    assert await make(store, clock, llm).decide() is None
