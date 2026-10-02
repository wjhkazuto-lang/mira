from datetime import date

import numpy as np

from evals.run_evals import check, load_scenarios, seed

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
