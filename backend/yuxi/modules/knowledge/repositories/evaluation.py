from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, and_, delete, func, or_, select

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.models import EvaluationDataset, EvaluationDatasetItem, EvaluationRun, EvaluationRunItem
from yuxi.shared.datetime import utc_now

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from yuxi.modules.background_jobs.models import BackgroundJobRecord


class EvaluationRepository:
    """评估数据集与运行记录的 PG 持久化仓储。

    *_in_session 方法运行在调用方（service / BackgroundJob 事务）拥有的会话内，
    其余方法自开短事务；更新前用 with_for_update 取行锁串行化并发终态写入。
    """

    # ─── 数据集写入 ───────────────────────────────────────────────

    @staticmethod
    async def create_dataset_in_session(session: AsyncSession, dataset_data: dict[str, Any]) -> EvaluationDataset:
        """在调用方事务中创建数据集。"""
        dataset = EvaluationDataset(**dataset_data)
        session.add(dataset)
        await session.flush()
        return dataset

    async def create_dataset_with_items(self, dataset_data: dict[str, Any], items_data: list[dict[str, Any]]) -> EvaluationDataset:
        """同一事务内写入数据集与全部题目，保证二者原子可见。"""
        async with pg_manager.get_async_session_context() as session:
            dataset = await self.create_dataset_in_session(session, dataset_data)
            await self.add_dataset_items_in_session(session, items_data)
            return dataset

    @staticmethod
    async def add_dataset_items_in_session(session: AsyncSession, items_data: list[dict[str, Any]]) -> None:
        """在调用方事务中批量写入数据集题目。"""
        session.add_all(EvaluationDatasetItem(**item) for item in items_data)
        await session.flush()

    @staticmethod
    async def attach_dataset_generation_job_in_session(
        session: AsyncSession,
        dataset_id: str,
        job_id: str,
    ) -> EvaluationDataset | None:
        """在调用方事务中关联生成作业，且不覆盖相同作业的终态。"""
        record = await session.scalar(select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id).with_for_update())
        if record is None:
            return None
        metadata = dict(record.build_metadata or {})
        if metadata.get("source") != "generated" or metadata.get("status") == "completed":
            return record
        if metadata.get("job_id") == job_id:
            return record
        metadata.update(
            job_id=job_id,
            status="pending",
            message="等待 worker 执行",
        )
        metadata.pop("error_message", None)
        record.build_metadata = metadata
        await session.flush()
        return record

    @staticmethod
    async def update_dataset_in_session(session: AsyncSession, dataset_id: str, data: dict[str, Any]) -> EvaluationDataset | None:
        """行锁内更新数据集字段；记录不存在时返回 None。"""
        record = await session.scalar(select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id).with_for_update())
        if record is None:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        await session.flush()
        return record

    async def update_dataset(self, dataset_id: str, data: dict[str, Any]) -> EvaluationDataset | None:
        """独立事务内更新数据集字段。"""
        async with pg_manager.get_async_session_context() as session:
            return await self.update_dataset_in_session(session, dataset_id, data)

    # ─── 数据集查询 ───────────────────────────────────────────────

    async def get_dataset(self, dataset_id: str) -> EvaluationDataset | None:
        """按 dataset_id 读取数据集。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id))
            return result.scalar_one_or_none()

    async def list_datasets(self, kb_id: str) -> list[EvaluationDataset]:
        """列出知识库的数据集，按创建时间倒序。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationDataset).where(EvaluationDataset.kb_id == kb_id).order_by(EvaluationDataset.created_at.desc())
            )
            return list(result.scalars().all())

    async def list_dataset_items(self, dataset_id: str, offset: int = 0, limit: int = 100) -> list[EvaluationDatasetItem]:
        """按 item_index 升序分页读取数据集题目，分页顺序是稳定契约。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationDatasetItem)
                .where(EvaluationDatasetItem.dataset_id == dataset_id)
                .order_by(EvaluationDatasetItem.item_index.asc())
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def count_dataset_items(self, dataset_id: str) -> int:
        """统计数据集题目总数，与分页列表同表同过滤口径。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count(EvaluationDatasetItem.id)).where(EvaluationDatasetItem.dataset_id == dataset_id)
            )
            return int(result.scalar() or 0)

    async def list_all_dataset_items(self, dataset_id: str) -> list[EvaluationDatasetItem]:
        """按 item_index 升序读取全部题目，供评估执行与 JSONL 导出复用。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationDatasetItem)
                .where(EvaluationDatasetItem.dataset_id == dataset_id)
                .order_by(EvaluationDatasetItem.item_index.asc())
            )
            return list(result.scalars().all())

    # ─── 评估运行写入 ─────────────────────────────────────────────

    @staticmethod
    async def create_run_in_session(session: AsyncSession, data: dict[str, Any]) -> EvaluationRun:
        """在调用方事务中创建评估 Run。"""
        run = EvaluationRun(**data)
        session.add(run)
        await session.flush()
        return run

    @staticmethod
    async def update_run_in_session(session: AsyncSession, run_id: str, data: dict[str, Any]) -> EvaluationRun | None:
        """行锁内更新评估 Run 字段；记录不存在时返回 None。"""
        record = await session.scalar(select(EvaluationRun).where(EvaluationRun.run_id == run_id).with_for_update())
        if record is None:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        await session.flush()
        return record

    async def update_run(self, run_id: str, data: dict[str, Any]) -> EvaluationRun | None:
        """独立事务内更新评估 Run 字段。"""
        async with pg_manager.get_async_session_context() as session:
            return await self.update_run_in_session(session, run_id, data)

    # ─── 评估运行查询 ─────────────────────────────────────────────

    async def get_run(self, run_id: str) -> EvaluationRun | None:
        """按 run_id 读取评估 Run。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run_id))
            return result.scalar_one_or_none()

    async def list_runs(self, kb_id: str) -> list[EvaluationRun]:
        """列出知识库的评估运行，按开始时间倒序。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationRun).where(EvaluationRun.kb_id == kb_id).order_by(EvaluationRun.started_at.desc())
            )
            return list(result.scalars().all())

    # ─── 运行结果：逐题写入与筛选读取 ─────────────────────────────

    @staticmethod
    async def upsert_run_item_in_session(
        session: AsyncSession,
        run_id: str,
        item_index: int,
        data: dict[str, Any],
    ) -> EvaluationRunItem:
        """绑定父运行的数据集；不能把其他数据集的题目写入当前结果。"""
        # MATCH FULL 复合外键要求 dataset_item_id 与 dataset_id 同时有值或同时为空：
        # 关联原题时从父 Run 继承 dataset_id，显式清空原题引用时同步清空 dataset_id。
        data = dict(data)
        if data.get("dataset_item_id") is not None:
            run = await session.scalar(select(EvaluationRun).where(EvaluationRun.run_id == run_id).with_for_update())
            if run is None or run.dataset_id is None:
                raise ValueError("评估运行没有可关联的数据集")
            data["dataset_id"] = run.dataset_id
        elif "dataset_item_id" in data:
            data["dataset_id"] = None
        record = await session.scalar(
            select(EvaluationRunItem)
            .where(EvaluationRunItem.run_id == run_id, EvaluationRunItem.item_index == item_index)
            .with_for_update()
        )
        if record is None:
            record = EvaluationRunItem(run_id=run_id, item_index=item_index, **data)
            session.add(record)
        else:
            for key, value in data.items():
                setattr(record, key, value)
        await session.flush()
        return record

    async def list_run_items(
        self, run_id: str, offset: int = 0, limit: int = 100, *, result_filter: str = "all"
    ) -> list[EvaluationRunItem]:
        """按 item_index 升序分页读取逐题结果，结果筛选在 SQL 内执行后再分页。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationRunItem)
                .where(EvaluationRunItem.run_id == run_id, *self._result_filters(result_filter))
                .order_by(EvaluationRunItem.item_index.asc())
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def count_run_items(self, run_id: str, *, result_filter: str = "all") -> int:
        """按与 list_run_items 相同的筛选口径统计逐题结果数。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count(EvaluationRunItem.id)).where(EvaluationRunItem.run_id == run_id, *self._result_filters(result_filter))
            )
            return int(result.scalar() or 0)

    @staticmethod
    def _result_filters(result_filter: str) -> list:
        """在 SQL 中筛选后分页，历史零命中题按标准片段与实际检索事实识别。"""
        if result_filter == "all":
            return []
        answer_error = func.coalesce(EvaluationRunItem.metrics["score"].as_float(), 1.0) <= 0.5
        if result_filter == "answer_errors":
            return [answer_error]
        missed_retrieval = and_(
            EvaluationRunItem.gold_chunk_ids.is_not(None),
            EvaluationRunItem.gold_chunk_ids != JSON.NULL,
            EvaluationRunItem.gold_chunk_ids != [],
            EvaluationRunItem.retrieved_chunks == [],
        )
        return [or_(answer_error, missed_retrieval, func.coalesce(EvaluationRunItem.metrics["recall@10"].as_float(), 1.0) < 1)]

    # ─── 删除 ─────────────────────────────────────────────────────

    async def delete_dataset(self, dataset_id: str) -> None:
        """删除数据集；题目由数据库外键级联清理。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id))
            record = result.scalar_one_or_none()
            if record is not None:
                await session.delete(record)

    @staticmethod
    async def delete_run_in_session(session: AsyncSession, kb_id: str, run_id: str) -> bool:
        """删除指定知识库的运行，逐题结果由外键级联清理。"""
        statement = delete(EvaluationRun).where(EvaluationRun.kb_id == kb_id, EvaluationRun.run_id == run_id)
        return await session.scalar(statement.returning(EvaluationRun.run_id)) is not None


# ─── BackgroundJob 终态钩子 ────────────────────────────────────────
# 四个钩子经 evaluation/service.py re-export 后由 background_jobs registry
# 按字符串路径加载：模块路径与函数名是注册契约，不可重命名。


async def finish_dataset_generation_job(session: AsyncSession, job_record: BackgroundJobRecord, result: Any) -> None:
    """在 BackgroundJob 成功事务内提交数据集完成事实。"""
    if not isinstance(result, dict) or result.get("dataset_id") != (job_record.payload or {}).get("dataset_id"):
        raise ValueError("Dataset generation result does not match BackgroundJob payload")
    dataset = await session.scalar(select(EvaluationDataset).where(EvaluationDataset.dataset_id == result["dataset_id"]).with_for_update())
    if dataset is None:
        raise ValueError("Dataset not found during BackgroundJob completion")
    dataset.item_count = int(result["item_count"])
    dataset.build_metadata = dict(result["build_metadata"])


async def fail_dataset_generation_job(session: AsyncSession, job_record: BackgroundJobRecord, error: str) -> None:
    """在 BackgroundJob 失败事务内收敛数据集构建状态。"""
    dataset_id = (job_record.payload or {}).get("dataset_id")
    dataset = await session.scalar(select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id).with_for_update())
    if dataset is None:
        return
    metadata = dict(dataset.build_metadata or {})
    if metadata.get("status") == "completed" or metadata.get("job_id") not in {None, job_record.id}:
        return
    metadata.update(
        status="failed",
        job_id=job_record.id,
        progress=100,
        error_message=error,
        message=error,
    )
    dataset.build_metadata = metadata


async def finish_rag_evaluation_job(session: AsyncSession, job_record: BackgroundJobRecord, result: Any) -> None:
    """在 BackgroundJob 成功事务内提交评估 Run 完成事实。"""
    if not isinstance(result, dict) or result.get("run_id") != (job_record.payload or {}).get("run_id"):
        raise ValueError("Evaluation result does not match BackgroundJob payload")
    run = await session.scalar(select(EvaluationRun).where(EvaluationRun.run_id == result["run_id"]).with_for_update())
    if run is None:
        raise ValueError("EvaluationRun not found during BackgroundJob completion")
    run.status = "completed"
    run.completed_items = int(result["completed_items"])
    run.metrics = dict(result["metrics"])
    run.overall_score = result["overall_score"]
    run.completed_at = utc_now()


async def fail_rag_evaluation_job(session: AsyncSession, job_record: BackgroundJobRecord, error: str) -> None:
    """在 BackgroundJob 失败事务内收敛评估 Run。"""
    run_id = (job_record.payload or {}).get("run_id")
    run = await session.scalar(select(EvaluationRun).where(EvaluationRun.run_id == run_id).with_for_update())
    if run is None or run.status == "completed":
        return
    run.status = "failed"
    run.metrics = {"error": error}
    run.completed_at = utc_now()
