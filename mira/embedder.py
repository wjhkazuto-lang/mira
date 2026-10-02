from pathlib import Path
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """返回 (n, dim) 的 float32 数组，每行 L2 归一化（全零向量保持为零）。"""
        ...


class EmbedderLoadError(Exception):
    pass


def normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    return np.divide(v, norms, out=np.zeros_like(v), where=norms > 0)


class FastEmbedder:
    dim = 512

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5", cache_dir: Path = Path(".cache/fastembed")):
        try:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
        except Exception as e:
            raise EmbedderLoadError(
                f"加载本地向量模型 {model_name} 失败：{e}\n"
                "第一次运行时需要联网下载模型（约 90MB）。请检查网络后重试；"
                "如果只想先试用界面，可以设置 MIRA_FAKE=1。"
            ) from e

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return normalize(np.stack(list(self._model.embed(texts))))
