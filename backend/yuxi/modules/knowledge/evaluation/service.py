"""RAG 评估编排服务：数据集生命周期、评估运行调度与只读进度展示。

指标计算在 evaluator/metrics，逐题生成在 benchmark_generation，
持久化在 repositories/evaluation；本模块负责流程编排，终态由后台作业事务维护。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from typing import TYPE_CHECKING, Any

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.repository import BackgroundJobRepository
from yuxi.modules.knowledge.evaluation.benchmark_generation import (
    EmptyKnowledgeBaseError,
    dump_benchmark_item,
    iter_generated_benchmark_items,
    normalize_generation_concurrency_count,
    normalize_graph_expand_top_k,
)
from yuxi.modules.knowledge.evaluation.evaluator import EVALUATION_MODEL_KEYS, aggregate_metrics, evaluate_question
from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository
from yuxi.modules.knowledge.repositories.evaluation import (
    EvaluationRepository,
)
from yuxi.modules.knowledge.repositories.evaluation import (
    fail_dataset_generation_job as fail_dataset_generation_job,
)
from yuxi.modules.knowledge.repositories.evaluation import (
    fail_rag_evaluation_job as fail_rag_evaluation_job,
)
from yuxi.modules.knowledge.repositories.evaluation import (
    finish_dataset_generation_job as finish_dataset_generation_job,
)
from yuxi.modules.knowledge.repositories.evaluation import (
    finish_rag_evaluation_job as finish_rag_evaluation_job,
)
from yuxi.modules.knowledge.runtime import knowledge_base as kb_manager
from yuxi.modules.knowledge.services.retrieval import normalize_query_overrides, require_file_ids_in_kb
from yuxi.modules.models import select_model
from yuxi.shared.errors import InvalidInputError

if TYPE_CHECKING:
    from yuxi.workers.background_job_context import BackgroundJobContext
from yuxi.modules.background_jobs.dispatch import dispatch_job, register_job_in_session
from yuxi.shared.datetime import format_utc_datetime, utc_now

DATASET_PERSIST_BATCH_SIZE = max(1, int(os.getenv("YUXI_DATASET_PERSIST_BATCH_SIZE") or 1))


class _DatasetAlreadyCompleted(RuntimeError):
    """并发 resume 取得行锁时数据集已经完成。"""


def build_evaluation_run_name(started_at=None, hash_value: str | None = None) -> str:
    """生成 eval-日期-哈希 形式的默认运行名。"""
    date_part = (started_at or utc_now()).strftime("%Y%m%d")
    hash_part = re.sub(r"[^a-fA-F0-9]", "", hash_value or uuid.uuid4().hex).lower()[:6]
    if len(hash_part) < 6:
        hash_part = (hash_part + uuid.uuid4().hex)[:6]
    return f"eval-{date_part}-{hash_part}"


class EvaluationService:
    """RAG 评估编排：数据集生命周期、评估运行调度与后台作业终态投影。"""

    def __init__(self):
        self.eval_repo = EvaluationRepository()
        self.kb_repo = KnowledgeBaseRepository()
        self.chunk_repo = KnowledgeChunkRepository()
        self.job_repo = BackgroundJobRepository()

    # ---------- 数据集：上传与自动生成 ----------

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
        graph_expand_top_k = normalize_graph_expand_top_k(graph_expand_top_k)
        if generation_mode not in {"vector", "graph_enhanced"}:
            raise ValueError("不支持的评估基准生成方式")
        if generation_mode == "graph_enhanced":
            indexed_count = await self.chunk_repo.count_graph_indexed_by_kb_id(kb_id)
            if indexed_count <= 0:
                raise ValueError("当前知识库尚未完成图索引，无法使用图增强构建")
        generation_params = {
            "count": count,
            "neighbors_count": neighbors_count,
            "concurrency_count": concurrency_count,
            "llm_model_spec": llm_model_spec,
            "generation_mode": generation_mode,
            "graph_expand_top_k": graph_expand_top_k,
        }
        build_metadata = {
            "source": "generated",
            "status": "pending",
            "progress": 0,
            "params": generation_params,
        }
        job_payload = {
            "dataset_id": dataset_id,
            "kb_id": kb_id,
            "created_by": created_by,
            "name": name,
            "description": description,
            **generation_params,
        }
        # 同一事务内先登记作业再建数据集，build_metadata 借助 job_id 关联进度投影
        async with pg_manager.get_async_session_context() as session:
            job, _ = await register_job_in_session(
                session,
                name="生成评估数据集",
                job_type="dataset_generation",
                payload=job_payload,
                payload_match={"dataset_id": dataset_id},
            )
            build_metadata["job_id"] = job.id
            await self.eval_repo.create_dataset_in_session(
                session,
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
                },
            )
        await dispatch_job(job.id)
        return {"dataset_id": dataset_id, "job_id": job.id, "message": "评估数据集生成任务已提交"}

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
            metadata.update(status="completed", progress=100, message="完成")
            await self.eval_repo.update_dataset(
                dataset_id,
                {"item_count": existing_count, "build_metadata": metadata},
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
        try:
            # attach 在行锁内做并发 resume 判定：状态被并发方推进为完成时放弃本次恢复
            async with pg_manager.get_async_session_context() as session:
                job, created = await register_job_in_session(
                    session,
                    name="继续生成评估数据集",
                    job_type="dataset_generation",
                    payload=payload,
                    payload_match={"dataset_id": dataset_id},
                )
                attached = await self.eval_repo.attach_dataset_generation_job_in_session(session, dataset_id, job.id)
                if attached is None:
                    raise ValueError("Dataset not found")
                attached_metadata = attached.build_metadata or {}
                if attached_metadata.get("status") == "completed" and attached_metadata.get("job_id") != job.id:
                    raise _DatasetAlreadyCompleted
        except _DatasetAlreadyCompleted:
            return {"dataset_id": dataset_id, "message": "数据集已完成生成"}
        if not created:
            return {
                "dataset_id": dataset_id,
                "job_id": job.id,
                "message": "已有进行中的生成任务",
            }
        await dispatch_job(job.id)
        return {
            "dataset_id": dataset_id,
            "job_id": job.id,
            "message": "评估数据集生成任务已恢复",
        }

    # ---------- 数据集：生成作业 Handler ----------

    async def _generate_dataset_job(self, context: BackgroundJobContext):
        await context.set_progress(0, "初始化")
        payload = context.payload

        dataset_id = payload.get("dataset_id")
        kb_id = payload.get("kb_id")
        total_count = int(payload.get("count", 10))
        neighbors_count = int(payload.get("neighbors_count", 1))
        concurrency_count = normalize_generation_concurrency_count(payload.get("concurrency_count"))
        llm_model_spec = payload.get("llm_model_spec")
        generation_mode = payload.get("generation_mode") or "vector"
        graph_expand_top_k = normalize_graph_expand_top_k(payload.get("graph_expand_top_k", 1))
        generation_params = {
            "count": total_count,
            "neighbors_count": neighbors_count,
            "concurrency_count": concurrency_count,
            "llm_model_spec": llm_model_spec,
            "generation_mode": generation_mode,
            "graph_expand_top_k": graph_expand_top_k,
        }

        existing_count = await self.eval_repo.count_dataset_items(dataset_id)
        if existing_count >= total_count:
            # 重放已完成的作业：直接投影完成态，不重复生成
            completed_metadata = {
                "source": "generated",
                "status": "completed",
                "progress": 100,
                "job_id": context.job_id,
                "params": generation_params,
            }
            await context.set_progress(100, "完成")
            return {
                "dataset_id": dataset_id,
                "item_count": existing_count,
                "build_metadata": completed_metadata,
            }

        remaining_count = total_count - existing_count
        start_index = existing_count

        build_metadata = {
            "source": "generated",
            "status": "running",
            "progress": int(99 * existing_count / total_count),
            "job_id": context.job_id,
            "params": generation_params,
        }

        async def persist_build_metadata(**updates) -> None:
            build_metadata.update(updates)

            async def operation(session, _job_record) -> None:
                record = await self.eval_repo.update_dataset_in_session(
                    session,
                    dataset_id,
                    {"build_metadata": build_metadata},
                )
                if record is None:
                    raise ValueError("Dataset not found")

            await context.run_owned_transaction(operation)

        await persist_build_metadata()

        async def report_progress(progress: float, message: str | None = None) -> None:
            # 进度双写：作业进度与数据集 build_metadata 同步推进，列表页据此显示生成状态
            await context.set_progress(progress, message)
            await persist_build_metadata(
                progress=max(0, min(round(progress), 100)),
                message=message or build_metadata.get("message", ""),
            )

        batch_size = DATASET_PERSIST_BATCH_SIZE
        buffer: list[dict[str, Any]] = []

        async def flush_items() -> None:
            if not buffer:
                return
            nonlocal start_index
            items = self._build_dataset_items(dataset_id, kb_id, buffer, start_index)
            next_index = start_index + len(items)

            async def operation(session, _job_record) -> None:
                await self.eval_repo.add_dataset_items_in_session(session, items)
                record = await self.eval_repo.update_dataset_in_session(
                    session,
                    dataset_id,
                    {"item_count": next_index},
                )
                if record is None:
                    raise ValueError("Dataset not found")

            await context.run_owned_transaction(operation)
            start_index = next_index
            buffer.clear()

        async def flush_items_best_effort() -> None:
            try:
                await flush_items()
            except Exception:
                logger.exception(f"保存残余题目失败: {dataset_id}")

        try:
            kb_config = await kb_manager.get_kb_config(kb_id)
            if kb_config.kb_type != "milvus":
                await report_progress(100, "仅支持 commonrag/Milvus 类型知识库生成评估数据集")
                raise ValueError("Unsupported KB type for dataset generation")

            try:
                async for item in iter_generated_benchmark_items(
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
            except EmptyKnowledgeBaseError:
                # 空知识库：进度置满提示根因，再原样上抛交给 failure hook 收敛
                await report_progress(100, "知识库为空或未解析到chunks")
                raise

            await flush_items()

            if start_index < total_count:
                raise ValueError(f"仅生成 {start_index}/{total_count} 道有效评估题目")

            await context.raise_if_cancelled()
            completed_metadata = {
                **build_metadata,
                "status": "completed",
                "progress": 100,
                "message": "完成",
            }
            await context.set_progress(100, "完成")
            return {
                "dataset_id": dataset_id,
                "item_count": start_index,
                "build_metadata": completed_metadata,
            }
        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                if context.cancellation_reason == "lease_lost":
                    # 旧 attempt 已失权：禁止任何后续落库，原样上抛交由作业框架收敛
                    raise
                # 外部取消：清除取消计数，保证残余题目落库与最终上抛仍可执行
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
            await flush_items_best_effort()
            raise

    # ---------- 数据集：查询、导出与删除 ----------

    async def get_dataset(self, dataset_id: str):
        """读取评估数据集；不存在时返回 None，由入口映射为拒绝。"""
        return await self.eval_repo.get_dataset(dataset_id)

    async def list_datasets(self, kb_id: str) -> list[dict[str, Any]]:
        try:
            rows = await self.eval_repo.list_datasets(kb_id)
            datasets = []
            for row in rows:
                data = self._dataset_to_dict(row)
                data["build_metadata"] = await self._dataset_build_metadata(row)
                datasets.append(data)
            return datasets
        except Exception as e:
            logger.error(f"获取评估数据集列表失败: {e}")
            raise

    async def get_dataset_detail(self, kb_id: str, dataset_id: str, page: int = 1, page_size: int = 10) -> dict[str, Any]:
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

    # ---------- 评估运行：启动与执行 ----------

    @staticmethod
    def _evaluation_model_values(model_config: dict) -> dict[str, str]:
        """取出评估自己的模型标识。检索键留给查询参数声明校验。"""

        values = {}
        for key in EVALUATION_MODEL_KEYS:
            if key not in model_config:
                continue
            value = model_config[key]
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise InvalidInputError(f"{key} 必须是模型标识")
            values[key] = value.strip()
        return values

    async def run_evaluation(
        self,
        kb_id: str,
        dataset_id: str,
        name: str | None = None,
        model_config: dict[str, Any] | None = None,
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

            # 检索配置失败不阻断启动：回退为空配置，仅叠加显式传入的 model_config
            retrieval_config = {}
            kb_row = None
            try:
                kb_row = await self.kb_repo.get_by_kb_id(kb_id)
                query_params = (kb_row.query_params if kb_row else None) or {}
                retrieval_config = query_params.get("options", {}) if isinstance(query_params, dict) else {}
                if not retrieval_config:
                    retrieval_config = (await kb_manager.get_kb_config(kb_id)).query_options
                logger.info(f"从知识库 {kb_id} 加载检索配置: {list(retrieval_config.keys())}")
            except Exception as e:
                logger.error(f"获取知识库检索配置失败: {e}")

            if model_config:
                if not isinstance(model_config, dict):
                    raise InvalidInputError("检索参数必须是对象")
                retrieval_config.update(self._evaluation_model_values(model_config))
                retrieval_overrides = {key: value for key, value in model_config.items() if key not in EVALUATION_MODEL_KEYS}
                if retrieval_overrides:
                    kb_type = (getattr(kb_row, "kb_type", None) if kb_row else None) or "milvus"
                    kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)
                    guarded = normalize_query_overrides(kb_class, retrieval_overrides)
                    if "file_ids" in guarded:
                        await require_file_ids_in_kb(kb_id, guarded["file_ids"])
                    retrieval_config.update(guarded)

            job_payload = {
                "run_id": run_id,
                "name": run_name,
                "kb_id": kb_id,
                "dataset_id": dataset_id,
                "retrieval_config": retrieval_config,
                "created_by": created_by,
            }
            async with pg_manager.get_async_session_context() as session:
                job, _ = await register_job_in_session(
                    session,
                    name=f"RAG评估({run_name})",
                    job_type="rag_evaluation",
                    payload=job_payload,
                    payload_match={"run_id": run_id},
                )
                await self.eval_repo.create_run_in_session(
                    session,
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
                        "started_at": utc_now(),
                        "completed_at": None,
                        "created_by": created_by,
                    },
                )
            await dispatch_job(job.id)
            return run_id
        except Exception as e:
            logger.error(f"启动评估失败: {e}")
            raise

    async def _run_evaluation_job(self, context: BackgroundJobContext):
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

            for index, item in enumerate(dataset_items):
                await context.raise_if_cancelled()
                # 进度契约：逐题评估映射到 10~90 区间，聚合与收尾占用其后区间
                progress = 10 + (index / total_items) * 80
                await context.set_progress(progress, f"评估 {index + 1}/{total_items}")

                question_data = {
                    "query": item.query_text,
                    "gold_chunk_ids": item.gold_chunk_ids or [],
                    "gold_answer": item.gold_answer,
                }
                question_result = await evaluate_question(
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

                await self._persist_run_item(
                    context,
                    run_id,
                    index,
                    {"dataset_item_id": item.item_id, **question_result["detail"]},
                )

                # 每 5 题与最后一题刷新中间指标，供前端展示当前聚合结果
                if (index + 1) % 5 == 0 or (index + 1) == total_items:
                    current_metrics, _ = aggregate_metrics(all_retrieval_metrics, all_answer_metrics)
                    await context.set_result({"current_metrics": current_metrics, "completed_items": index + 1, "total_items": total_items})
                    await self._persist_completed_items(context, run_id, index + 1)

            await context.set_progress(95, "计算最终指标")
            overall_metrics, overall_score = aggregate_metrics(all_retrieval_metrics, all_answer_metrics, include_overall_score=True)
            await context.raise_if_cancelled()
            await context.set_progress(100, "完成")
            return {
                "run_id": run_id,
                "completed_items": total_items,
                "metrics": overall_metrics,
                "overall_score": overall_score,
            }
        except (Exception, asyncio.CancelledError) as e:
            error = str(e)
            if isinstance(e, asyncio.CancelledError):
                if context.cancellation_reason == "lease_lost":
                    # 旧 attempt 已失权：不再写任何状态，原样上抛交由作业框架收敛
                    raise
                # 外部取消：清除取消计数后按原因归类，终态由 failure hook 收敛
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
                if context.is_cancel_requested():
                    error = "任务已取消"
                elif context.cancellation_reason == "timeout":
                    error = "任务执行超时"
                else:
                    error = "服务停止，任务执行中断"
            logger.error(f"BackgroundJob failed: {error}")
            await context.set_message("评估执行中断，请查看业务资源状态或日志")
            raise

    # ---------- 评估运行：查询与删除 ----------

    async def list_runs(self, kb_id: str) -> list[dict[str, Any]]:
        try:
            rows = await self.eval_repo.list_runs(kb_id)
            running_run_ids = {row.run_id for row in rows if row.status == "running"}
            job_by_run_id = {}
            if running_run_ids:
                jobs = await self.job_repo.list_by_payload_values(
                    job_type="rag_evaluation",
                    payload_key="run_id",
                    payload_values=running_run_ids,
                )
                # 查询按创建时间倒序，同一 run 保留最新一条作业做终态投影
                for job in jobs:
                    job_run_id = (job.payload or {}).get("run_id")
                    if job_run_id not in job_by_run_id:
                        job_by_run_id[job_run_id] = job

            runs = []
            for row in rows:
                job = job_by_run_id.get(row.run_id)
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
                if row.status == "running" and job is not None and job.status in {"pending", "running"}:
                    run.update(progress=job.progress, message=job.message)
                runs.append(run)
            return runs
        except Exception as e:
            logger.error(f"获取评估运行历史失败: {e}")
            raise

    async def get_run_results(
        self,
        kb_id: str,
        run_id: str,
        page: int = 1,
        page_size: int = 20,
        result_filter: str = "all",
    ) -> dict[str, Any]:
        """分页获取评估运行结果，并按结果类型筛选。"""
        if not re.match(r"^run_[a-f0-9]{8}$", run_id):
            raise ValueError("Invalid run_id format")
        row = await self.eval_repo.get_run(run_id)
        if row is None or row.kb_id != kb_id:
            raise ValueError(f"Run not found for {run_id}")

        start_idx = (page - 1) * page_size
        total = await self.eval_repo.count_run_items(run_id, result_filter=result_filter)
        details = await self.eval_repo.list_run_items(run_id, start_idx, page_size, result_filter=result_filter)
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
                "result_filter": result_filter,
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

    # ---------- 内部辅助：只读进度投影 ----------

    async def _dataset_build_metadata(self, row) -> dict[str, Any]:
        """读取作业进度作为响应装饰；业务状态与关联仅由执行事务维护。"""
        metadata = dict(row.build_metadata or {})
        if metadata.get("source") != "generated" or metadata.get("status") not in {"pending", "running"}:
            return metadata
        job_id = metadata.get("job_id")
        job = await self.job_repo.get_by_id(job_id) if job_id else None
        if job is not None and job.status in {"pending", "running"}:
            metadata.update(progress=job.progress, message=job.message)
        return metadata

    # ---------- 内部辅助：评估结果落库 ----------

    async def _persist_run_item(self, context: BackgroundJobContext, run_id: str, item_index: int, data: dict[str, Any]) -> None:
        """在 lease 保护事务内写入单题评估结果。"""

        async def operation(session, _job_record) -> None:
            await self.eval_repo.upsert_run_item_in_session(
                session,
                run_id=run_id,
                item_index=item_index,
                data=data,
            )

        await context.run_owned_transaction(operation)

    async def _persist_completed_items(self, context: BackgroundJobContext, run_id: str, completed_items: int) -> None:
        """在 lease 保护事务内更新已完成题数。"""

        async def operation(session, _job_record) -> None:
            record = await self.eval_repo.update_run_in_session(
                session,
                run_id,
                {"completed_items": completed_items},
            )
            if record is None:
                raise ValueError("EvaluationRun not found")

        await context.run_owned_transaction(operation)

    # ---------- 内部辅助：字段投影与默认命名 ----------

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
            "item_index": item.item_index,
            "query": item.query_text,
            "gold_chunk_ids": item.gold_chunk_ids,
            "gold_answer": item.gold_answer,
            "generated_answer": item.generated_answer,
            "retrieved_chunks": item.retrieved_chunks,
            "metrics": item.metrics or {},
        }

    def _normalize_run_name(self, name: str | None, run_id: str) -> str:
        """创建时归一运行名：空白名按 run_id 哈希生成默认名。"""
        run_name = (name or "").strip()
        if run_name:
            return run_name
        return build_evaluation_run_name(hash_value=run_id.removeprefix("run_"))

    def _run_name_from_row(self, row) -> str:
        """读取时回退默认运行名（按开始日期 + run_id 哈希）。"""
        name = (getattr(row, "name", None) or "").strip()
        if name:
            return name
        return build_evaluation_run_name(row.started_at, hash_value=row.run_id.removeprefix("run_"))

    # ---------- 内部辅助：条目构造与 JSONL 解析 ----------

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


async def run_dataset_generation_job(context: BackgroundJobContext):
    """从持久 payload 重建评估数据集生成 Handler。"""
    return await EvaluationService()._generate_dataset_job(context)


async def run_rag_evaluation_job(context: BackgroundJobContext):
    """从持久 payload 重建 RAG 评估 Handler。"""
    return await EvaluationService()._run_evaluation_job(context)
