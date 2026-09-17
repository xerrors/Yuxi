"""知识域 Dashboard HTTP 路由。"""

import traceback

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server.utils.auth_middleware import get_superadmin_user
from yuxi.services.knowledge_dashboard_service import get_knowledge_stats
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger


knowledge_dashboard = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class KnowledgeStats(BaseModel):
    """知识库统计。"""

    total_databases: int
    total_files: int
    total_nodes: int
    total_storage_size: int
    databases_by_type: dict
    file_type_distribution: dict


@knowledge_dashboard.get("/stats/knowledge", response_model=KnowledgeStats)
async def read_knowledge_stats(
    current_user: User = Depends(get_superadmin_user),
):
    """获取知识库统计（超级管理员权限）。"""

    try:
        return KnowledgeStats(**await get_knowledge_stats())
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error getting knowledge stats: {exc}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to get knowledge stats: {str(exc)}") from exc
