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
