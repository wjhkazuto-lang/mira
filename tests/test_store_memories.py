from datetime import date

import numpy as np
import pytest

V = np.ones(4, dtype=np.float32) / 2


def add(store, type="fact", content="喜欢火锅", **kw):
    return store.add_memory(type, content, vector=V, actor="writer", **kw)


def test_memory_roundtrip_with_vector(store, clock):
    m = add(store, "commitment", "周五前改完简历", status="open", due_at=date(2026, 10, 9),
            importance=4, evidence=[1, 2], source_message_ids=[7])
    got = store.get_memory(m.id)
    assert (got.type, got.content, got.status, got.due_at, got.importance) == (
        "commitment", "周五前改完简历", "open", date(2026, 10, 9), 4)
    assert got.evidence == [1, 2] and got.source_message_ids == [7]
    assert got.user_locked is False and got.superseded_by is None
    assert got.created_at == clock.now() and got.last_recalled_at is None
    assert np.allclose(store.load_vectors([m.id])[m.id], V)


def test_add_rejects_bad_type_and_importance(store):
    with pytest.raises(ValueError):
        add(store, type="dream")
    with pytest.raises(ValueError):
        add(store, importance=0)
    with pytest.raises(ValueError):
        add(store, "commitment", status="maybe")


def test_update_logs_before_after(store, clock):
    m = add(store)
    clock.advance(60)
    new_v = np.zeros(4, dtype=np.float32)
    got = store.update_memory(m.id, actor="user", vector=new_v, content="喜欢麻辣火锅", user_locked=True)
    assert got.content == "喜欢麻辣火锅" and got.user_locked and got.updated_at == clock.now()
    entry = store.recent_log()[0]
    assert (entry.op, entry.actor, entry.memory_id) == ("update", "user", m.id)
    assert entry.before["content"] == "喜欢火锅" and entry.after["content"] == "喜欢麻辣火锅"
    assert np.allclose(store.load_vectors([m.id])[m.id], new_v)


def test_update_rejects_unknown_field(store):
    m = add(store)
    with pytest.raises(ValueError):
        store.update_memory(m.id, actor="user", created_at="x")


def test_delete_removes_vector_and_logs(store):
    m = add(store)
    store.delete_memory(m.id, actor="user")
    assert store.get_memory(m.id) is None
    assert m.id not in store.load_vectors()
    entry = store.recent_log()[0]
    assert entry.op == "delete" and entry.before["content"] == "喜欢火锅" and entry.after is None


def test_list_filters_type_and_superseded(store):
    a = add(store, "fact", "在 A 公司")
    b = add(store, "fact", "在 B 公司")
    add(store, "person", "小林", subject="小林")
    store.update_memory(a.id, actor="writer", superseded_by=b.id)
    assert {m.id for m in store.list_memories("fact")} == {a.id, b.id}
    assert [m.id for m in store.list_memories("fact", include_superseded=False)] == [b.id]
    assert len(store.list_memories()) == 3


def test_open_commitments(store):
    late = add(store, "commitment", "晚", status="open", due_at=date(2026, 10, 20))
    early = add(store, "commitment", "早", status="overdue", due_at=date(2026, 10, 1))
    nodate = add(store, "commitment", "无日期", status="open")
    add(store, "commitment", "完成了", status="done")
    add(store, "commitment", "放弃了", status="dropped")
    old = add(store, "commitment", "被替代", status="open")
    store.update_memory(old.id, actor="writer", superseded_by=late.id)
    assert [m.id for m in store.open_commitments()] == [early.id, late.id, nodate.id]


def test_profile_versions(store, clock):
    assert store.current_profile() is None
    p1 = store.add_profile("第一版", "reflector")
    clock.advance(1)
    p2 = store.add_profile("第二版", "user")
    assert store.current_profile().content == "第二版"
    assert [p.id for p in store.list_profiles()] == [p2.id, p1.id]
    assert store.get_profile(p1.id).source == "reflector"


def test_job_state_roundtrip(store, clock):
    assert store.get_job_last_run("reflector") is None
    store.set_job_last_run("reflector", clock.now())
    clock.advance(10)
    store.set_job_last_run("reflector", clock.now())
    assert store.get_job_last_run("reflector") == clock.now()


def test_touch_recalled(store, clock):
    m = add(store)
    store.touch_recalled([m.id])
    assert store.get_memory(m.id).last_recalled_at == clock.now()


def test_memory_log_newest_first(store):
    a = add(store)
    store.update_memory(a.id, actor="writer", importance=5)
    assert [e.op for e in store.recent_log(limit=2)] == ["update", "add"]


def test_deleted_ids_never_reused(store):
    a = add(store)
    b = add(store)
    store.delete_memory(b.id, actor="user")
    assert add(store).id > b.id


def test_schema_version_set(store):
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_idea_and_goal_types(store):
    idea = add(store, "idea", "想学吉他")
    goal = add(store, "goal", "考研", status="open")
    assert (idea.type, idea.status, goal.type, goal.status) == ("idea", None, "goal", "open")


def test_open_goals(store):
    a = add(store, "goal", "考研", status="open")
    add(store, "goal", "减肥", status="done")
    add(store, "commitment", "周五交报告", status="open")
    old = add(store, "goal", "旧目标", status="open")
    store.update_memory(old.id, actor="writer", superseded_by=a.id)
    assert [m.id for m in store.open_goals()] == [a.id]


def test_change_memory_type(store):
    m = add(store, "fact", "想靠 AI 挣钱")
    got = store.update_memory(m.id, actor="user", type="idea")
    assert got.type == "idea"
    with pytest.raises(ValueError):
        store.update_memory(m.id, actor="user", type="dream")


def test_delete_restores_memories_it_superseded(store):
    old, other = add(store, content="喜欢猫"), add(store, content="无关")
    new = add(store, content="喜欢狗")
    store.update_memory(old.id, actor="writer", superseded_by=new.id)
    store.delete_memory(new.id, actor="user")
    assert store.get_memory(old.id).superseded_by is None
    assert store.get_memory(other.id).superseded_by is None
    entry = next(e for e in store.recent_log() if e.memory_id == old.id)
    assert entry.actor == "user" and entry.op == "update"


def test_delete_rolls_back_restore_on_failure(store):
    old, new = add(store, content="喜欢猫"), add(store, content="喜欢狗")
    store.update_memory(old.id, actor="writer", superseded_by=new.id)
    with pytest.raises(KeyError):
        store.delete_memory(999, actor="user")
    assert store.get_memory(old.id).superseded_by == new.id
