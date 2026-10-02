import pytest


def test_add_and_list_in_order(store):
    for t in ["一", "二", "三"]:
        store.add_message("user", t)
    msgs = store.list_messages()
    assert [m.id for m in msgs] == sorted(m.id for m in msgs)
    assert [m.content for m in msgs] == ["一", "二", "三"]


def test_list_before_paginates(store):
    for i in range(5):
        store.add_message("user", str(i))
    assert [m.id for m in store.list_messages(before_id=4, limit=2)] == [2, 3]


def test_recent_respects_budget(store):
    for i in range(5):
        store.add_message("user", f"{i}" * 10)
    recent = store.recent_messages(25)
    assert [m.content for m in recent] == ["3" * 10, "4" * 10]


def test_recent_excludes_batch(store):
    store.add_message("user", "旧", batch_id="b0")
    store.add_message("user", "新", batch_id="b1")
    assert [m.content for m in store.recent_messages(100, exclude_batch="b1")] == ["旧"]


def test_messages_in_batch(store):
    store.add_message("user", "a", batch_id="b1")
    store.add_message("user", "b", batch_id="b2")
    store.add_message("user", "c", batch_id="b1")
    assert [m.content for m in store.messages_in_batch("b1")] == ["a", "c"]


def test_meta_roundtrip(store, clock):
    m = store.add_message("assistant", "嗯", batch_id="b", meta={"approach": "comfort"})
    got = store.list_messages()[0]
    assert got.meta == {"approach": "comfort"} and got.role == "assistant"
    assert got.created_at == clock.now() and got.id == m.id and got.batch_id == "b"


def test_unprocessed_and_mark(store):
    a = store.add_message("user", "a")
    b = store.add_message("assistant", "b")
    assert [m.id for m in store.unprocessed_messages()] == [a.id, b.id]
    store.mark_processed([a.id])
    assert [m.id for m in store.unprocessed_messages()] == [b.id]


def test_last_unanswered_batch(store):
    assert store.last_unanswered_batch() is None
    store.add_message("user", "在吗", batch_id="b1")
    assert store.last_unanswered_batch() == "b1"
    store.add_message("assistant", "在", batch_id="b1")
    assert store.last_unanswered_batch() is None


def test_transaction_rolls_back(store):
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.add_message("user", "会被回滚")
            raise RuntimeError("boom")
    assert store.list_messages() == []


def test_file_db_creates_parent_dir(tmp_path):
    from mira.store import Store

    s = Store(tmp_path / "nested" / "mira.db")
    s.add_message("user", "hi")
    assert (tmp_path / "nested" / "mira.db").exists()


def test_get_messages_by_ids(store):
    a = store.add_message("user", "a")
    store.add_message("user", "b")
    c = store.add_message("user", "c")
    assert [m.content for m in store.get_messages([c.id, a.id, 999])] == ["a", "c"]
    assert store.get_messages([]) == []
