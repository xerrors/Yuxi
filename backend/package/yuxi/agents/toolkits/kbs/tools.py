"""知识库工具模块"""

import inspect
from typing import Any

from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi import knowledge_base
from yuxi.utils import logger

# ========== 通用知识库工具函数 ==========


class ListKBsInput(BaseModel):
    """列出用户可访问的知识库输入模型"""

    # Langchain 的 runtime 注入机制要求必须有参数
    dummy: str = Field(default="", description="Dummy parameter - ignore")  # Add this


@tool(args_schema=ListKBsInput)
async def list_kbs(dummy: str, runtime: ToolRuntime) -> str:  # Now has 2 params
    """列出当前用户可访问的知识库列表

    返回用户基于权限可访问的知识库名称列表。这个列表是根据用户的角色和部门信息过滤后的结果，
    但不包括用户在当前对话中未启用的知识库。

    Returns:
        用户可访问的知识库名称列表（字符串格式）
    """
    # 从 runtime.context 获取用户信息
    runtime_context = runtime.context
    user_id = getattr(runtime_context, "user_id", None)
    if not user_id:
        return "无法获取用户信息"

    # 打印 runtime—context 中的所有信息以进行调试
    logger.debug(f"Runtime context: {runtime_context.__dict__}")

    enabled_kb_names = getattr(runtime_context, "knowledges", None)

    # 获取用户可访问的知识库列表（包含名称和描述）
    try:
        result = await knowledge_base.get_databases_by_raw_id(user_id)
        all_kbs = result.get("databases", [])
    except Exception as e:
        logger.error(f"获取用户知识库列表失败: {e}")
        return f"获取知识库列表失败: {str(e)}"

    all_kb_names = [kb["name"] for kb in all_kbs]

    logger.debug(f"用户 {user_id} 可访问的知识库列表: {all_kb_names}")
    logger.debug(f"用户 {user_id} 当前对话启用的知识库列表: {enabled_kb_names}")

    if enabled_kb_names is None:
        available_kbs = all_kbs
    else:
        available_kbs = [kb for kb in all_kbs if kb["name"] in enabled_kb_names]

    if not available_kbs:
        return "当前没有可访问的知识库"

    # 格式化输出（包含名称和描述）
    kb_list = []
    for kb in available_kbs:
        name = kb.get("name", "")
        desc = kb.get("description") or "无描述"
        kb_list.append({"name": name, "description": desc})

    return kb_list


class GetMindmapInput(BaseModel):
    """获取思维导图输入模型"""

    kb_name: str = Field(description="知识库名称，用于指定要获取思维导图的知识库")


@tool(args_schema=GetMindmapInput)
async def get_mindmap(kb_name: str, runtime: ToolRuntime) -> str:
    """获取指定知识库的思维导图结构

    当用户想要了解知识库的整体结构、文件分类、知识架构时使用此工具。
    返回知识库的思维导图层级结构。

    Args:
        kb_name: 知识库名称

    Returns:
        知识库的思维导图结构（文本格式）
    """
    if not kb_name:
        return "请提供知识库名称"

    # 获取所有检索器
    retrievers = knowledge_base.get_retrievers()

    # 查找对应的知识库
    target_db_id = None
    target_info = None
    for db_id, info in retrievers.items():
        if info["name"] == kb_name:
            target_db_id = db_id
            target_info = info
            break

    if not target_db_id:
        return f"知识库 '{kb_name}' 不存在"

    try:
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_id(target_db_id)

        if kb is None:
            return f"知识库 {target_info['name']} 不存在"

        mindmap_data = kb.mindmap

        if not mindmap_data:
            return f"知识库 {target_info['name']} 还没有生成思维导图。"

        # 将思维导图数据转换为文本格式
        def mindmap_to_text(node, level=0):
            """递归将思维导图JSON转换为层级文本"""
            indent = "  " * level
            text = f"{indent}- {node.get('content', '')}\n"
            for child in node.get("children", []):
                text += mindmap_to_text(child, level + 1)
            return text

        mindmap_text = f"知识库 {target_info['name']} 的思维导图结构：\n\n"
        mindmap_text += mindmap_to_text(mindmap_data)

        return mindmap_text

    except Exception as e:
        logger.error(f"获取思维导图失败: {e}")
        return f"获取思维导图失败: {str(e)}"


class QueryKBInput(BaseModel):
    """知识库检索输入模型"""

    kb_name: str = Field(description="知识库名称，用于指定要在哪个知识库中检索")
    query_text: str = Field(
        description=(
            "查询的关键词，查询的时候，应该尽量以可能帮助回答这个问题的关键词进行查询，"
            "不要直接使用用户的原始输入去查询。"
        )
    )
    file_name: str | None = Field(
        default=None,
        description=(
            "（非必要不启用此参数，留空即可）当已经读取思维导图之后，可以指定文件关键词，支持模糊匹配。\n"
            "仅当检索结果过多且不相关，需要进一步缩小范围时使用。"
        ),
    )


async def _resolve_visible_knowledge_bases_for_query(runtime: ToolRuntime | None) -> list[dict[str, Any]]:
    if runtime is None:
        return []

    context = getattr(runtime, "context", None)
    if context is None:
        return []

    visible_kbs = getattr(context, "_visible_knowledge_bases", None)
    if isinstance(visible_kbs, list):
        return visible_kbs

    try:
        from yuxi.agents.backends.knowledge_base_backend import resolve_visible_knowledge_bases_for_context

        return await resolve_visible_knowledge_bases_for_context(context)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"解析会话可见知识库失败，跳过 filepath 注入: {exc}")
        return []


def _find_query_target(
    *,
    kb_name: str,
    retrievers: dict[str, Any],
    visible_kbs: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    if visible_kbs:
        matched_kbs = [db for db in visible_kbs if str(db.get("name") or "").strip() == kb_name]
        if not matched_kbs:
            return None, None, f"知识库 '{kb_name}' 不存在或当前会话未启用"
        if len(matched_kbs) > 1:
            return None, None, f"知识库 '{kb_name}' 存在重名，请先调整名称后重试"

        target_db_id = str(matched_kbs[0].get("db_id") or "")
        target_info = retrievers.get(target_db_id)
        if target_info is None:
            return None, None, f"知识库 '{kb_name}' 不存在"
        return target_db_id, target_info, None

    for db_id, info in retrievers.items():
        if info["name"] == kb_name:
            return str(db_id), info, None

    return None, None, f"知识库 '{kb_name}' 不存在"


@tool(args_schema=QueryKBInput)
async def query_kb(kb_name: str, query_text: str, file_name: str | None = None, runtime: ToolRuntime = None) -> Any:
    """在指定知识库中检索内容

    当用户需要查询具体内容时使用此工具。根据关键词在知识库中检索相关文档片段。

    Args:
        kb_name: 知识库名称
        query_text: 查询的关键词
        file_name: （可选）文件名称过滤

    Returns:
        检索结果
    """
    if not kb_name:
        return "请提供知识库名称"
    if not query_text:
        return "请提供查询内容"

    # 获取所有检索器
    retrievers = knowledge_base.get_retrievers()

    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)

    target_db_id, target_info, target_error = _find_query_target(
        kb_name=kb_name,
        retrievers=retrievers,
        visible_kbs=visible_kbs,
    )
    if target_error:
        return target_error

    metadata = target_info.get("metadata") if isinstance(target_info, dict) else None
    kb_type = str((metadata or {}).get("kb_type") or "").strip().lower()

    try:
        retriever = target_info["retriever"]
        kwargs = {}
        if file_name:
            kwargs["file_name"] = file_name

        if inspect.iscoroutinefunction(retriever):
            result = await retriever(query_text, **kwargs)
        else:
            result = retriever(query_text, **kwargs)

        if kb_type != "milvus":
            return result

        if not isinstance(result, list):
            return f"知识库 '{kb_name}' 返回结果不是 Milvus chunks 列表，无法注入文件路径"

        from yuxi.agents.backends.knowledge_base_backend import inject_filepaths_into_retrieval_result

        # 只有 Milvus 结果的 file_id 对应本地文件系统，可补充沙盒可读路径。
        return await inject_filepaths_into_retrieval_result(
            retrieval_chunks=result,
            visible_kbs=visible_kbs,
            target_db_id=target_db_id,
            target_kb_name=kb_name,
        )

    except Exception as e:
        logger.error(f"检索失败: {e}")
        return f"检索失败: {str(e)}"


def get_common_kb_tools() -> list:
    """获取通用知识库工具列表

    返回 3 个通用工具：
    - list_kbs: 列出用户可访问的知识库
    - get_mindmap: 获取指定知识库的思维导图
    - query_kb: 在指定知识库中检索
    """
    return [list_kbs, get_mindmap, query_kb]
