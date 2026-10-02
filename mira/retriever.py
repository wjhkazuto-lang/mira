"""混合检索：语义相似度 + 关键词重合 + 重要度/新近度（spec §5.3）。"""

import math
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime

from mira import clock
from mira.embedder import Embedder
from mira.store import Memory, Store
from mira.textutil import bigrams

OLD_EPISODE_DAYS = 90
OLD_EPISODE_SEMANTIC_CAP = 20


def score(cos: float, kw: float, importance: int, age_days: float) -> float:
    return 0.6 * max(cos, 0.0) + 0.25 * kw + 0.15 * (0.5 * importance / 5 + 0.5 * math.exp(-age_days / 30))


def keyword_score(query: str, content: str) -> float:
    c = bigrams(content)
    if not c:
        return 0.0
    return min(len(bigrams(query) & c) / len(c), 1.0)


@dataclass(frozen=True)
class Scored:
    memory: Memory
    score: float


class Retriever:
    def __init__(self, store: Store, embedder: Embedder, now: Callable[[], datetime] = clock.now):
        self._store = store
        self._embedder = embedder
        self._now = now

    def search(self, query: str, top_k: int, types: Collection[str] | None = None) -> list[Scored]:
        memories = [
            m for m in self._store.list_memories(include_superseded=False) if types is None or m.type in types
        ]
        if not memories:
            return []
        vectors = self._store.load_vectors(m.id for m in memories)
        q = self._embedder.embed([query])[0]
        now = self._now()
        cos = {m.id: float(vectors[m.id] @ q) if m.id in vectors else 0.0 for m in memories}

        def age_days(m: Memory) -> float:
            return max((now - m.updated_at).total_seconds() / 86400, 0.0)

        old_episodes = [m for m in memories if m.type == "episode" and age_days(m) > OLD_EPISODE_DAYS]
        ranked_old = sorted(old_episodes, key=lambda m: -cos[m.id])
        dropped = {m.id for m in ranked_old[OLD_EPISODE_SEMANTIC_CAP:]}
        candidates = [m for m in memories if m.id not in dropped]

        scored = [
            Scored(m, score(cos[m.id], keyword_score(query, m.content), m.importance, age_days(m)))
            for m in candidates
        ]
        scored.sort(key=lambda s: (-s.score, -s.memory.id))
        return scored[:top_k]
