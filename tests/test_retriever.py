import pytest

from mira.fakes import HashEmbedder
from mira.retriever import Retriever, keyword_score, score

EMB = HashEmbedder()


def add(store, type, content, **kw):
    return store.add_memory(type, content, vector=EMB.embed([content])[0], actor="writer", **kw)


def retriever(store, clock):
    return Retriever(store, EMB, now=clock.now)


def test_score_formula():
    assert score(1, 1, 5, 0) == pytest.approx(1.0)
    assert score(-0.5, 0, 1, 0) == pytest.approx(0.15 * (0.1 + 0.5))


def test_keyword_score():
    assert keyword_score("我周五有面试", "周五面试") == pytest.approx(2 / 3)
    assert keyword_score("随便", "，，") == 0


def test_semantic_match_first(store, clock):
    add(store, "fact", "喜欢吃火锅")
    interview = add(store, "commitment", "周五下午面试", status="open")
    results = retriever(store, clock).search("面试准备得怎么样", top_k=2)
    assert results[0].memory.id == interview.id
    assert results[0].score > results[1].score


def test_superseded_excluded(store, clock):
    old = add(store, "fact", "在 A 公司上班")
    new = add(store, "fact", "在 B 公司上班")
    store.update_memory(old.id, actor="writer", superseded_by=new.id)
    ids = [s.memory.id for s in retriever(store, clock).search("公司上班", top_k=10)]
    assert ids == [new.id]


def test_type_filter(store, clock):
    add(store, "fact", "面试官很严")
    p = add(store, "person", "小林是面试搭子", subject="小林")
    assert [s.memory.id for s in retriever(store, clock).search("面试", 10, types={"person"})] == [p.id]


def test_old_episodes_capped_at_20(store, clock):
    for i in range(25):
        add(store, "episode", f"很久以前的第{i}件事")
    clock.advance(120 * 86400)
    recent = [add(store, "episode", f"最近的第{i}件事") for i in range(3)]
    results = retriever(store, clock).search("事情", top_k=100)
    ids = {s.memory.id for s in results}
    assert {m.id for m in recent} <= ids
    assert len(ids) - 3 <= 20


def test_recency_breaks_tie(store, clock):
    a = add(store, "fact", "喜欢猫")
    clock.advance(60 * 86400)
    b = add(store, "fact", "喜欢猫")
    assert [s.memory.id for s in retriever(store, clock).search("猫", 2)] == [b.id, a.id]


def test_empty_store(store, clock):
    assert retriever(store, clock).search("任何东西", 5) == []
