"""真实目录创建、重命名和移动用例。"""

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.names import normalize_name
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.modules.knowledge.services import documents, statistics
from yuxi.shared.errors import InvalidOperationError, StateConflictError


async def create_folder(kb_id: str, folder_name: str, parent_id: str | None = None, operator_id: str | None = None) -> dict:
    """创建目录并刷新聚合统计，不装配检索连接。"""
    await documents.require_document_support(kb_id, "文件夹创建")
    return await statistics.run_with_stats_refresh(kb_id, _create_folder(kb_id, folder_name, parent_id, operator_id))


async def change_node(kb_id: str, file_id: str, *, name: str | None = None, parent_id: str | None = None, move: bool = False) -> dict:
    """在同一树事务内修改位置或目录名，保持节点身份。"""
    name = normalize_name(name) if name is not None else None
    repo = DirectoryRepository()
    async with pg_manager.get_async_session_context() as session:
        await repo.lock(session, kb_id)
        node = await repo.node(session, kb_id, file_id)
        target = parent_id if move else node.parent_id
        await repo.parent(session, kb_id, target)
        if move and node.is_folder:
            current = target
            while current is not None:
                if current == file_id:
                    raise InvalidOperationError("Cannot move a folder into itself or its own subfolder")
                current = (await repo.node(session, kb_id, current)).parent_id
        if name is not None and not node.is_folder:
            raise InvalidOperationError("Document is not a folder")
        final_name = name if name is not None else node.filename
        occupied = await repo.named(session, kb_id, target, final_name)
        if occupied is not None and occupied.file_id != file_id:
            raise StateConflictError(f"名称「{final_name}」已被占用", code="name_conflict")
        node.parent_id = target
        if name is not None:
            node.filename, node.path = name, name
        await repo.flush(session)
        result = documents.file_record_to_meta(node)
    return result


async def delete_folder(kb_id: str, folder_id: str) -> None:
    """原子删除真实子树，并为每个节点登记清理意图。"""
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    repo = DirectoryRepository()
    async with pg_manager.get_async_session_context() as session:
        await repo.lock(session, kb_id)
        folder = await repo.node(session, kb_id, folder_id)
        if not folder.is_folder:
            raise InvalidOperationError("目标不是文件夹")
        for node in await repo.subtree(session, kb_id, folder_id):
            await KnowledgeFileRepository().mark_deleted_in_session(session, node)


async def move_file(kb_id: str, file_id: str, new_parent_id: str | None) -> dict:
    """在目录事务中移动节点。"""
    await documents.require_document_support(kb_id, "文件移动")
    return await change_node(kb_id, file_id, parent_id=new_parent_id, move=True)


async def rename_folder(kb_id: str, folder_id: str, folder_name: str) -> dict:
    """在目录事务中重命名文件夹。"""
    await documents.require_document_support(kb_id, "文件夹重命名")
    return await change_node(kb_id, folder_id, name=folder_name)


async def _create_folder(kb_id: str, name: str, parent_id: str | None, operator_id: str | None) -> dict:
    """创建独立目录，名称占用时拒绝。"""
    name = normalize_name(name)
    repo = DirectoryRepository()
    async with pg_manager.get_async_session_context() as session:
        await repo.lock(session, kb_id)
        await repo.parent(session, kb_id, parent_id)
        folder = await repo.folder(session, kb_id, parent_id, name, operator_id, reuse=False)
        result = documents.file_record_to_meta(folder)
    return result
