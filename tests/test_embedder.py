import os

import numpy as np
import pytest

from mira.fakes import HashEmbedder
from mira.textutil import bigrams


def test_bigrams():
    assert bigrams("面试，准备") == {"面试", "准备"}
    assert bigrams("A b") == {"a", "b"}
    assert bigrams("") == set()


def test_hash_embedder_shape_norm():
    v = HashEmbedder().embed(["你好", "周五面试"])
    assert v.shape == (2, 256) and v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0)


def test_hash_embedder_empty_text_is_zero():
    assert not HashEmbedder().embed(["，，"]).any()


def test_hash_embedder_similarity():
    a, b, c = HashEmbedder().embed(["周五面试", "面试在周五", "今天吃火锅"])
    assert a @ b > a @ c


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="set RUN_SLOW=1 to download the model")
def test_fast_embedder_chinese_semantics():
    from mira.embedder import FastEmbedder

    e = FastEmbedder()
    v = e.embed(["我明天有个面试", "明天要去面试了", "晚饭吃了火锅"])
    assert v.shape == (3, 512) and e.dim == 512
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-3)
    assert v[0] @ v[1] > v[0] @ v[2]
