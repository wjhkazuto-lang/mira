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


def test_recent_clips_oversized_message_keeps_earlier(store):
    store.add_message("user", "早" * 10)
    store.add_message("user", "长" * 1000)
    store.add_message("user", "近" * 10)
    store.add_message("user", "长" * 5000)
    store.add_message("user", "近" * 10)
    recent = store.recent_messages(1000)  # 单条上限 250 字
    assert [m.content[0] for m in recent] == ["早", "长", "近", "长", "近"]
    assert "省略 750 字" in recent[1].content and "省略 4750 字" in recent[3].content


def test_latest_message_excluding_batch(store, clock):
    store.add_message("user", "旧", batch_id="a")
    clock.advance(5)
    store.add_message("user", "新", batch_id="b")
    assert store.latest_message(exclude_batch="b").content == "旧"
    assert store.latest_message(exclude_batch="a").content == "新"
    assert store.latest_message() .content == "新"


def test_unprocessed_user_messages_ignores_assistant(store):
    store.add_message("user", "hi")
    store.add_message("assistant", "在", meta={"proactive": {"kind": "missing"}})
    assert [m.role for m in store.unprocessed_user_messages()] == ["user"]
    assert store.pending_user_message_count() == 1
    store.mark_processed([1])
    assert store.pending_user_message_count() == 0
    assert store.unprocessed_user_messages() == []


def test_latest_user_message_skips_assistant(store):
    assert store.latest_user_message() is None
    store.add_message("user", "一")
    store.add_message("assistant", "二")
    assert store.latest_user_message().content == "一"


def test_latest_user_message_excludes_batch(store):
    store.add_message("user", "旧", batch_id="a")
    store.add_message("user", "新", batch_id="b")
    assert store.latest_user_message(exclude_batch="b").content == "旧"
    assert store.latest_user_message(exclude_batch="a").content == "新"
