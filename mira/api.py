"""记忆管理页和聊天页用到的 HTTP 接口（spec §6.3）。"""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mira.embedder import Embedder
from mira.retriever import Retriever
from mira.store import COMMITMENT_STATUSES, Memory, Message, Profile, Store


def _jsonable(d: dict) -> dict:
    return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in d.items()}


def memory_dict(m: Memory) -> dict:
    return _jsonable(asdict(m))


def message_dict(m: Message) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "batch_id": m.batch_id,
        "created_at": m.created_at.isoformat(),
    }


def profile_dict(p: Profile) -> dict:
    return _jsonable(asdict(p))


CONVERTIBLE_TYPES = ("fact", "idea", "goal", "commitment")  # 你可以在这几种之间互相纠正
TRACKED_STATUSES = {"goal": ("open", "done", "dropped"), "commitment": COMMITMENT_STATUSES}


class MemoryPatch(BaseModel):
    type: str | None = None
    content: str | None = None
    status: str | None = None
    importance: int | None = None
    due_at: str | None = None


class ProfileBody(BaseModel):
    content: str


def build_router(store: Store, retriever: Retriever, embedder: Embedder) -> APIRouter:
    router = APIRouter(prefix="/api")

    def get_or_404(id: int) -> Memory:
        m = store.get_memory(id)
        if m is None:
            raise HTTPException(404, "这条记忆不存在")
        return m

    @router.get("/messages")
    async def list_messages(before: int | None = None, limit: int = 50):
        return [message_dict(m) for m in store.list_messages(before_id=before, limit=min(limit, 200))]

    @router.get("/memories")
    async def list_memories(type: str | None = None, q: str | None = None):
        if q:
            types = {type} if type else None
            return [memory_dict(s.memory) | {"score": round(s.score, 3)} for s in retriever.search(q, 50, types)]
        return [memory_dict(m) for m in store.list_memories(type)]

    @router.get("/memories/{id}")
    async def memory_detail(id: int):
        m = get_or_404(id)
        out = memory_dict(m)
        out["source_messages"] = [message_dict(msg) for msg in store.get_messages(m.source_message_ids)]
        if m.type == "pattern":
            items = [store.get_memory(e) for e in m.evidence]
            out["evidence_items"] = [
                {"id": e.id, "content": e.content, "created_at": e.created_at.isoformat()} for e in items if e and e.type == "episode"
            ]
        return out

    @router.patch("/memories/{id}")
    async def patch_memory(id: int, body: MemoryPatch):
        m = get_or_404(id)
        given = body.model_fields_set
        if not given:
            raise HTTPException(422, "没有要修改的内容")
        fields: dict = {"user_locked": True}
        new_type = m.type
        if "type" in given:
            if m.type not in CONVERTIBLE_TYPES or body.type not in CONVERTIBLE_TYPES:
                raise HTTPException(422, "只能在事实、想法、目标、承诺之间改类型")
            new_type = fields["type"] = body.type
            if new_type in TRACKED_STATUSES:
                if m.status not in TRACKED_STATUSES[new_type]:
                    fields["status"] = "open"
            else:
                fields["status"] = None
            if new_type != "commitment":
                fields["due_at"] = None
        if "content" in given:
            if not body.content or not body.content.strip():
                raise HTTPException(422, "内容不能为空")
            fields["content"] = body.content.strip()
        if "status" in given:
            if body.status not in TRACKED_STATUSES.get(new_type, ()):
                raise HTTPException(422, "只有目标和承诺有状态；目标没有“逾期”")
            fields["status"] = body.status
        if "importance" in given:
            if body.importance is None or not 1 <= body.importance <= 5:
                raise HTTPException(422, "重要度必须是 1 到 5")
            fields["importance"] = body.importance
        if "due_at" in given:
            try:
                fields["due_at"] = date.fromisoformat(body.due_at) if body.due_at else None
            except ValueError:
                raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from None
        vector = embedder.embed([fields["content"]])[0] if "content" in fields else None
        return memory_dict(store.update_memory(id, actor="user", vector=vector, **fields))

    @router.delete("/memories/{id}")
    async def delete_memory(id: int):
        get_or_404(id)
        store.delete_memory(id, actor="user")
        return {"ok": True}

    @router.get("/profile")
    async def get_profile():
        current = store.current_profile()
        return {
            "current": profile_dict(current) if current else None,
            "history": [profile_dict(p) for p in store.list_profiles()],
        }

    @router.put("/profile")
    async def put_profile(body: ProfileBody):
        if not body.content.strip():
            raise HTTPException(422, "核心档案不能为空")
        return profile_dict(store.add_profile(body.content.strip(), "user"))

    @router.post("/profile/rollback/{id}")
    async def rollback_profile(id: int):
        old = store.get_profile(id)
        if old is None:
            raise HTTPException(404, "这个版本不存在")
        return profile_dict(store.add_profile(old.content, "user"))

    @router.get("/memory-log")
    async def memory_log(limit: int = 20):
        return [_jsonable(asdict(e)) for e in store.recent_log(limit=min(limit, 200))]

    return router
