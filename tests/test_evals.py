from datetime import date

import numpy as np

from evals.run_evals import check, load_proactive_scenarios, load_scenarios, run_proactive_scenario, seed
from mira.config import load_settings
from mira.fakes import FakeLLM, HashEmbedder

V = np.zeros(4, dtype=np.float32)


def test_all_scenarios_parse():
    scenarios = load_scenarios()
    assert len(scenarios) == 15
    for s in scenarios:
        assert {"name", "now", "messages", "run", "expect"} <= set(s)
        assert set(s["run"]) <= {"writer", "reflector"} and s["expect"]


def test_seed_creates_memories(store):
    ids = seed(store, [{"type": "commitment", "content": "改简历", "status": "open", "due_at": "2026-10-02"},
                       {"type": "episode", "content": "熬夜"}], V)
    assert store.get_memory(ids[0]).due_at == date(2026, 10, 2) and len(ids) == 2


def test_check_passes_and_fails(store):
    store.add_memory("commitment", "下周三面试", vector=V, actor="w", status="open", due_at=date(2026, 10, 7))
    old = store.add_memory("fact", "在 A 公司", vector=V, actor="w")
    new = store.add_memory("fact", "在 B 公司", vector=V, actor="w")
    store.update_memory(old.id, actor="w", superseded_by=new.id)
    store.add_memory("person", "大学室友", vector=V, actor="w", subject="小林")
    assert check(store, [
        {"type": "commitment", "contains": "面试", "due_at": "2026-10-07", "status": "open"},
        {"type": "fact", "contains": "B 公司"},
        {"type": "fact", "superseded_min": 1},
        {"type": "person", "subject": "小林"},
        {"type": "pattern", "max_count": 0},
    ]) == []
    failures = check(store, [
        {"type": "commitment", "contains": "面试", "due_at": "2026-10-08"},
        {"type": "fact", "max_count": 0},
        {"type": "episode", "min_count": 1},
        {"type": "fact", "contains": "C 公司"},
    ])
    assert len(failures) == 4


def test_all_proactive_scenarios_parse():
    scenarios = load_proactive_scenarios()
    assert len(scenarios) == 5
    for s in scenarios:
        assert {"name", "now", "messages", "run", "expect"} <= set(s)
        assert s["run"] in ("proactive", "weekly") and s["expect"]


async def test_proactive_scenarios_pass_with_a_well_behaved_model():
    """用脚本化的假模型跑一遍全部场景：验证场景本身和断言自洽（不花钱）。"""
    settings = load_settings({"MIRA_FAKE": "1", "PROACTIVE": "1"})
    embedder = HashEmbedder()
    scripts = {
        "承诺逾期 + 两天没聊 → 开口提这一件": [
            {"speak": True, "kind": "commitment", "ref_id": 1, "reason": "逾期了", "messages": ["材料的事，怎么样了？"]},
        ],
        "风平浪静 → 沉默且不调模型": [],  # 被调用会 IndexError，正好当"零调用"的断言
        "昨天的情绪事件 → 次日温暖回访": [
            {"speak": True, "kind": "checkin", "ref_id": 1, "reason": "回访", "messages": ["昨天那事，后来心里好点了吗？"]},
        ],
        "刚聊过（人在场）→ 沉默且不调模型": [],
        "每周信：一周素材 → 2–4 条": [
            {"messages": ["这周你把简历改完了，还跑了五公里。", "下周材料的事，记得留一天余量。"], "expression": "gentle"},
        ],
    }
    for s in load_proactive_scenarios():
        failures = await run_proactive_scenario(s, FakeLLM(scripts[s["name"]]), embedder, settings)
        assert failures == [], f"{s['name']}: {failures}"
