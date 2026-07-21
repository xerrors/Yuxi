import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable
from typing import Any

import json_repair

from yuxi.models import select_model
from yuxi.utils import logger

DEFAULT_BENCHMARK_GENERATION_CONCURRENCY = 10
MAX_BENCHMARK_GENERATION_CONCURRENCY = 20
DEFAULT_GRAPH_EXPAND_TOP_K = 1
MAX_GRAPH_EXPAND_TOP_K = 3
GRAPH_SEED_DECAY = 0.9
GRAPH_PPR_DAMPING = 0.85
GRAPH_PPR_MAX_NODES = 10000

_WORKER_DONE = object()


async def collect_kb_chunks(kb_instance: Any, kb_id: str) -> list[dict[str, Any]]:
    del kb_instance

    from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository

    return [
        {
            "id": chunk.chunk_id,
            "content": chunk.content or "",
            "file_id": chunk.file_id,
            "chunk_index": chunk.chunk_index,
            "graph_indexed": bool(chunk.graph_indexed),
            "ent_ids": chunk.ent_ids or [],
            "tags": chunk.tags or [],
            "extraction_result": chunk.extraction_result,
        }
        for chunk in await KnowledgeChunkRepository().list_by_kb_id(kb_id)
    ]


def clamp_neighbors_count(neighbors_count: int) -> int:
    return min(max(neighbors_count, 0), 10)


def normalize_generation_concurrency_count(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_BENCHMARK_GENERATION_CONCURRENCY
    return min(max(1, int(value)), MAX_BENCHMARK_GENERATION_CONCURRENCY)


def normalize_graph_expand_top_k(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_GRAPH_EXPAND_TOP_K
    return min(max(1, int(value)), MAX_GRAPH_EXPAND_TOP_K)


def _chunk_entity_ids(chunk: dict[str, Any]) -> list[str]:
    return [str(entity_id) for entity_id in chunk.get("ent_ids") or [] if entity_id]


def _is_anchor_chunk(candidate: dict[str, Any], anchor_chunk: dict[str, Any]) -> bool:
    metadata = candidate.get("metadata") or {}
    candidate_id = metadata.get("chunk_id")
    if candidate_id is not None and str(candidate_id) == str(anchor_chunk.get("id")):
        return True

    candidate_file_id = metadata.get("file_id")
    candidate_chunk_index = metadata.get("chunk_index")
    return candidate_file_id == anchor_chunk.get("file_id") and candidate_chunk_index == anchor_chunk.get("chunk_index")


async def select_neighbor_chunks_by_kb_query(
    *, kb_instance: Any, kb_id: str, anchor_chunk: dict[str, Any], neighbors_count: int
) -> list[dict[str, Any]]:
    if neighbors_count <= 0:
        return []

    anchor_content = anchor_chunk.get("content", "")
    if not anchor_content:
        return []

    candidates = await kb_instance.aquery(
        anchor_content,
        kb_id,
        search_mode="vector",
        final_top_k=neighbors_count + 3,
        use_reranker=False,
        similarity_threshold=0.0,
    )

    chunks = []
    for candidate in candidates:
        if _is_anchor_chunk(candidate, anchor_chunk):
            continue

        metadata = candidate.get("metadata") or {}
        chunk_id = metadata.get("chunk_id")
        content = candidate.get("content", "")
        if not chunk_id or not content:
            continue

        chunks.append(
            {
                "id": str(chunk_id),
                "content": content,
                "file_id": metadata.get("file_id"),
                "chunk_index": metadata.get("chunk_index"),
            }
        )
        if len(chunks) >= neighbors_count:
            break

    return chunks


async def select_graph_enhanced_chunks(
    *,
    kb_id: str,
    anchor_chunk: dict[str, Any],
    chunks_by_id: dict[str, dict[str, Any]],
    context_count: int,
    graph_expand_top_k: int,
) -> list[dict[str, Any]] | None:
    if context_count <= 1:
        return [anchor_chunk]

    from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService

    anchor_entity_ids = _chunk_entity_ids(anchor_chunk)
    if not anchor_entity_ids:
        return None

    graph_service = MilvusGraphService()
    selected = [anchor_chunk]
    selected_ids = {str(anchor_chunk.get("id"))}
    seed_weights = {entity_id: 1.0 for entity_id in anchor_entity_ids}
    round_index = 1

    while len(selected) < context_count:
        for entity_id in anchor_entity_ids:
            seed_weights[entity_id] = 1.0

        ranked_chunks = await graph_service.query_and_rank_chunks_by_ppr(
            kb_id,
            seed_weights,
            max_nodes=GRAPH_PPR_MAX_NODES,
            top_k=max(context_count * 5, 20),
            damping=GRAPH_PPR_DAMPING,
        )
        if not ranked_chunks:
            return None

        new_chunks = []
        for chunk_id, _ in ranked_chunks:
            chunk_id = str(chunk_id)
            if chunk_id in selected_ids:
                continue
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                continue
            new_chunks.append(chunk)
            if len(new_chunks) >= min(graph_expand_top_k, context_count - len(selected)):
                break

        if not new_chunks:
            return None

        new_weight = GRAPH_SEED_DECAY**round_index
        for chunk in new_chunks:
            selected.append(chunk)
            selected_ids.add(str(chunk.get("id")))
            for entity_id in _chunk_entity_ids(chunk):
                seed_weights[entity_id] = max(seed_weights.get(entity_id, 0.0), new_weight)
        round_index += 1

    return selected


def build_benchmark_generation_prompt(ctx_items: list[tuple[str, str]]) -> str:
    context_text = "\n\n".join([f"片段ID={cid}\n{content}" for cid, content in ctx_items])
    return (
        "你将基于以下上下文生成一个可由上下文准确回答的问题与标准答案。"
        "仅返回一个JSON对象，不要包含其他文字。"
        "键为 query、gold_answer、gold_chunk_ids。gold_chunk_ids 必须是上述上下文片段的ID子集。\n\n"
        "上下文：\n" + context_text + "\n"
    )


async def _generate_benchmark_item_once(
    *,
    kb_instance: Any,
    kb_id: str,
    all_chunks: list[dict[str, Any]],
    llm: Any,
    context_count: int,
    generation_mode: str,
    graph_expand_top_k: int,
    chunks_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if generation_mode == "graph_enhanced":
        graph_anchor_chunks = [
            chunk for chunk in all_chunks if chunk.get("graph_indexed") is True and _chunk_entity_ids(chunk)
        ]
        if not graph_anchor_chunks:
            raise ValueError("No graph indexed chunks with entities found in knowledge base")
        anchor_chunk = graph_anchor_chunks[random.randrange(len(graph_anchor_chunks))]
        ctx_chunks = await select_graph_enhanced_chunks(
            kb_id=kb_id,
            anchor_chunk=anchor_chunk,
            chunks_by_id=chunks_by_id,
            context_count=context_count,
            graph_expand_top_k=graph_expand_top_k,
        )
        if ctx_chunks is None:
            return None
    else:
        anchor_chunk = all_chunks[random.randrange(len(all_chunks))]
        neighbor_chunks = await select_neighbor_chunks_by_kb_query(
            kb_instance=kb_instance,
            kb_id=kb_id,
            anchor_chunk=anchor_chunk,
            neighbors_count=context_count - 1,
        )
        ctx_chunks = [anchor_chunk] + neighbor_chunks
    ctx_items = [(chunk["id"], chunk["content"]) for chunk in ctx_chunks]
    allowed_ids = {cid for cid, _ in ctx_items}

    try:
        resp = await llm.call(build_benchmark_generation_prompt(ctx_items), False)
        obj = json_repair.loads(resp.content if resp else "")
        query = obj.get("query")
        answer = obj.get("gold_answer")
        gold_ids = obj.get("gold_chunk_ids")
        if not query or not answer or not isinstance(gold_ids, list):
            logger.warning(f"Generated JSON missing fields or invalid format: {obj}")
            return None

        gold_ids = [str(item) for item in gold_ids if str(item) in allowed_ids]
        if not gold_ids:
            logger.warning("Generated gold_chunk_ids not found in allowed context")
            return None

        return {"query": query, "gold_chunk_ids": gold_ids, "gold_answer": answer}
    except Exception as e:
        logger.warning(f"Benchmark generation failed for one item: {e}")
        return None


async def iter_generated_benchmark_items(
    *,
    kb_instance: Any,
    kb_id: str,
    count: int,
    neighbors_count: int,
    llm_model_spec: str | None,
    concurrency_count: int = DEFAULT_BENCHMARK_GENERATION_CONCURRENCY,
    generation_mode: str = "vector",
    graph_expand_top_k: int = DEFAULT_GRAPH_EXPAND_TOP_K,
    progress_base: int = 0,
    total_progress: int | None = None,
    progress_cb: Callable[[int, str], Any] | None = None,
    cancel_cb: Callable[[], Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    if progress_cb:
        await progress_cb(5, "加载chunks")

    all_chunks = await collect_kb_chunks(kb_instance, kb_id)
    if not all_chunks:
        raise ValueError("No chunks found in knowledge base")
    chunks_by_id = {str(chunk["id"]): chunk for chunk in all_chunks if chunk.get("id") is not None}

    if generation_mode not in {"vector", "graph_enhanced"}:
        raise ValueError("Unsupported benchmark generation mode")
    graph_expand_top_k = normalize_graph_expand_top_k(graph_expand_top_k)

    if progress_cb:
        await progress_cb(15, "准备生成样本")

    if not llm_model_spec:
        raise ValueError("llm_model_spec 不能为空")

    llm = select_model(model_spec=llm_model_spec)
    context_count = max(clamp_neighbors_count(neighbors_count), 1)
    max_attempts = max(count * 5, 50)
    worker_count = normalize_generation_concurrency_count(concurrency_count)
    actual_worker_count = min(worker_count, max(count, 1), max_attempts)
    generated = 0
    results_queue: asyncio.Queue = asyncio.Queue()
    state_lock = asyncio.Lock()
    queue: asyncio.Queue[int] = asyncio.Queue()

    for attempt_no in range(max_attempts):
        queue.put_nowait(attempt_no)

    async def worker() -> None:
        nonlocal generated
        try:
            while True:
                if cancel_cb:
                    await cancel_cb()
                async with state_lock:
                    if generated >= count:
                        return
                try:
                    attempt_no = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    item = await _generate_benchmark_item_once(
                        kb_instance=kb_instance,
                        kb_id=kb_id,
                        all_chunks=all_chunks,
                        llm=llm,
                        context_count=context_count,
                        generation_mode=generation_mode,
                        graph_expand_top_k=graph_expand_top_k,
                        chunks_by_id=chunks_by_id,
                    )
                    progress = None
                    message = None
                    if item is not None:
                        async with state_lock:
                            if generated >= count:
                                item = None  # 超额丢弃，但 attempt 已 resolved，仍需回报
                            else:
                                generated += 1
                                if progress_cb:
                                    total_val = (
                                        total_progress if total_progress is not None else (progress_base + count)
                                    )
                                    progress = int(99 * (progress_base + generated) / max(total_val, 1))
                                    message = f"已生成 {progress_base + generated}/{total_val}"
                    await results_queue.put((attempt_no, item))
                    if progress_cb and progress is not None:
                        await progress_cb(progress, message)
                finally:
                    queue.task_done()
        except asyncio.CancelledError as exc:
            await results_queue.put(exc)
            raise
        except Exception as exc:
            await results_queue.put(exc)
        finally:
            await results_queue.put(_WORKER_DONE)

    workers = [asyncio.create_task(worker()) for _ in range(actual_worker_count)]
    done_workers = 0
    received = 0
    next_attempt = 0
    reorder: dict[int, dict[str, Any] | None] = {}
    pending_error: BaseException | None = None
    completed = False
    try:
        while done_workers < actual_worker_count and received < count:
            report = await results_queue.get()
            if report is _WORKER_DONE:
                done_workers += 1
                continue
            if isinstance(report, BaseException):
                pending_error = report
                break
            attempt_no, item = report
            reorder[attempt_no] = item
            while next_attempt in reorder:
                resolved = reorder.pop(next_attempt)
                next_attempt += 1
                if resolved is None:
                    continue
                received += 1
                yield resolved
        completed = pending_error is None
    finally:
        if not completed:
            for task in workers:
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    if pending_error is not None:
        while not results_queue.empty():
            pending = results_queue.get_nowait()
            if pending is _WORKER_DONE or isinstance(pending, BaseException):
                continue
            attempt_no, item = pending
            reorder[attempt_no] = item
        for attempt_no in sorted(reorder):
            if reorder[attempt_no] is not None:
                yield reorder[attempt_no]
        raise pending_error


def dump_benchmark_item(item: dict[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
