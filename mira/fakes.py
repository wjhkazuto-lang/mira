"""测试和开发模式（MIRA_FAKE=1）用的替身。"""

import zlib

import numpy as np

from mira.embedder import normalize
from mira.textutil import bigrams


class HashEmbedder:
    """把字符二元组哈希到固定维度上计数。字面相近的文本向量也相近。"""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for g in bigrams(t):
                out[i, zlib.crc32(g.encode()) % self.dim] += 1
        return normalize(out)


class FakeLLM:
    """按脚本依次返回结果（dict）或抛出异常，并记录每次调用。"""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []

    async def complete_json(self, *, purpose: str, model: str, messages: list[dict], max_tokens: int = 2000) -> dict:
        self.calls.append({"purpose": purpose, "model": model, "messages": messages, "max_tokens": max_tokens})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class EchoLLM:
    """开发模式用的假模型：复读你说的话，不产生记忆。"""

    async def complete_json(self, *, purpose: str, model: str, messages: list[dict], max_tokens: int = 2000) -> dict:
        if purpose == "writer":
            return {"ops": [], "episode": {"content": "开发模式事件", "importance": 1}}
        if purpose == "reflector":
            return {"patterns": [], "profile": ""}
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        said = last.split("【新消息】", 1)[-1].strip()
        return {
            "mood_read": "开发模式",
            "approach": "normal",
            "messages": [f"收到：{said[:20]}", "（这是开发模式的假回复）"],
        }
