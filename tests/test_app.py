import contextlib
import inspect
import time

import numpy as np
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mira.config import PROJECT_ROOT, load_settings
from mira.main import create_app

# TestClient 的 websocket_connect 会忽略 base_url，必须写完整地址
WS = "ws://127.0.0.1:8000/ws"


@pytest.fixture
def app(tmp_path):
    settings = load_settings({"MIRA_FAKE": "1", "DB_PATH": str(tmp_path / "t.db"), "DEBOUNCE_SECONDS": "0",
                              "THEME_DIR": str(tmp_path / "no-theme"),  # 不读你真实的 theme/ 素材
                              "BACKUP_DIR": str(tmp_path / "backups")})
    return create_app(settings)


@contextlib.contextmanager
def started_client(app):
    # 等启动任务（含启动备份）结束再碰数据库，否则两个线程会同时用同一个连接
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        assert app.state.startup_done.wait(5)
        yield c


@pytest.fixture
def client(app):
    with started_client(app) as c:
        yield c


def test_startup_done_waits_for_slow_startup_backup(app):
    original = app.state.scheduler._backup

    def slow_backup(manual):
        time.sleep(0.3)
        return original(manual)

    app.state.scheduler._backup = slow_backup
    with started_client(app):
        assert app.state.store.get_job_last_run("backup") is not None


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
        ws.send_json({"type": "message", "text": "你好", "client_id": "c9"})
        events = [ws.receive_json() for _ in range(5)]
    assert [e["type"] for e in events] == ["user_message", "typing", "bubble", "typing", "bubble"]
    assert events[0]["client_id"] == "c9" and events[2]["text"] == "收到：你好"
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


async def test_lifespan_catches_up_and_processes_new_messages(tmp_path):
    """真实应用生命周期 + 调度器 + 写入器 + SQLite；只替换付费模型和时钟等待。"""
    import asyncio

    settings = load_settings({"MIRA_FAKE": "1", "DB_PATH": str(tmp_path / "lifecycle.db"), "IDLE_WRITE_MINUTES": "0",
                              "BACKUP_DIR": str(tmp_path / "backups")})
    app = create_app(settings)
    store = app.state.store
    scheduler = app.state.scheduler
    original_sleep = asyncio.sleep

    async def quick_sleep(_):
        await original_sleep(0.01)

    scheduler._sleep = quick_sleep
    store.add_message("user", "启动前的测试消息")

    async def wait_processed():
        async with asyncio.timeout(2):
            while store.pending_message_count():
                await original_sleep(0.01)

    async with app.router.lifespan_context(app):
        await wait_processed()
        assert len(store.list_memories("episode")) == 1
        assert scheduler.status()["last_success_at"] is not None
        store.add_message("user", "启动后的测试消息")
        scheduler.notify_activity()
        await wait_processed()
        assert len(store.list_memories("episode")) == 2
        assert scheduler.status()["state"] == "idle"
    # 退出应用后循环必须停止。
    store.add_message("user", "退出后的消息")
    await original_sleep(0.03)
    assert store.pending_message_count() == 1


def test_memory_status_endpoint(client):
    data = client.get("/api/memory-status").json()
    assert data["state"] == "idle"
    assert data["pending_messages"] == 0
    assert data["error"] is None


def test_patch_type_conversion(client, app):
    from datetime import date

    m = add(app, "fact", "想靠 AI 挣钱")
    r = client.patch(f"/api/memories/{m.id}", json={"type": "goal"}).json()
    assert (r["type"], r["status"], r["due_at"]) == ("goal", "open", None)
    c = add(app, "commitment", "周五交", status="overdue", due_at=date(2026, 10, 9))
    r = client.patch(f"/api/memories/{c.id}", json={"type": "idea"}).json()
    assert (r["type"], r["status"], r["due_at"]) == ("idea", None, None)


@pytest.mark.parametrize("body", [{"type": "pattern"}, {"type": "dream"}])
def test_patch_type_rejects_non_convertible(client, app, body):
    m = add(app, "fact", "x")
    assert client.patch(f"/api/memories/{m.id}", json=body).status_code == 422


def test_patch_goal_status(client, app):
    g = add(app, "goal", "考研", status="open")
    assert client.patch(f"/api/memories/{g.id}", json={"status": "done"}).json()["status"] == "done"
    assert client.patch(f"/api/memories/{g.id}", json={"status": "overdue"}).status_code == 422


def test_pattern_type_cannot_be_changed(client, app):
    e1, e2 = add(app, "episode", "a"), add(app, "episode", "b")
    p = add(app, "pattern", "熬夜", evidence=[e1.id, e2.id])
    assert client.patch(f"/api/memories/{p.id}", json={"type": "fact"}).status_code == 422


def test_theme_api_and_static(tmp_path):
    theme = tmp_path / "theme"
    (theme / "mira").mkdir(parents=True)
    (theme / "mira" / "calm.png").write_bytes(b"\x89PNG")
    settings = load_settings({"MIRA_FAKE": "1", "DB_PATH": str(tmp_path / "t.db"), "THEME_DIR": str(theme),
                              "BACKUP_DIR": str(tmp_path / "backups")})
    app = create_app(settings)
    with started_client(app) as c:
        data = c.get("/api/theme").json()
        assert data["expressions"] == {"calm": "/theme/mira/calm.png"} and data["backgrounds"] == {}
        assert c.get("/theme/mira/calm.png").content == b"\x89PNG"
        assert c.get("/theme/../mira.db").status_code == 404


def test_theme_api_without_theme(client):
    assert client.get("/api/theme").json() == {"expressions": {}, "backgrounds": {}, "avatar": None}


def test_static_files_revalidated(client):
    # 更新界面后不用强制刷新：浏览器每次都先问服务器文件变没变
    assert client.get("/static/style.css").headers.get("cache-control") == "no-cache"
    assert client.get("/").headers.get("cache-control") == "no-cache"


def test_video_background_supports_range_requests(tmp_path):
    # Safari 播放视频必须支持按段读取（Range）
    theme = tmp_path / "theme"
    (theme / "background").mkdir(parents=True)
    (theme / "background" / "night.mp4").write_bytes(b"0123456789")
    settings = load_settings({"MIRA_FAKE": "1", "DB_PATH": str(tmp_path / "t.db"), "THEME_DIR": str(theme),
                              "BACKUP_DIR": str(tmp_path / "backups")})
    app = create_app(settings)
    with started_client(app) as c:
        assert c.get("/api/theme").json()["backgrounds"] == {"night": "/theme/background/night.mp4"}
        r = c.get("/theme/background/night.mp4", headers={"Range": "bytes=2-5"})
        assert r.status_code == 206 and r.content == b"2345"
        assert r.headers["content-type"] == "video/mp4"


def test_backup_endpoint(client, tmp_path):
    r = client.post("/api/backup")
    assert r.status_code == 200 and r.json()["file"].startswith("t-manual-")
    assert (tmp_path / "backups" / r.json()["file"]).exists()
    assert client.get("/api/memory-status").json()["last_backup_at"]


def test_backup_endpoint_rejects_cross_site(client):
    assert client.post("/api/backup", headers={"Origin": "http://evil.example"}).status_code == 403


def test_backup_endpoint_failure_is_500_with_chinese(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")  # 备份目录指向普通文件，必然失败
    settings = load_settings({"MIRA_FAKE": "1", "DB_PATH": str(tmp_path / "t.db"), "THEME_DIR": str(tmp_path / "no-theme"),
                              "BACKUP_DIR": str(blocker)})
    app = create_app(settings)
    with started_client(app) as c:
        r = c.post("/api/backup")
        assert r.status_code == 500 and r.json()["detail"].startswith("备份失败")
        assert c.get("/api/memory-status").json()["backup_error"].startswith("备份失败")


def test_app_tests_never_write_real_backups():
    real = PROJECT_ROOT / "data" / "backups"
    assert not real.exists() or not [p for p in real.iterdir() if not p.name.startswith(("mira-", "dev-"))]


def test_patch_status_only_does_not_lock(client, app):
    c = add(app, "commitment", "改简历", status="open")
    r = client.patch(f"/api/memories/{c.id}", json={"status": "done"})
    assert r.json()["status"] == "done" and r.json()["user_locked"] is False


def test_patch_status_only_keeps_existing_lock(client, app):
    c = add(app, "commitment", "改简历", status="open", user_locked=True)
    r = client.patch(f"/api/memories/{c.id}", json={"status": "done"})
    assert r.json()["status"] == "done" and r.json()["user_locked"] is True


def test_patch_importance_only_still_locks(client, app):
    m = add(app, "fact", "喜欢猫")
    assert client.patch(f"/api/memories/{m.id}", json={"importance": 4}).json()["user_locked"] is True


def test_delete_superseding_memory_restores_old(client, app):
    old = add(app, "fact", "喜欢猫")
    new = add(app, "fact", "喜欢狗")
    app.state.store.update_memory(old.id, actor="writer", superseded_by=new.id)
    assert old.id in {m["id"] for m in client.get("/api/memories").json()}  # 列表含已过时的
    assert client.get(f"/api/memories/{old.id}").json()["superseded_by"] == new.id
    client.delete(f"/api/memories/{new.id}")
    assert client.get(f"/api/memories/{old.id}").json()["superseded_by"] is None
