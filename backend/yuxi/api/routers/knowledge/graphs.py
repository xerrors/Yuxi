"""Milvus 知识库图谱查询路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from yuxi.api.dependencies.auth import get_admin_user
from yuxi.api.dependencies.knowledge import require_knowledge_base_read
from yuxi.api.responses.knowledge import serialize_knowledge_base
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.modules.knowledge.runtime import knowledge_base
from yuxi.shared.errors import DomainError

graph = APIRouter(prefix="/graph", tags=["graph"])


@graph.get("/list")
async def get_graphs(current_user: User = Depends(get_admin_user)):
    """获取支持图谱能力的 Milvus 知识库列表"""
    try:
        knowledge_bases = await knowledge_base.get_knowledge_bases_by_uid(current_user.uid)
        graphs = []
        for kb in knowledge_bases:
            if kb.kb_type.lower() != "milvus":
                continue
            serialized = serialize_knowledge_base(kb)
            graphs.append(
                {
                    "id": kb.kb_id,
                    "name": kb.name,
                    "type": "milvus",
                    "description": kb.description,
                    "status": "已连接",
                    "created_at": serialized["created_at"],
                    "metadata": serialized,
                }
            )
        return {"success": True, "data": graphs}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to list graphs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list graphs: {str(e)}")


@graph.get("/subgraph")
async def get_subgraph(
    kb_id: str = Query(..., description="Milvus 知识库ID"),
    node_label: str = Query("*", description="节点标签或查询关键词"),
    max_depth: int = Query(2, description="最大深度", ge=1, le=5),
    max_nodes: int = Query(100, description="最大节点数", ge=1, le=1000),
    exclude_chunk: bool = Query(False, description="是否排除 Chunk 节点"),
    file_id: str | None = Query(None, min_length=1, description="限定单个文件"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """查询 Milvus 知识库图谱子图"""
    try:
        logger.info("Querying subgraph kb_id={}", kb_id)
        service = await _get_graph_service(kb_id)
        result_data = await service.query_nodes(
            keyword=node_label,
            max_depth=max_depth,
            max_nodes=max_nodes,
            exclude_chunk=exclude_chunk,
            file_id=file_id,
        )
        return {"success": True, "data": result_data}
    except (HTTPException, DomainError):
        raise
    except Exception as e:
        logger.exception(f"Failed to get subgraph: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get subgraph: {str(e)}")


@graph.get("/labels")
async def get_graph_labels(
    kb_id: str = Query(..., description="Milvus 知识库ID"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """获取 Milvus 知识库图谱的所有标签"""
    try:
        service = await _get_graph_service(kb_id)
        labels = await service.get_labels()
        return {"success": True, "data": {"labels": labels}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get labels: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get labels: {str(e)}")


@graph.get("/stats")
async def get_graph_stats(
    kb_id: str = Query(..., description="Milvus 知识库ID"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """获取 Milvus 知识库图谱统计信息"""
    try:
        service = await _get_graph_service(kb_id)
        stats_data = await service.get_stats()
        return {"success": True, "data": stats_data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}") from e


async def _get_graph_service(kb_id: str) -> KnowledgeGraphService:
    """加载 Milvus 知识库的图谱服务；类型不符按不存在处理。"""
    kb_info = await knowledge_base.get_knowledge_base_info(kb_id)
    if not kb_info:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    kb_type = kb_info.kb_type.lower()
    if kb_type != "milvus":
        raise HTTPException(status_code=404, detail="Graph API only supports Milvus knowledge bases")

    return KnowledgeGraphService(kb_id=kb_id)
