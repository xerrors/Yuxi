import asyncio
import json
import os
import re
import uuid
from typing import Any

from yuxi.knowledge.eval.benchmark_generation import (
    dump_benchmark_item,
    iter_generated_benchmark_items,
    normalize_generation_concurrency_count,
)
from yuxi.knowledge.eval.evaluator import aggregate_metrics, evaluate_question
from yuxi.knowledge.runtime import knowledge_base
from yuxi.models import select_model
from yuxi.repositories.evaluation_repository import EvaluationRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services.task_service import TaskContext, tasker
from yuxi.utils import logger
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive

DATASET_PERSIST_BATCH_SIZE = max(1, int(os.getenv("YUXI_DATASET_PERSIST_BATCH_SIZE") or 1))


def build_evaluation_run_name(started_at=None, hash_value: str | None = None) -> str:
    date_part = (started_at or utc_now_naive()).strftime("%Y%m%d")
    hash_part = re.sub(r"[^a-fA-F0-9]", "", hash_value or uuid.uuid4().hex).lower()[:6]
    if len(hash_part) < 6:
        hash_part = (hash_part + uuid.uuid4().hex)[:6]
    return f"eval-{date_part}-{hash_part}"


class EvaluationService:
    """RAG评估服务"""

    def __init__(self):
        self.eval_repo = EvaluationRepository()
        self.kb_repo = KnowledgeBaseRepository()
        self.chunk_repo = KnowledgeChunkRepository()
        self.task_repo = TaskRepository()

    def _dataset_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row.dataset_id,
            "dataset_id": row.dataset_id,
            "name": row.name,
            "description": row.description,
            "kb_id": row.kb_id,
            "item_count": row.item_count,
            "has_gold_chunks": row.has_gold_chunks,
            "has_gold_answers": row.has_gold_answers,
            "build_metadata": row.build_metadata or {},
            "created_by": row.created_by,
            "created_at": format_utc_datetime(row.created_at),
            "updated_at": format_utc_datetime(row.updated_at),
        }

    def _dataset_item_to_dict(self, item) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
            "item_index": item.item_index,
            "query": item.query_text,
            "gold_chunk_ids": item.gold_chunk_ids or [],
            "gold_answer": item.gold_answer,
        }

    def _run_item_to_dict(self, item) -> dict[str, Any]:
        return {
            "query": item.query_text,
            "gold_chunk_ids": item.gold_chunk_ids,
            "gold_answer": item.gold_answer,
            "generated_answer": item.generated_answer,
            "retrieved_chunks": item.retrieved_chunks,
            "metrics": item.metrics or {},
        }

    def _is_error_run_item(self, item) -> bool:
        metrics = item.metrics or {}
        return metrics.get("score", 1.0) <= 0.5 or any(
            metrics.get(key, 1.0) < 0.3 for key in metrics if key.startswith("recall@")
        )

    def _normalize_run_name(self, name: str | None, run_id: str) -> str:
        run_name = (name or "").strip()
        if run_name:
            return run_name
        return build_evaluation_run_name(hash_value=run_id.removeprefix("run_"))

    def _run_name_from_row(self, row) -> str:
        name = (getattr(row, "name", None) or "").strip()
        if name:
            return name
        return build_evaluation_run_name(row.started_at, hash_value=row.run_id.removeprefix("run_"))

    async def _sync_dataset_build_metadata(self, row) -> None:
        metadata = dict(row.build_metadata or {})
        if metadata.get("source") != "generated" or metadata.get("status") not in {"pending", "running"}:
            return

        task_id = metadata.get("task_id")
        task = await self.task_repo.get_by_id(task_id) if task_id else None
        if task is None:
            metadata.pop("progress", None)
            metadata.update(status="failed", message="生成任务不存在")
        elif task.status == "success":
            metadata.update(status="completed", progress=100, message=task.message or "完成")
        elif task.status in {"failed", "cancelled"}:
            metadata.pop("progress", None)
            metadata.update(status="failed", message=task.error or task.message or "生成任务失败")
        else:
            metadata.update(status=task.status, progress=task.progress, message=task.message)

        if metadata != (row.build_metadata or {}):
            await self.eval_repo.update_dataset(row.dataset_id, {"build_metadata": metadata})
            row.build_metadata = metadata

    def _build_dataset_items(
        self, dataset_id: str, kb_id: str, questions: list[dict[str, Any]], start_index: int = 0
    ) -> list[dict[str, Any]]:
        return [
            {
                "item_id": f"dataset_item_{uuid.uuid4().hex[:12]}",
                "dataset_id": dataset_id,
                "kb_id": kb_id,
                "item_index": start_index + index,
                "query_text": item["query"],
                "gold_chunk_ids": item.get("gold_chunk_ids") or [],
                "gold_answer": item.get("gold_answer"),
            }
            for index, item in enumerate(questions)
        ]

    def _build_jsonl_content(self, items: list[Any]) -> str:
        lines = []
        for item in items:
            payload = {"query": item.query_text}
            if item.gold_chunk_ids:
                payload["gold_chunk_ids"] = item.gold_chunk_ids
            if item.gold_answer:
                payload["gold_answer"] = item.gold_answer
            lines.append(dump_benchmark_item(payload).rstrip("\n"))
        return "\n".join(lines) + ("\n" if lines else "")

    def _safe_jsonl_filename(self, name: str | None, fallback: str) -> str:
        filename = (name or "").strip() or fallback
        filename = re.sub(r"[\\/:*?\"<>|]+", "_", filename).strip()
        if not filename or filename in {".", ".."}:
            filename = fallback
        return filename if filename.endswith(".jsonl") else f"{filename}.jsonl"

    def _parse_jsonl_questions(self, file_content: bytes) -> tuple[list[dict[str, Any]], bool, bool]:
        questions = []
        has_gold_chunks = False
        has_gold_answers = False
        content = file_content.decode("utf-8")

        for line_num, line in enumerate(content.strip().split("\n"), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"第{line_num}行JSON格式错误: {str(e)}")
            if "query" not in item:
                raise ValueError(f"第{line_num}行缺少必需的'query'字段")
            if item.get("gold_chunk_ids"):
                has_gold_chunks = True
            if item.get("gold_answer"):
                has_gold_answers = True
            questions.append(item)

        if not questions:
            raise ValueError("文件中没有有效的问题数据")
        return questions, has_gold_chunks, has_gold_answers

    async def upload_dataset(
        self, kb_id: str, file_content: bytes, filename: str, name: str, description: str, created_by: str
    ) -> dict[str, Any]:
        try:
            questions, has_gold_chunks, has_gold_answers = self._parse_jsonl_questions(file_content)
            dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
            dataset_name = name.strip() or filename or dataset_id

            row = await self.eval_repo.create_dataset_with_items(
                {
                    "dataset_id": dataset_id,
                    "kb_id": kb_id,
                    "name": dataset_name,
                    "description": description,
                    "item_count": len(questions),
                    "has_gold_chunks": has_gold_chunks,
                    "has_gold_answers": has_gold_answers,
                    "build_metadata": {
                        "source": "upload",
                        "status": "completed",
                        "progress": 100,
                        "filename": filename,
                    },
                    "created_by": created_by,
                },
                self._build_dataset_items(dataset_id, kb_id, questions),
            )
            return self._dataset_to_dict(row)
        except Exception as e:
            logger.error(f"上传评估数据集失败: {e}")
            raise

    async def list_datasets(self, kb_id: str) -> list[dict[str, Any]]:
        try:
            rows = await self.eval_repo.list_datasets(kb_id)
            for row in rows:
                await self._sync_dataset_build_metadata(row)
            return [self._dataset_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"获取评估数据集列表失败: {e}")
            raise

    async def get_dataset_detail(
        self, kb_id: str, dataset_id: str, page: int = 1, page_size: int = 10
    ) -> dict[str, Any]:
        try:
            row = await self.eval_repo.get_dataset(dataset_id)
            if row is None or row.kb_id != kb_id:
                raise ValueError("Dataset not found")
            if (row.build_metadata or {}).get("status", "completed") not in {"completed", "failed"}:
                raise ValueError("Dataset is not ready")

            total_items = await self.eval_repo.count_dataset_items(dataset_id)
            items = await self.eval_repo.list_dataset_items(dataset_id, (page - 1) * page_size, page_size)
            total_pages = (total_items + page_size - 1) // page_size
            data = self._dataset_to_dict(row)
            data.update(
                {
                    "items": [self._dataset_item_to_dict(item) for item in items],
                    "pagination": {
                        "current_page": page,
                        "page_size": page_size,
                        "total_items": total_items,
                        "total_pages": total_pages,
                        "has_next": page < total_pages,
                        "has_prev": page > 1,
                    },
                }
            )
            return data
        except Exception as e:
            logger.error(f"获取评估数据集详情失败: {e}")
            raise

    async def export_dataset_jsonl(self, dataset_id: str) -> dict[str, str]:
        row = await self.eval_repo.get_dataset(dataset_id)
        if row is None:
            raise ValueError("Dataset not found")
        if (row.build_metadata or {}).get("status", "completed") != "completed":
            raise ValueError("Dataset is not ready")
        items = await self.eval_repo.list_all_dataset_items(dataset_id)
        return {
            "filename": self._safe_jsonl_filename(row.name, row.dataset_id),
            "content": self._build_jsonl_content(items),
        }

    async def delete_dataset(self, dataset_id: str) -> None:
        try:
            row = await self.eval_repo.get_dataset(dataset_id)
            if row is None:
                raise ValueError("Dataset not found")
            await self.eval_repo.delete_dataset(dataset_id)
            logger.info(f"成功删除评估数据集: {dataset_id}")
        except Exception as e:
            logger.error(f"删除评估数据集失败: {e}")
            raise

    async def resume_dataset_generation(self, kb_id: str, dataset_id: str, created_by: str) -> dict[str, Any]:
        row = await self.eval_repo.get_dataset(dataset_id)
        if row is None or row.kb_id != kb_id:
            raise ValueError("Dataset not found")
        metadata = dict(row.build_metadata or {})
        if metadata.get("source") != "generated":
            raise ValueError("只能恢复自动生成的数据集")
        params = metadata.get("params") or {}
        if not params:
            raise ValueError("数据集缺少生成参数")

        existing_count = await self.eval_repo.count_dataset_items(dataset_id)
        total_count = int(params.get("count", 0))
        if existing_count >= total_count:
            await self.eval_repo.update_dataset(dataset_id, {"item_count": existing_count})
            await self._update_dataset_build_metadata(
                dataset_id,
                metadata,
                status="completed",
                progress=100,
                message="完成",
            )
            return {"dataset_id": dataset_id, "message": "数据集已完成生成"}

        payload = {
            "dataset_id": dataset_id,
            "kb_id": kb_id,
            "created_by": created_by,
            "name": row.name,
            "description": row.description,
            "count": total_count,
            "neighbors_count": int(params.get("neighbors_count", 1)),
            "concurrency_count": int(params.get("concurrency_count", 10)),
            "llm_model_spec": params.get("llm_model_spec"),
            "generation_mode": params.get("generation_mode", "vector"),
            "graph_expand_top_k": int(params.get("graph_expand_top_k", 1)),
        }
        task, created = await tasker.enqueue_unique_by_payload(
            name="继续生成评估数据集",
            task_type="dataset_generation",
            payload=payload,
            coroutine=self._generate_dataset_task,
            payload_match={"dataset_id": dataset_id},
            statuses={"pending", "running"},
        )
        if not created:
            return {
                "dataset_id": dataset_id,
                "task_id": task.id,
                "message": "已有进行中的生成任务",
            }
        metadata["status"] = "pending"
        metadata["task_id"] = task.id
        metadata["progress"] = int(99 * existing_count / max(total_count, 1))
        metadata["message"] = "恢复生成中"
        await self._update_dataset_build_metadata(dataset_id, metadata)
        return {
            "dataset_id": dataset_id,
            "task_id": task.id,
            "message": "评估数据集生成任务已恢复",
        }

    async def generate_dataset(
        self,
        kb_id: str,
        name: str,
        description: str,
        count: int,
        neighbors_count: int,
        concurrency_count: int,
        llm_model_spec: str,
        generation_mode: str = "vector",
        graph_expand_top_k: int = 1,
        created_by: str = "system",
    ) -> dict[str, Any]:
        dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
        count = int(count)
        neighbors_count = int(neighbors_count)
        concurrency_count = normalize_generation_concurrency_count(concurrency_count)
        graph_expand_top_k = min(max(1, int(graph_expand_top_k)), 3)
        if generation_mode not in {"vector", "graph_enhanced"}:
            raise ValueError("不支持的评估基准生成方式")
        if generation_mode == "graph_enhanced":
            indexed_count = await self.chunk_repo.count_graph_indexed_by_kb_id(kb_id)
            if indexed_count <= 0:
                raise ValueError("当前知识库尚未完成图索引，无法使用图增强构建")
        build_metadata = {
            "source": "generated",
            "status": "pending",
            "progress": 0,
            "params": {
                "count": count,
                "neighbors_count": neighbors_count,
                "concurrency_count": concurrency_count,
                "llm_model_spec": llm_model_spec,
                "generation_mode": generation_mode,
                "graph_expand_top_k": graph_expand_top_k,
            },
        }
        await self.eval_repo.create_dataset(
            {
                "dataset_id": dataset_id,
                "kb_id": kb_id,
                "name": name,
                "description": description,
                "item_count": 0,
                "has_gold_chunks": True,
                "has_gold_answers": True,
                "build_metadata": build_metadata,
                "created_by": created_by,
            }
        )
        task = await tasker.enqueue(
            name="生成评估数据集",
            task_type="dataset_generation",
            payload={
                "dataset_id": dataset_id,
                "kb_id": kb_id,
                "created_by": created_by,
                "name": name,
                "description": description,
                "count": count,
                "neighbors_count": neighbors_count,
                "concurrency_count": concurrency_count,
                "llm_model_spec": llm_model_spec,
                "generation_mode": generation_mode,
                "graph_expand_top_k": graph_expand_top_k,
            },
            coroutine=self._generate_dataset_task,
        )
        build_metadata["task_id"] = task.id
        await self.eval_repo.update_dataset(dataset_id, {"build_metadata": build_metadata})
        return {"dataset_id": dataset_id, "task_id": task.id, "message": "评估数据集生成任务已提交"}

    async def _update_dataset_build_metadata(
        self, dataset_id: str, metadata: dict[str, Any], **updates
    ) -> dict[str, Any]:
        metadata.update(updates)
        await self.eval_repo.update_dataset(dataset_id, {"build_metadata": metadata})
        return metadata

    async def _generate_dataset_task(self, context: TaskContext):
        await context.set_progress(0, "初始化")
        payload = context.payload

        dataset_id = payload.get("dataset_id")
        kb_id = payload.get("kb_id")
        total_count = int(payload.get("count", 10))
        neighbors_count = int(payload.get("neighbors_count", 1))
        concurrency_count = normalize_generation_concurrency_count(payload.get("concurrency_count"))
        llm_model_spec = payload.get("llm_model_spec")
        generation_mode = payload.get("generation_mode") or "vector"
        graph_expand_top_k = min(max(1, int(payload.get("graph_expand_top_k", 1))), 3)

        existing_count = await self.eval_repo.count_dataset_items(dataset_id)
        if existing_count >= total_count:
            completed_metadata = {
                "source": "generated",
                "status": "completed",
                "progress": 100,
                "task_id": context.task_id,
                "params": {
                    "count": total_count,
                    "neighbors_count": neighbors_count,
                    "concurrency_count": concurrency_count,
                    "llm_model_spec": llm_model_spec,
                    "generation_mode": generation_mode,
                    "graph_expand_top_k": graph_expand_top_k,
                },
            }
            await self._update_dataset_build_metadata(dataset_id, completed_metadata)
            await self.eval_repo.update_dataset(dataset_id, {"item_count": existing_count})
            await context.set_progress(100, "完成")
            return

        remaining_count = total_count - existing_count
        start_index = existing_count

        build_metadata = {
            "source": "generated",
            "status": "running",
            "progress": int(99 * existing_count / total_count),
            "task_id": context.task_id,
            "params": {
                "count": total_count,
                "neighbors_count": neighbors_count,
                "concurrency_count": concurrency_count,
                "llm_model_spec": llm_model_spec,
                "generation_mode": generation_mode,
                "graph_expand_top_k": graph_expand_top_k,
            },
        }
        await self._update_dataset_build_metadata(dataset_id, build_metadata)

        async def report_progress(progress: float, message: str | None = None) -> None:
            await context.set_progress(progress, message)
            await self._update_dataset_build_metadata(
                dataset_id,
                build_metadata,
                progress=max(0, min(round(progress), 100)),
                message=message or build_metadata.get("message", ""),
            )

        batch_size = DATASET_PERSIST_BATCH_SIZE
        buffer: list[dict[str, Any]] = []

        async def flush_items() -> None:
            if not buffer:
                return
            nonlocal start_index
            await self.eval_repo.add_dataset_items(self._build_dataset_items(dataset_id, kb_id, buffer, start_index))
            start_index += len(buffer)
            buffer.clear()
            await self.eval_repo.update_dataset(dataset_id, {"item_count": start_index})

        async def flush_items_best_effort() -> None:
            try:
                await flush_items()
            except Exception:
                logger.exception(f"保存残余题目失败: {dataset_id}")

        try:
            kb_instance = await knowledge_base.aget_kb(kb_id)
            if not kb_instance:
                await report_progress(100, "知识库不存在")
                raise ValueError("Knowledge Base not found")
            if kb_instance.kb_type != "milvus":
                await report_progress(100, "仅支持 commonrag/Milvus 类型知识库生成评估数据集")
                raise ValueError("Unsupported KB type for dataset generation")

            try:
                async for item in iter_generated_benchmark_items(
                    kb_instance=kb_instance,
                    kb_id=kb_id,
                    count=remaining_count,
                    neighbors_count=neighbors_count,
                    llm_model_spec=llm_model_spec,
                    concurrency_count=concurrency_count,
                    generation_mode=generation_mode,
                    graph_expand_top_k=graph_expand_top_k,
                    progress_base=existing_count,
                    total_progress=total_count,
                    progress_cb=report_progress,
                    cancel_cb=context.raise_if_cancelled,
                ):
                    buffer.append(item)
                    if len(buffer) >= batch_size:
                        await flush_items_best_effort()
            except ValueError as e:
                if str(e) == "No chunks found in knowledge base":
                    await report_progress(100, "知识库为空或未解析到chunks")
                raise

            await flush_items()

            if start_index < total_count:
                raise ValueError(f"仅生成 {start_index}/{total_count} 道有效评估题目")

            await self._update_dataset_build_metadata(
                dataset_id,
                build_metadata,
                status="completed",
                progress=100,
                message="完成",
            )
            await context.set_progress(100, "完成")
        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
            error = str(e)
            if isinstance(e, asyncio.CancelledError):
                if context.is_cancel_requested():
                    error = "任务已取消"
                elif context.cancellation_reason == "timeout":
                    error = "任务执行超时"
                else:
                    error = "服务停止，任务执行中断"
            await flush_items_best_effort()
            await self._update_dataset_build_metadata(
                dataset_id,
                build_metadata,
                status="failed",
                progress=100,
                error_message=error,
                message=error,
            )
            raise

    async def run_evaluation(
        self,
        kb_id: str,
        dataset_id: str,
        name: str | None = None,
        model_config: dict[str, Any] = None,
        created_by: str = "system",
    ) -> str:
        try:
            run_id = f"run_{uuid.uuid4().hex[:8]}"
            run_name = self._normalize_run_name(name, run_id)
            dataset_row = await self.eval_repo.get_dataset(dataset_id)
            if dataset_row is None or dataset_row.kb_id != kb_id:
                raise ValueError("Dataset not found")
            if (dataset_row.build_metadata or {}).get("status", "completed") != "completed":
                raise ValueError("Dataset is not ready")

            retrieval_config = {}
            try:
                kb_row = await self.kb_repo.get_by_kb_id(kb_id)
                query_params = (kb_row.query_params if kb_row else None) or {}
                retrieval_config = query_params.get("options", {}) if isinstance(query_params, dict) else {}
                if not retrieval_config:
                    kb_instance = await knowledge_base.aget_kb(kb_id)
                    if kb_instance:
                        retrieval_config = kb_instance._get_default_query_params(kb_id).get("options", {})
                logger.info(f"从知识库 {kb_id} 加载检索配置: {list(retrieval_config.keys())}")
            except Exception as e:
                logger.error(f"获取知识库检索配置失败: {e}")

            if model_config:
                retrieval_config.update(model_config)

            await self.eval_repo.create_run(
                {
                    "run_id": run_id,
                    "name": run_name,
                    "kb_id": kb_id,
                    "dataset_id": dataset_id,
                    "status": "running",
                    "retrieval_config": retrieval_config,
                    "metrics": {},
                    "overall_score": None,
                    "total_items": dataset_row.item_count or 0,
                    "completed_items": 0,
                    "started_at": utc_now_naive(),
                    "completed_at": None,
                    "created_by": created_by,
                }
            )

            await tasker.enqueue(
                name=f"RAG评估({run_name})",
                task_type="rag_evaluation",
                payload={
                    "run_id": run_id,
                    "name": run_name,
                    "kb_id": kb_id,
                    "dataset_id": dataset_id,
                    "retrieval_config": retrieval_config,
                    "created_by": created_by,
                },
                coroutine=self._run_evaluation_task,
            )
            return run_id
        except Exception as e:
            logger.error(f"启动评估失败: {e}")
            raise

    async def _run_evaluation_task(self, context: TaskContext):
        try:
            payload = context.payload

            run_id = payload["run_id"]
            kb_id = payload["kb_id"]
            dataset_id = payload["dataset_id"]
            retrieval_config = payload["retrieval_config"]

            await context.set_progress(5, "加载评估数据集")
            dataset_row = await self.eval_repo.get_dataset(dataset_id)
            if dataset_row is None or dataset_row.kb_id != kb_id:
                raise ValueError("Dataset not found")
            dataset_items = await self.eval_repo.list_all_dataset_items(dataset_id)
            if not dataset_items:
                raise ValueError("Dataset has no items")

            kb_instance = await knowledge_base.aget_kb(kb_id)
            if not kb_instance:
                raise ValueError(f"Knowledge Base {kb_id} not found")

            judge_llm = None
            if dataset_row.has_gold_answers:
                judge_model_spec = retrieval_config.get("judge_llm") or retrieval_config.get("answer_llm")
                if judge_model_spec:
                    try:
                        logger.debug(f"Initializing Judge LLM: {judge_model_spec}")
                        judge_llm = select_model(model_spec=judge_model_spec)
                    except Exception as e:
                        logger.error(f"Failed to load judge LLM: {e}")

            all_retrieval_metrics = []
            all_answer_metrics = []
            total_items = len(dataset_items)

            async def update_run_db(status=None, completed=None, metrics=None, final_score=None):
                data = {}
                if status is not None:
                    data["status"] = status
                    if status in ["completed", "failed"]:
                        data["completed_at"] = utc_now_naive()
                if completed is not None:
                    data["completed_items"] = completed
                if metrics is not None:
                    data["metrics"] = metrics
                if final_score is not None:
                    data["overall_score"] = final_score
                if data:
                    await self.eval_repo.update_run(run_id, data)

            for index, item in enumerate(dataset_items):
                await context.raise_if_cancelled()
                progress = 10 + (index / total_items) * 80
                await context.set_progress(progress, f"评估 {index + 1}/{total_items}")

                question_data = {
                    "query": item.query_text,
                    "gold_chunk_ids": item.gold_chunk_ids or [],
                    "gold_answer": item.gold_answer,
                }
                question_result = await evaluate_question(
                    kb_instance=kb_instance,
                    kb_id=kb_id,
                    question_data=question_data,
                    retrieval_config=retrieval_config,
                    has_gold_chunks=dataset_row.has_gold_chunks,
                    has_gold_answers=dataset_row.has_gold_answers,
                    judge_llm=judge_llm,
                    select_model_fn=select_model,
                )

                if dataset_row.has_gold_chunks and question_data.get("gold_chunk_ids"):
                    all_retrieval_metrics.append(question_result["retrieval_scores"])
                if dataset_row.has_gold_answers and question_data.get("gold_answer") and judge_llm:
                    all_answer_metrics.append(question_result["answer_scores"])

                await self.eval_repo.upsert_run_item(
                    run_id=run_id,
                    item_index=index,
                    data={"dataset_item_id": item.item_id, **question_result["detail"]},
                )

                if (index + 1) % 5 == 0 or (index + 1) == total_items:
                    current_metrics, _ = aggregate_metrics(all_retrieval_metrics, all_answer_metrics)
                    await context.set_result(
                        {"current_metrics": current_metrics, "completed_items": index + 1, "total_items": total_items}
                    )
                    await update_run_db(completed=index + 1)

            await context.set_progress(95, "计算最终指标")
            overall_metrics, overall_score = aggregate_metrics(
                all_retrieval_metrics, all_answer_metrics, include_overall_score=True
            )
            await update_run_db(
                status="completed",
                completed=total_items,
                metrics=overall_metrics,
                final_score=overall_score,
            )
            await context.set_progress(100, "完成")
        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
            error = str(e)
            if isinstance(e, asyncio.CancelledError):
                if context.is_cancel_requested():
                    error = "任务已取消"
                elif context.cancellation_reason == "timeout":
                    error = "任务执行超时"
                else:
                    error = "服务停止，任务执行中断"
            logger.error(f"Task failed: {error}")
            try:
                if "payload" in locals():
                    await self.eval_repo.update_run(
                        payload["run_id"],
                        {"status": "failed", "metrics": {"error": error}, "completed_at": utc_now_naive()},
                    )
            except Exception as exc:
                logger.error(f"Error updating run record: {exc}")
            await context.set_message(f"Error: {error}")
            raise

    async def list_runs(self, kb_id: str) -> list[dict[str, Any]]:
        try:
            rows = await self.eval_repo.list_runs(kb_id)
            running_run_ids = {row.run_id for row in rows if row.status == "running"}
            task_by_run_id = {}
            if running_run_ids:
                tasks = await self.task_repo.list_all()
                task_by_run_id = {
                    (task.payload or {}).get("run_id"): task
                    for task in tasks
                    if task.type == "rag_evaluation"
                    and task.status in {"pending", "running"}
                    and (task.payload or {}).get("run_id") in running_run_ids
                }

            runs = []
            for row in rows:
                run = {
                    "run_id": row.run_id,
                    "name": self._run_name_from_row(row),
                    "dataset_id": row.dataset_id,
                    "status": row.status,
                    "started_at": format_utc_datetime(row.started_at),
                    "completed_at": format_utc_datetime(row.completed_at),
                    "total_items": row.total_items,
                    "completed_items": row.completed_items,
                    "overall_score": row.overall_score,
                    "retrieval_config": row.retrieval_config or {},
                    "metrics": row.metrics or {},
                }
                if row.status == "running":
                    task = task_by_run_id.get(row.run_id)
                    if task:
                        run.update(progress=task.progress, message=task.message)
                runs.append(run)
            return runs
        except Exception as e:
            logger.error(f"获取评估运行历史失败: {e}")
            raise

    async def get_run_results(
        self, kb_id: str, run_id: str, page: int = 1, page_size: int = 20, error_only: bool = False
    ) -> dict[str, Any]:
        if not re.match(r"^run_[a-f0-9]{8}$", run_id):
            raise ValueError("Invalid run_id format")
        row = await self.eval_repo.get_run(run_id)
        if row is None or row.kb_id != kb_id:
            task = await tasker.get_task(run_id)
            if task:
                return {"run_id": run_id, "status": task.status, "progress": task.progress, "message": task.message}
            raise ValueError(f"Run not found for {run_id}")

        start_idx = (page - 1) * page_size
        if error_only:
            total = 0
            paged_items = []
            offset = 0
            batch_size = 200
            while True:
                batch = await self.eval_repo.list_run_items(run_id, offset, batch_size)
                if not batch:
                    break
                for item in batch:
                    if not self._is_error_run_item(item):
                        continue
                    if start_idx <= total < start_idx + page_size:
                        paged_items.append(self._run_item_to_dict(item))
                    total += 1
                offset += batch_size
        else:
            total = await self.eval_repo.count_run_items(run_id)
            details = await self.eval_repo.list_run_items(run_id, start_idx, page_size)
            paged_items = [self._run_item_to_dict(item) for item in details]
        return {
            "run_id": row.run_id,
            "name": self._run_name_from_row(row),
            "status": row.status,
            "started_at": format_utc_datetime(row.started_at),
            "completed_at": format_utc_datetime(row.completed_at),
            "total_items": row.total_items or 0,
            "completed_items": row.completed_items or 0,
            "overall_score": row.overall_score,
            "retrieval_config": row.retrieval_config or {},
            "items": paged_items,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
                "error_only": error_only,
            },
        }

    async def delete_run(self, kb_id: str, run_id: str) -> None:
        if not re.match(r"^run_[a-f0-9]{8}$", run_id):
            raise ValueError("Invalid run_id format")
        row = await self.eval_repo.get_run(run_id)
        if row is None or row.kb_id != kb_id:
            raise ValueError("Run not found")
        await self.eval_repo.delete_run(run_id)
        logger.info(f"成功删除评估运行: {run_id}")
