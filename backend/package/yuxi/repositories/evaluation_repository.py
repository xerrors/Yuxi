from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    EvaluationDataset,
    EvaluationDatasetItem,
    EvaluationRun,
    EvaluationRunItem,
)


class EvaluationRepository:
    @staticmethod
    async def create_dataset_in_session(session, dataset_data: dict[str, Any]) -> EvaluationDataset:
        """在 service 拥有的事务中创建数据集。"""
        dataset = EvaluationDataset(**dataset_data)
        session.add(dataset)
        await session.flush()
        return dataset

    async def create_dataset(self, dataset_data: dict[str, Any]) -> EvaluationDataset:
        async with pg_manager.get_async_session_context() as session:
            return await self.create_dataset_in_session(session, dataset_data)

    async def create_dataset_with_items(
        self, dataset_data: dict[str, Any], items_data: list[dict[str, Any]]
    ) -> EvaluationDataset:
        async with pg_manager.get_async_session_context() as session:
            dataset = await self.create_dataset_in_session(session, dataset_data)
            await self.add_dataset_items_in_session(session, items_data)
            return dataset

    @staticmethod
    async def attach_dataset_generation_task_in_session(
        session,
        dataset_id: str,
        task_id: str,
    ) -> EvaluationDataset | None:
        """在调用方事务中关联生成 Task，且不覆盖相同 Task 的终态。"""
        record = await session.scalar(
            select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id).with_for_update()
        )
        if record is None:
            return None
        metadata = dict(record.build_metadata or {})
        if metadata.get("source") != "generated" or metadata.get("status") == "completed":
            return record
        if metadata.get("task_id") == task_id:
            return record
        metadata.update(
            task_id=task_id,
            status="pending",
            message="等待 worker 执行",
        )
        metadata.pop("error_message", None)
        record.build_metadata = metadata
        await session.flush()
        return record

    @staticmethod
    async def update_dataset_in_session(session, dataset_id: str, data: dict[str, Any]) -> EvaluationDataset | None:
        record = await session.scalar(
            select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id).with_for_update()
        )
        if record is None:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        await session.flush()
        return record

    async def update_dataset(self, dataset_id: str, data: dict[str, Any]) -> EvaluationDataset | None:
        async with pg_manager.get_async_session_context() as session:
            return await self.update_dataset_in_session(session, dataset_id, data)

    @staticmethod
    async def add_dataset_items_in_session(session, items_data: list[dict[str, Any]]) -> None:
        session.add_all(EvaluationDatasetItem(**item) for item in items_data)
        await session.flush()

    async def get_dataset(self, dataset_id: str) -> EvaluationDataset | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id))
            return result.scalar_one_or_none()

    async def list_datasets(self, kb_id: str) -> list[EvaluationDataset]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationDataset)
                .where(EvaluationDataset.kb_id == kb_id)
                .order_by(EvaluationDataset.created_at.desc())
            )
            return list(result.scalars().all())

    async def list_dataset_items(
        self, dataset_id: str, offset: int = 0, limit: int = 100
    ) -> list[EvaluationDatasetItem]:
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
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count(EvaluationDatasetItem.id)).where(EvaluationDatasetItem.dataset_id == dataset_id)
            )
            return int(result.scalar() or 0)

    async def list_all_dataset_items(self, dataset_id: str) -> list[EvaluationDatasetItem]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationDatasetItem)
                .where(EvaluationDatasetItem.dataset_id == dataset_id)
                .order_by(EvaluationDatasetItem.item_index.asc())
            )
            return list(result.scalars().all())

    async def delete_dataset(self, dataset_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(EvaluationDataset).where(EvaluationDataset.dataset_id == dataset_id))
            record = result.scalar_one_or_none()
            if record is not None:
                await session.delete(record)

    @staticmethod
    async def create_run_in_session(session, data: dict[str, Any]) -> EvaluationRun:
        """在 service 拥有的事务中创建评估 Run。"""
        run = EvaluationRun(**data)
        session.add(run)
        await session.flush()
        return run

    async def get_run(self, run_id: str) -> EvaluationRun | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run_id))
            return result.scalar_one_or_none()

    async def list_runs(self, kb_id: str) -> list[EvaluationRun]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationRun).where(EvaluationRun.kb_id == kb_id).order_by(EvaluationRun.started_at.desc())
            )
            return list(result.scalars().all())

    @staticmethod
    async def update_run_in_session(session, run_id: str, data: dict[str, Any]) -> EvaluationRun | None:
        record = await session.scalar(select(EvaluationRun).where(EvaluationRun.run_id == run_id).with_for_update())
        if record is None:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        await session.flush()
        return record

    async def update_run(self, run_id: str, data: dict[str, Any]) -> EvaluationRun | None:
        async with pg_manager.get_async_session_context() as session:
            return await self.update_run_in_session(session, run_id, data)

    async def delete_run(self, run_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(EvaluationRunItem).where(EvaluationRunItem.run_id == run_id))
            result = await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run_id))
            record = result.scalar_one_or_none()
            if record is not None:
                await session.delete(record)

    @staticmethod
    async def upsert_run_item_in_session(
        session,
        run_id: str,
        item_index: int,
        data: dict[str, Any],
    ) -> EvaluationRunItem:
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

    async def list_run_items(self, run_id: str, offset: int = 0, limit: int = 100) -> list[EvaluationRunItem]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EvaluationRunItem)
                .where(EvaluationRunItem.run_id == run_id)
                .order_by(EvaluationRunItem.item_index.asc())
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def count_run_items(self, run_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count(EvaluationRunItem.id)).where(EvaluationRunItem.run_id == run_id)
            )
            return int(result.scalar() or 0)

    async def delete_all(self) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(EvaluationRunItem))
            await session.execute(delete(EvaluationRun))
            await session.execute(delete(EvaluationDatasetItem))
            await session.execute(delete(EvaluationDataset))
