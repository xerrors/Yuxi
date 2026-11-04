"""语义切分必须拿到每条句子对应的向量，否则会静默丢句。"""

import numpy as np
import pytest

from yuxi.modules.knowledge.chunking.ragflow_like.utils import semantic_utils
from yuxi.modules.knowledge.chunking.ragflow_like.utils.semantic_utils import (
    semantic_chunking_with_auto_clusters,
)


def _token_count(text: str) -> int:
    return len(text)


def test_short_embeddings_raise_instead_of_dropping_sentences():
    """4 句只返回 2 个向量时，后两句会被 zip 截断丢失，必须报错。

    预算取 30 时簇数为 2，与返回的向量行数一致，旧代码能正常聚类后再丢句；
    取 10 时簇数算成 4，会先被聚类以「簇数多于样本数」拒绝，测不到本问题。
    """
    text = "Sentence one. Sentence two. Sentence three. Sentence four."
    embeddings = np.array([[1.0, 0.0], [0.0, 1.0]])

    with pytest.raises(ValueError, match="embedding"):
        semantic_chunking_with_auto_clusters(
            text,
            embed_fn=lambda texts: embeddings,
            token_count_fn=_token_count,
            max_chunk_size=30,
        )


def test_extra_embeddings_raise():
    text = "Sentence one. Sentence two. Sentence three."
    embeddings = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.5, 0.5]])

    with pytest.raises(ValueError, match="embedding"):
        semantic_chunking_with_auto_clusters(
            text,
            embed_fn=lambda texts: embeddings,
            token_count_fn=_token_count,
            max_chunk_size=10,
        )


def test_aligned_embeddings_keep_every_sentence(monkeypatch):
    """条数对齐时，输出必须包含全部原文句子。"""
    sentences = ["Alpha beta.", "Gamma delta.", "Epsilon zeta."]
    monkeypatch.setattr(semantic_utils, "split_mixed_sentences", lambda _text: sentences)

    embeddings = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]])
    chunks = semantic_chunking_with_auto_clusters(
        "ignored",
        embed_fn=lambda texts: embeddings,
        token_count_fn=_token_count,
        max_chunk_size=5,
    )

    joined = "".join(chunks)
    for sentence in sentences:
        assert sentence in joined
