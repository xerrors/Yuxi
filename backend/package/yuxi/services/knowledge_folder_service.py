from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.task_service import TaskContext


class KnowledgeFolderService:
    """编排知识库历史虚拟目录迁移。"""

    def __init__(self, repository: KnowledgeFileRepository | None = None) -> None:
        self.repository = repository or KnowledgeFileRepository()

    async def detect_virtual_folder_data(self, kb_id: str) -> dict[str, int | bool]:
        """检测知识库是否仍有路径型历史记录。"""
        return await self.repository.detect_virtual_folder_data(kb_id)

    async def migrate_virtual_folder_data(
        self,
        context: TaskContext,
        *,
        kb_id: str,
        operator_id: str,
    ) -> dict:
        """按可续跑批次迁移全部历史路径记录。"""
        initial = await self.repository.detect_virtual_folder_data(kb_id)
        initial_steps = int(initial["remaining_steps"])
        processed_steps = 0
        created_folders = 0
        conflict_file_ids: set[str] = set()
        cursor: str | None = None
        pass_progress = 0

        while True:
            await context.raise_if_cancelled()
            batch: dict = {}

            async def migrate_batch(session, _task_record) -> None:
                batch.update(
                    await self.repository.migrate_virtual_folder_batch(
                        session,
                        kb_id=kb_id,
                        operator_id=operator_id,
                        after_file_id=cursor,
                    )
                )

            await context.run_owned_transaction(migrate_batch)
            if batch["scanned"] == 0:
                if pass_progress == 0:
                    break
                cursor = None
                pass_progress = 0
                continue

            cursor = batch["last_file_id"]
            processed = int(batch["processed"])
            processed_steps += processed
            pass_progress += processed
            created_folders += int(batch["created_folders"])
            conflict_file_ids.update(batch["conflict_file_ids"])
            progress = 100.0 if initial_steps == 0 else min(processed_steps / initial_steps * 100.0, 99.0)
            await context.set_progress(
                progress,
                f"已转换 {processed_steps}/{initial_steps} 个目录层级",
            )

        remaining = await self.repository.detect_virtual_folder_data(kb_id)
        result = {
            "processed_steps": processed_steps,
            "created_folders": created_folders,
            "conflict_files": len(conflict_file_ids),
            "remaining_files": int(remaining["file_count"]),
        }
        await context.set_result(result)
        message = (
            f"转换结束，仍有 {result['remaining_files']} 个冲突文件"
            if result["remaining_files"]
            else "历史虚拟文件夹转换完成"
        )
        await context.set_progress(100.0, message)
        return result


knowledge_folder_service = KnowledgeFolderService()
