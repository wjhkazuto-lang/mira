"""组装整个应用：各模块 + HTTP 接口 + WebSocket + 后台任务。"""

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from mira import clock
from mira.api import build_router
from mira.backup import BackupError, run_backup
from mira.chat import ChatEngine
from mira.config import Settings
from mira.embedder import Embedder, FastEmbedder
from mira.fakes import EchoLLM, HashEmbedder
from mira.llm import LLM, DeepSeekLLM
from mira.prompts import render
from mira.reflector import Reflector
from mira.retriever import Retriever
from mira.scheduler import Scheduler
from mira.store import Store
from mira.theme import Theme
from mira.writer import Writer

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: Settings, *, llm: LLM | None = None, embedder: Embedder | None = None) -> FastAPI:
    if llm is None:
        llm = EchoLLM() if settings.fake else DeepSeekLLM(settings.deepseek_api_key, settings.deepseek_base_url)
    if embedder is None:
        embedder = HashEmbedder() if settings.fake else FastEmbedder()

    store = Store(settings.db_path)
    theme = Theme(settings.theme_dir)
    retriever = Retriever(store, embedder)
    writer = Writer(store, embedder, retriever, llm, settings)
    reflector = Reflector(store, embedder, llm, settings)
    scheduler = Scheduler(
        store=store, writer=writer, reflector=reflector, settings=settings,
        backup=lambda: run_backup(settings.db_path, settings.backup_dir, settings.backup_keep, clock.now()),
    )
    engine = ChatEngine(
        store=store,
        retriever=retriever,
        llm=llm,
        settings=settings,
        persona=settings.persona_path.read_text(encoding="utf-8"),
        rules=render("chat_rules", crisis_resources=settings.crisis_resources, expression_hint=theme.prompt_hint()),
        on_activity=scheduler.notify_activity,
        theme=theme,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def background():
            await scheduler.startup()
            await scheduler.run_forever()

        task = asyncio.create_task(background())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="Mira", lifespan=lifespan)

    # 只允许本机页面访问：挡住 DNS 重绑定（Host）和其他网站的跨站请求（Origin）
    hosts = sorted({"127.0.0.1", "localhost", settings.host})
    origins = {f"http://{h}:{settings.port}" for h in hosts}
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def same_origin_only(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in origins:
            return JSONResponse({"detail": "拒绝来自其他网站的请求"}, status_code=403)
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            # 页面、样式、立绘：每次都向服务器确认有没有更新（没变时返回 304，几乎不费时间）
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    app.state.store = store
    app.state.embedder = embedder
    app.state.engine = engine
    app.state.scheduler = scheduler

    @app.get("/api/memory-status")
    async def memory_status():
        return scheduler.status()

    @app.post("/api/backup")
    async def backup_now():
        try:
            path = await scheduler.backup_now()
        except BackupError as e:
            raise HTTPException(500, e.user_message)
        return {"file": path.name, "at": clock.now().isoformat()}

    app.include_router(build_router(store, retriever, embedder))
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    # 你自己的立绘和背景（theme/ 不提交 git）；文件夹不存在时这个路径只是返回 404
    app.mount("/theme", StaticFiles(directory=settings.theme_dir, check_dir=False), name="theme")

    @app.get("/api/theme")
    async def theme_info():
        return {"expressions": theme.urls(), "backgrounds": theme.backgrounds(), "avatar": theme.avatar_url()}

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/memory")
    def memory_page():
        return FileResponse(WEB_DIR / "memory.html")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        if origin and origin not in origins:
            await websocket.close(code=1008)
            return
        await websocket.accept()

        async def send(event: dict) -> None:
            await websocket.send_json(event)

        engine.attach(send)
        try:
            while True:
                try:
                    data = json.loads(await websocket.receive_text())
                except json.JSONDecodeError:
                    continue
                kind = data.get("type") if isinstance(data, dict) else None
                if kind == "message":
                    client_id = data.get("client_id")
                    await engine.on_user_message(
                        str(data.get("text", "")), client_id=client_id if isinstance(client_id, str) else None
                    )
                elif kind == "typing":
                    await engine.on_typing()
                elif kind == "retry":
                    await engine.retry()
        except WebSocketDisconnect:
            pass
        finally:
            engine.detach(send)

    return app
