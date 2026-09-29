"""知识库工具模块"""

import asyncio
import os
import tempfile
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend
from yuxi.agents.toolkits.registry import tool
from yuxi.knowledge.schemas import (
    FindInputSchema,
    OpenInputSchema,
    SearchInputSchema,
)
from yuxi.services.knowledge import tools as knowledge_tools
from yuxi.utils import logger

# ========== 通用知识库工具 ==========


def get_common_kb_tools() -> list:
    """获取通用知识库工具列表

    返回 7 个通用工具：
    - list_kbs: 列出用户可访问的知识库
    - get_mindmap: 获取指定知识库的思维导图
    - query_kb: 在指定知识库中检索
    - find_kb_document: 在指定文件内定位关键词或正则模式
    - open_kb_document: 按 file_id 分段打开知识库文档
    - search_file: 搜索知识库中的文件
    - download_kb_file: 按 file_id 下载知识库原始文件到沙盒 outputs
    """
    return [
        list_kbs,
        get_mindmap,
        query_kb,
        find_kb_document,
        open_kb_document,
        search_file,
        download_kb_file,
    ]


class ListKBsInput(BaseModel):
    """列出用户可访问的知识库输入模型"""

    # Langchain 的 runtime 注入机制要求必须有参数
    dummy: str = Field(default="", description="Dummy parameter - ignore")


@tool(category="knowledge", tags=["知识库"], display_name="列出知识库", args_schema=ListKBsInput)
async def list_kbs(dummy: str, runtime: ToolRuntime) -> str:
    """列出当前用户可访问的知识库列表

    返回用户基于权限可访问的知识库名称列表。这个列表是根据用户的角色和部门信息过滤后的结果，
    但不包括用户在当前对话中未启用的知识库。

    Returns:
        用户可访问的知识库名称列表（字符串格式）
    """
    runtime_context = runtime.context
    uid = getattr(runtime_context, "uid", None)
    if not uid:
        return "无法获取用户信息"

    available_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    if not available_kbs:
        return "当前没有可访问的知识库"

    return knowledge_tools.list_kbs(available_kbs)


class GetMindmapInput(BaseModel):
    """获取思维导图输入模型"""

    kb_name: str = Field(description="知识库名称，用于指定要获取思维导图的知识库")


@tool(category="knowledge", tags=["知识库"], display_name="获取思维导图", args_schema=GetMindmapInput)
async def get_mindmap(kb_name: str, runtime: ToolRuntime) -> str:
    """获取指定知识库的思维导图结构

    当用户想要了解知识库的整体结构、文件分类、知识架构时使用此工具。
    返回知识库的思维导图层级结构。

    Args:
        kb_name: 知识库名称

    Returns:
        知识库的思维导图结构（文本格式）
    """
    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    try:
        return await knowledge_tools.get_mindmap(kb_name, visible_kbs)
    except knowledge_tools.KnowledgeToolError as e:
        return str(e)
    except Exception as e:
        logger.error(f"获取思维导图失败: {e}")
        return f"获取思维导图失败: {str(e)}"


QueryKBInput = SearchInputSchema


@tool(category="knowledge", tags=["知识库"], display_name="检索知识库", args_schema=QueryKBInput)
async def query_kb(kb_id: str, query_text: str, file_name: str | None = None, runtime: ToolRuntime = None) -> Any:
    """在指定知识库中检索内容

    当用户需要查询具体内容时使用此工具。kb_id 是知识库资源 ID，也就是 kb_id；返回结果中的
    file_id 可继续用于 find_kb_document 或 open_kb_document。
    """
    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    try:
        return await knowledge_tools.query_kb(
            kb_id,
            query_text,
            visible_kbs,
            file_name=file_name,
            kb_service=_get_knowledge_base(),
        )
    except knowledge_tools.KnowledgeToolError as e:
        return str(e)
    except Exception as e:
        logger.error(f"检索失败: {e}")
        return f"检索失败: {str(e)}"


OpenKBDocumentInput = OpenInputSchema


@tool(category="knowledge", tags=["知识库"], display_name="打开知识库文档", args_schema=OpenKBDocumentInput)
async def open_kb_document(
    kb_id: str,
    file_id: str,
    line: int | None = None,
    offset: int | None = None,
    window_size: int = 1800,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """按行窗口打开知识库文档原文

    当 query_kb 返回的片段不足以回答问题，或需要查看某个文档的上下文时使用。
    kb_id 是知识库资源 ID，也就是 kb_id；file_id 是知识库文件 ID。
    """
    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    try:
        return await knowledge_tools.open_kb_document(
            kb_id,
            file_id,
            visible_kbs,
            line=line,
            offset=offset,
            window_size=window_size,
            kb_service=_get_knowledge_base(),
        )
    except knowledge_tools.KnowledgeToolError as e:
        return str(e)
    except Exception as e:
        logger.error(f"打开知识库文档失败: {e}")
        return f"打开知识库文档失败: {str(e)}"


FindKBDocumentInput = FindInputSchema


@tool(category="knowledge", tags=["知识库"], display_name="定位文档内容", args_schema=FindKBDocumentInput)
async def find_kb_document(
    kb_id: str,
    file_id: str,
    patterns: list[str],
    use_regex: bool = False,
    case_sensitive: bool = False,
    max_windows: int = 5,
    window_size: int = 80,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """在已知知识库文件内做关键词或正则定位。

    当 query_kb 已找到候选文件，但需要在该文件内定位术语、指标、章节或实体时使用。
    """
    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    try:
        return await knowledge_tools.find_kb_document(
            kb_id,
            file_id,
            patterns,
            visible_kbs,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_windows=max_windows,
            window_size=window_size,
            kb_service=_get_knowledge_base(),
        )
    except knowledge_tools.KnowledgeToolError as e:
        return str(e)
    except Exception as e:
        logger.error(f"知识库文档内检索失败: {e}")
        return f"知识库文档内检索失败: {str(e)}"


class SearchFileInput(BaseModel):
    """搜索文件输入模型"""

    kb_name: str | None = Field(default=None, description="知识库名称，为空时搜索所有知识库")
    query: str | None = Field(default=None, description="搜索关键词，为空时返回所有文件")
    offset: int = Field(default=0, ge=0, description="偏移量，从 0 开始")
    limit: int = Field(default=300, ge=1, le=5000, description="返回数量限制，默认 300")


@tool(category="knowledge", tags=["知识库"], display_name="搜索知识库文件", args_schema=SearchFileInput)
async def search_file(
    kb_name: str | None = None,
    query: str | None = None,
    offset: int = 0,
    limit: int = 300,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """搜索知识库中的文件

    当用户需要查找特定文件时使用此工具。可以指定知识库名称和搜索关键词。
    如果不指定知识库，将搜索所有可访问的知识库。
    如果不指定搜索关键词，将返回所有文件。

    Args:
        kb_name: 知识库名称，为空时搜索所有知识库
        query: 搜索关键词，为空时返回所有文件
        offset: 偏移量，从 0 开始
        limit: 返回数量限制，默认 300

    Returns:
        匹配的文件列表和分页信息
    """
    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    try:
        return await knowledge_tools.search_file(
            visible_kbs,
            kb_name=kb_name,
            query=query,
            offset=offset,
            limit=limit,
            kb_service=_get_knowledge_base(),
        )
    except knowledge_tools.KnowledgeToolError as e:
        return str(e)


class DownloadKBFileInput(BaseModel):
    """下载知识库原始文件输入模型"""

    kb_id: str = Field(description="知识库资源 ID")
    file_id: str = Field(description="知识库文件 ID，来自 query_kb 或 search_file 的返回结果")
    save_as: str | None = Field(
        default=None,
        description="落盘文件名；为空时使用原始文件名。仅取文件名部分，不可包含目录",
    )


@tool(category="knowledge", tags=["知识库"], display_name="下载知识库文件", args_schema=DownloadKBFileInput)
async def download_kb_file(
    kb_id: str,
    file_id: str,
    save_as: str | None = None,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """下载知识库文件的原始二进制（pdf/docx/xlsx 等）到沙盒 outputs 目录。

    当后续需要对原始文件结构做处理时使用：例如用 openpyxl/pandas 读取 xlsx 单元格、
    用 pdfplumber/python-docx 重新解析版面。query_kb/open_kb_document 只返回文本切片，
    无法满足这类需要文件对象的场景。返回的 virtual_path 是沙盒内可见路径，可直接在代码中读取。
    kb_id 是知识库资源 ID；file_id 来自 query_kb 或 search_file 的返回结果。
    """
    normalized_kb_id = str(kb_id or "").strip()
    normalized_file_id = str(file_id or "").strip()
    if not normalized_kb_id:
        return "请提供 kb_id"
    if not normalized_file_id:
        return "请提供 file_id"

    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    target_kb_id, target_error = _find_query_target(kb_id=normalized_kb_id, visible_kbs=visible_kbs)
    if target_error:
        return target_error

    knowledge_base = _get_knowledge_base()
    try:
        data = await knowledge_base.get_file_download(target_kb_id, normalized_file_id, variant="original")
    except ValueError as e:
        return str(e)
    except Exception as e:
        logger.error(f"下载知识库原始文件失败: {e}")
        return f"下载知识库原始文件失败: {str(e)}"

    sandbox_scope = _runtime_sandbox_scope(runtime)
    if sandbox_scope is None:
        return "无法获取当前会话的沙盒上下文，缺少 thread_id 或 uid"
    runtime_thread_id, uid, workdir_relative_path, workdir_path = sandbox_scope
    backend = ProvisionerSandboxBackend(
        thread_id=runtime_thread_id,
        uid=uid,
        workdir_path=workdir_relative_path,
        create_if_missing=True,
    )

    output_path = _resolve_download_output_path(backend, workdir_path, data, normalized_file_id, save_as)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="yuxi-kb-download-", delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(data["content"])
        await asyncio.to_thread(
            backend.upload_authorized_file_from_path,
            output_path,
            temp_path,
        )
    except (OSError, RuntimeError) as e:
        logger.error(f"写入沙盒 outputs 失败: {e}")
        return f"写入沙盒 outputs 失败: {str(e)}"
    finally:
        if temp_path:
            with suppress(FileNotFoundError):
                await asyncio.to_thread(os.unlink, temp_path)

    return {
        "virtual_path": output_path,
        "filename": data["filename"] or normalized_file_id,
        "media_type": data["media_type"],
        "size_bytes": len(data["content"]),
        "saved_as": PurePosixPath(output_path).name,
    }


# ========== 共享 helper（细节层） ==========


def _get_knowledge_base():
    from yuxi.knowledge.runtime import knowledge_base

    return knowledge_base


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
        logger.warning(f"解析会话可见知识库失败: {exc}")
        return []


def _find_query_target(
    *,
    kb_id: str,
    visible_kbs: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    """校验 kb_id 在当前会话可见知识库内，返回 (kb_id, error)。"""
    try:
        return knowledge_tools.require_visible_kb(kb_id, visible_kbs), None
    except knowledge_tools.KnowledgeToolError as exc:
        return None, str(exc)


def _runtime_sandbox_scope(runtime: ToolRuntime | None) -> tuple[str, str, str, str] | None:
    """返回知识库下载使用的 runtime、用户和 Workdir 路径。"""
    context = getattr(runtime, "context", None) if runtime else None
    if context is None:
        return None
    runtime_thread_id = getattr(context, "runtime_scope_id", None) or getattr(context, "thread_id", None)
    uid = getattr(context, "uid", None)
    workdir_relative_path = getattr(context, "workdir_relative_path", None)
    workdir_path = getattr(context, "workdir_path", None)
    if not runtime_thread_id or not uid or not workdir_relative_path or not workdir_path:
        return None
    return (
        str(runtime_thread_id),
        str(uid),
        str(workdir_relative_path),
        str(workdir_path),
    )


def _resolve_download_output_path(
    backend: ProvisionerSandboxBackend,
    workdir_path: str,
    data: dict[str, Any],
    file_id: str,
    save_as: str | None,
) -> str:
    """计算沙盒 outputs 目录下的落盘路径，处理重名与路径穿越防护。"""
    # 仅取文件名部分，剥离任何目录，防止路径穿越
    wanted_name = (save_as or data.get("filename") or file_id).strip()
    base_name = Path(wanted_name).name or file_id

    candidate = f"{workdir_path}/outputs/{base_name}"
    if not backend.regular_file_exists(candidate):
        return candidate

    # 重名时追加 _1 / _2 ... 后缀
    pure_candidate = PurePosixPath(candidate)
    stem = pure_candidate.stem
    suffix = pure_candidate.suffix
    index = 1
    while backend.regular_file_exists(candidate):
        candidate = f"{workdir_path}/outputs/{stem}_{index}{suffix}"
        index += 1
    return candidate
