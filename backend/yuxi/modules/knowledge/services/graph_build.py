"""图谱构建的提交准入和重置；worker 独立消费已经登记的任务。"""

from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.background_jobs.service import job_tracker
from yuxi.modules.knowledge.graphs.service import GRAPH_TASK_TYPE, KnowledgeGraphService
from yuxi.modules.knowledge.read_models import KnowledgeBaseDetail
from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.repositories.graphs import KnowledgeGraphRepository
from yuxi.modules.knowledge.services import bases as knowledge_bases
from yuxi.modules.knowledge.services.document_actions import submit_knowledge_job
from yuxi.shared.errors import InvalidOperationError, ResourceNotFoundError, StateConflictError


async def get_file_graph_build_status(kb_id: str, file_id: str) -> dict[str, int]:
    """读取文件当前可见代次中尚未完成图谱索引的分块数。"""
    file = await KnowledgeFileRepository().get_by_file_id(file_id)
    if file is None or file.kb_id != kb_id:
        raise ResourceNotFoundError("文件不存在或不属于该知识库")
    if file.is_folder:
        raise InvalidOperationError("目录不能构建图谱")
    pending = await KnowledgeChunkRepository().count_graph_pending_by_kb_id(kb_id, file_id=file_id)
    return {"pending_chunks": pending}


async def start_graph_build(kb_id: str, *, file_id: str | None = None) -> BackgroundJobRecord:
    """确认配置已经锁定后登记唯一构建任务。"""
    kb = await _require_available_knowledge_base(kb_id)
    status = await KnowledgeGraphService().get_status(kb_id)
    if not status.get("locked"):
        raise InvalidOperationError("请先确认并锁定图谱抽取配置")
    payload = {"kb_id": kb_id, "action": "build"}
    if file_id is not None:
        file = await KnowledgeFileRepository().get_by_file_id(file_id)
        if file is None or file.kb_id != kb_id:
            raise ResourceNotFoundError("文件不存在或不属于该知识库")
        if file.is_folder:
            raise InvalidOperationError("目录不能构建图谱")
        if not await KnowledgeChunkRepository().count_by_file_id(file_id):
            raise InvalidOperationError("文件当前没有可构建的分块")
        payload.update(file_id=file_id, generation=int(file.active_generation))
    return await _submit_graph_job(kb_id, name=f"图谱构建 ({kb.name})", payload=payload)


async def clear_file_graph(kb_id: str, file_id: str) -> BackgroundJobRecord:
    """为稳定文件登记图谱撤销任务，所有图谱动作共用知识库唯一准入。"""
    kb = await _require_available_knowledge_base(kb_id)
    if kb.kb_type.lower() != "milvus":
        raise InvalidOperationError("仅 Milvus 知识库支持清除图谱")
    file = await KnowledgeFileRepository().get_by_file_id(file_id)
    if file is None or file.kb_id != kb_id:
        raise ResourceNotFoundError("文件不存在或不属于该知识库")
    KnowledgeGraphRepository.require_clearable_file(file)
    return await _submit_graph_job(
        kb_id,
        name=f"清除文件图谱 ({file.filename})",
        payload={"kb_id": kb_id, "action": "clear_file", "file_id": file_id, "generation": int(file.active_generation)},
    )


async def reconcile_graph_build(kb_id: str, *, mode: str) -> BackgroundJobRecord:
    """校验修复范围并登记唯一修复任务，执行方才修改图谱事实。"""
    if mode not in {"failed", "all_vectors"}:
        raise InvalidOperationError("mode 必须是 failed 或 all_vectors")
    kb = await _require_available_knowledge_base(kb_id)
    return await _submit_graph_job(
        kb_id,
        name=f"图谱向量索引修复 ({kb.name})",
        payload={"kb_id": kb_id, "action": "reconcile", "reconcile_mode": mode},
    )


async def reset_graph_build(kb_id: str, *, clear_extraction_result: bool, clear_config: bool) -> dict:
    """活动任务拒绝重置，配置和存储重置继续由图谱执行器拥有。"""
    if await _has_active_job(kb_id):
        raise StateConflictError("该知识库存在正在运行的图谱构建任务，无法重置")
    return await KnowledgeGraphService().reset(kb_id, clear_extraction_result=clear_extraction_result, clear_config=clear_config)


async def _require_available_knowledge_base(kb_id: str) -> KnowledgeBaseDetail:
    """按活动任务、知识库存在的既有次序裁决提交准入。"""
    if await _has_active_job(kb_id):
        raise StateConflictError("该知识库已有正在运行的图谱构建任务")
    kb = await knowledge_bases.get_knowledge_base_info(kb_id)
    if kb is None:
        raise ResourceNotFoundError(f"知识库 {kb_id} 不存在")
    return kb


async def _submit_graph_job(kb_id: str, *, name: str, payload: dict) -> BackgroundJobRecord:
    """通过持久唯一登记处理预检后的并发竞争，提交后才投递。"""
    job, created = await submit_knowledge_job(name=name, job_type=GRAPH_TASK_TYPE, payload=payload, payload_match={"kb_id": kb_id})
    if not created:
        raise StateConflictError("该知识库已有正在运行的图谱构建任务")
    return job


async def _has_active_job(kb_id: str) -> bool:
    """读取本知识库的 pending/running 图谱任务。"""
    return (
        await job_tracker.find_job_by_payload(job_type=GRAPH_TASK_TYPE, payload_match={"kb_id": kb_id}, statuses={"pending", "running"})
        is not None
    )
