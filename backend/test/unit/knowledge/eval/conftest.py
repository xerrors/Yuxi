"""knowledge/eval 测试共享的假实现与工具函数。

test_benchmark_generation.py 与 test_dataset_generation_resume.py 复用的
假知识库 / 假 LLM / chunk 构造等定义集中在 conftest，避免两份拷贝漂移。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


class FakeGenerationKnowledgeBase:
    def __init__(self, query_results=None):
        self.query_results = query_results or []
        self.query_calls = []

    async def aquery(self, query_text, kb_id, **kwargs):
        self.query_calls.append({"query_text": query_text, "kb_id": kb_id, **kwargs})
        return self.query_results


class NoQueryKnowledgeBase(FakeGenerationKnowledgeBase):
    async def aquery(self, query_text, kb_id, **kwargs):
        raise AssertionError("neighbors_count=1 时不应调用 aquery")


class TrackingLlm:
    def __init__(self, content=None, delay=0):
        self.content = content or '{"query":"问题","gold_answer":"答案","gold_chunk_ids":["anchor_chunk"]}'
        self.delay = delay
        self.active_calls = 0
        self.max_active_calls = 0
        self.calls = 0

    async def call(self, prompt, stream):
        self.calls += 1
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return SimpleNamespace(content=self.content)
        finally:
            self.active_calls -= 1


def make_chunk(
    chunk_id: str,
    *,
    kb_id: str = "db_1",
    file_id: str = "file_a",
    content: str = "anchor content",
    chunk_index: int = 0,
    graph_indexed: bool = False,
    ent_ids: list[str] | None = None,
):
    return SimpleNamespace(
        chunk_id=chunk_id,
        kb_id=kb_id,
        file_id=file_id,
        content=content,
        chunk_index=chunk_index,
        graph_indexed=graph_indexed,
        ent_ids=ent_ids,
        tags=None,
        extraction_result=None,
    )
