import inspect

import numpy as np
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mira.config import load_settings
from mira.main import create_app

# TestClient 的 websocket_connect 会忽略 base_url，必须写完整地址
WS = "ws://127.0.0.1:8000/ws"


@pytest.fixture
def app(tmp_path):
    settings = load_settings({"MIRA_FAKE": "1", "DB_PATH": str(tmp_path / "t.db"), "DEBOUNCE_SECONDS": "0"})
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        yield c


def add(app, type, content, **kw):
    vec = app.state.embedder.embed([content])[0]
    return app.state.store.add_memory(type, content, vector=vec, actor="writer", **kw)


def test_pages_served(client):
    assert client.get("/").status_code == 200 and "text/html" in client.get("/").headers["content-type"]
    assert client.get("/memory").status_code == 200


def test_messages_pagination(client, app):
    for i in range(5):
        app.state.store.add_message("user", str(i))
    page = client.get("/api/messages", params={"before": 4, "limit": 2}).json()
    assert [m["content"] for m in page] == ["1", "2"]
    assert set(page[0]) == {"id", "role", "content", "batch_id", "created_at"}


def test_list_and_search_memories(client, app):
    a = add(app, "fact", "养了一只猫")
    b = add(app, "fact", "在 A 公司")
    c = add(app, "fact", "在 B 公司")
    app.state.store.update_memory(b.id, actor="writer", superseded_by=c.id)
    add(app, "person", "小林", subject="小林")
    listed = client.get("/api/memories", params={"type": "fact"}).json()
    assert {m["id"] for m in listed} == {a.id, b.id, c.id}
    found = client.get("/api/memories", params={"q": "一只猫"}).json()
    assert found[0]["id"] == a.id and "score" in found[0]
    assert b.id not in {m["id"] for m in found}


def test_patch_locks_and_reembeds(client, app):
    m = add(app, "fact", "喜欢猫")
    before = app.state.store.load_vectors([m.id])[m.id].copy()
    r = client.patch(f"/api/memories/{m.id}", json={"content": "喜欢狗", "importance": 5})
    assert r.status_code == 200 and r.json()["user_locked"] is True and r.json()["importance"] == 5
    assert not np.allclose(app.state.store.load_vectors([m.id])[m.id], before)
    assert app.state.store.recent_log()[0].actor == "user"


def test_patch_commitment_status_and_due(client, app):
    c = add(app, "commitment", "改简历", status="open")
    r = client.patch(f"/api/memories/{c.id}", json={"status": "done", "due_at": "2026-10-09"})
    assert r.json()["status"] == "done" and r.json()["due_at"] == "2026-10-09"


@pytest.mark.parametrize("body", [{"content": "  "}, {"status": "done"}, {"importance": 9}, {"due_at": "明天"}, {}])
def test_patch_validation(client, app, body):
    m = add(app, "fact", "喜欢猫")
    assert client.patch(f"/api/memories/{m.id}", json=body).status_code == 422


def test_delete_then_404(client, app):
    m = add(app, "fact", "喜欢猫")
    assert client.delete(f"/api/memories/{m.id}").status_code == 200
    assert client.get(f"/api/memories/{m.id}").status_code == 404
    assert client.delete(f"/api/memories/{m.id}").status_code == 404
    assert client.patch(f"/api/memories/{m.id}", json={"importance": 2}).status_code == 404


def test_memory_detail_has_sources(client, app):
    msg = app.state.store.add_message("user", "我养了只猫")
    m = add(app, "fact", "养了一只猫", source_message_ids=[msg.id])
    detail = client.get(f"/api/memories/{m.id}").json()
    assert [s["content"] for s in detail["source_messages"]] == ["我养了只猫"]


def test_pattern_detail_filters_dangling_evidence(client, app):
    e1, e2 = add(app, "episode", "熬夜一"), add(app, "episode", "熬夜二")
    p = add(app, "pattern", "压力大时熬夜", evidence=[e1.id, e2.id])
    client.delete(f"/api/memories/{e2.id}")
    detail = client.get(f"/api/memories/{p.id}").json()
    assert [e["id"] for e in detail["evidence_items"]] == [e1.id]


def test_profile_put_get_rollback(client):
    assert client.get("/api/profile").json() == {"current": None, "history": []}
    first = client.put("/api/profile", json={"content": "第一版"}).json()
    client.put("/api/profile", json={"content": "第二版"})
    assert client.put("/api/profile", json={"content": " "}).status_code == 422
    rolled = client.post(f"/api/profile/rollback/{first['id']}").json()
    data = client.get("/api/profile").json()
    assert data["current"]["content"] == "第一版" and data["current"]["id"] == rolled["id"]
    assert [p["content"] for p in data["history"]] == ["第一版", "第二版", "第一版"]
    assert client.post("/api/profile/rollback/999").status_code == 404


def test_memory_log_endpoint(client, app):
    add(app, "fact", "喜欢猫")
    log = client.get("/api/memory-log", params={"limit": 5}).json()
    assert log[0]["op"] == "add" and log[0]["after"]["content"] == "喜欢猫"


def test_ws_roundtrip(client):
    with client.websocket_connect(WS, headers={"origin": "http://127.0.0.1:8000"}) as ws:
        ws.send_json({"type": "nonsense"})
        ws.send_text("not json")
        ws.send_json({"type": "message", "text": "你好"})
        events = [ws.receive_json() for _ in range(4)]
    assert [e["type"] for e in events] == ["typing", "bubble", "typing", "bubble"]
    assert events[1]["text"] == "收到：你好"
    msgs = client.get("/api/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]


def test_api_routes_are_async(app):
    # 同步路由会在线程池里跑，和事件循环共用一个 sqlite 连接会导致事务交错
    from mira.api import build_router
    from mira.retriever import Retriever

    router = build_router(app.state.store, Retriever(app.state.store, app.state.embedder), app.state.embedder)
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert routes and all(inspect.iscoroutinefunction(r.endpoint) for r in routes)


def test_foreign_host_rejected(client):
    assert client.get("/api/messages", headers={"host": "evil.example"}).status_code == 400


def test_ws_foreign_origin_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(WS, headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_cross_site_mutation_rejected(client):
    first = client.put("/api/profile", json={"content": "第一版"}).json()
    r = client.post(f"/api/profile/rollback/{first['id']}", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.post(f"/api/profile/rollback/{first['id']}", headers={"origin": "http://localhost:8000"}).status_code == 200


def test_evidence_items_only_episodes(client, app):
    e1, e2 = add(app, "episode", "熬夜一"), add(app, "episode", "熬夜二")
    f = add(app, "fact", "喜欢猫")
    p = add(app, "pattern", "压力大时熬夜", evidence=[e1.id, e2.id, f.id])
    detail = client.get(f"/api/memories/{p.id}").json()
    assert [e["id"] for e in detail["evidence_items"]] == [e1.id, e2.id]
