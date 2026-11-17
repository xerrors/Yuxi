"""知识库管理用例：删除、工作区导入和描述生成。"""

import os
import textwrap

from yuxi.infrastructure.document_parsing import is_supported_file_extension
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.services import directories, documents, statistics
from yuxi.modules.knowledge.services.uploads import stage_document
from yuxi.modules.system.options import system_options
from yuxi.modules.workspace.services.files import read_workspace_file_bytes
from yuxi.shared.errors import DomainError, InvalidOperationError


async def delete_document(kb_id: str, doc_id: str) -> dict:
    """按目录事实删除文件或文件夹。"""
    await documents.require_document_support(kb_id, "文档删除")
    return await _delete_document(kb_id, doc_id)


async def _delete_document(kb_id: str, doc_id: str) -> dict:
    """按已校验类型的当前文件事实写入删除意图。"""
    info = await documents.get_file_basic_info(kb_id, doc_id)
    if info["meta"].get("is_folder"):
        await statistics.run_with_stats_refresh(kb_id, directories.delete_folder(kb_id, doc_id))
        return {"message": "文件夹删除成功"}
    await statistics.run_with_stats_refresh(kb_id, KnowledgeFileRepository().mark_deleted(kb_id=kb_id, file_id=doc_id))
    return {"message": "删除成功"}


async def batch_delete_documents(kb_id: str, file_ids: list[str]) -> dict:
    """逐项删除并保留部分成功结果。"""
    await documents.require_document_support(kb_id, "批量文档删除")
    deleted_count = 0
    failed_items = []
    for doc_id in file_ids:
        try:
            await _delete_document(kb_id, doc_id)
            deleted_count += 1
        except DomainError as error:
            failed_items.append({"doc_id": doc_id, "error": error.message})
        except Exception:
            logger.exception("删除知识库文档失败: {}", doc_id)
            failed_items.append({"doc_id": doc_id, "error": "删除失败，请稍后重试"})

    if failed_items:
        if deleted_count == 0:
            raise InvalidOperationError(f"批量删除失败: 所有 {len(failed_items)} 个文件均未删除。")
        return {
            "message": f"部分删除成功: 已删除 {deleted_count} 个文件，失败 {len(failed_items)} 个",
            "deleted_count": deleted_count,
            "failed_items": failed_items,
        }
    return {"message": f"批量删除成功: 已删除 {deleted_count} 个文件", "deleted_count": deleted_count}


async def import_workspace_files(kb_id: str, paths: list[str], current_user) -> list[dict]:
    """经过工作区权限边界读取文件，再写入知识库暂存区。"""
    results = []
    for workspace_path in paths:
        filename, content = await read_workspace_file_bytes(path=workspace_path, current_user=current_user)
        if not is_supported_file_extension(filename):
            raise InvalidOperationError(f"Unsupported file type: {os.path.splitext(filename)[1].lower()}")
        staged = await stage_document(kb_id=kb_id, filename=filename, content=content)
        results.append({"staged": staged, "filename": filename, "size": len(content), "workspace_path": workspace_path})
    return results


async def generate_description(name: str, current_description: str, file_list: list[str] | None) -> str:
    """根据名称、已有描述和有限文件摘要生成知识库说明。"""
    from yuxi.modules.models import select_model

    file_list = file_list or []
    logger.debug(f"Generating description for knowledge base: {name}, files: {len(file_list)}")

    # 构建文件列表文本
    if file_list:
        # 限制文件数量，避免 prompt 过长
        display_files = file_list[:50]
        files_str = "\n".join(f"- {file_name}" for file_name in display_files)
        more_text = f"\n... (还有 {len(file_list) - 50} 个文件)" if len(file_list) > 50 else ""
        current_description += f"\n\n知识库包含的文件:\n{files_str}{more_text}"

    current_description = current_description or "暂无描述"

    # 构建提示词
    prompt = textwrap.dedent(f"""
        请帮我优化以下知识库的描述。

        知识库名称: {name}
        当前描述: {current_description}

        要求:
        1. 这个描述将作为智能体工具的描述使用
        2. 智能体会根据知识库的标题和描述来选择合适的工具
        3. 所以描述需要清晰、具体，说明该知识库包含什么内容、适合解答什么类型的问题
        4. 描述应该简洁有力，通常 2-4 句话即可
        5. 不要使用 Markdown 格式
        {"6. 请参考提供的文件列表来准确概括知识库内容" if file_list else ""}

        请直接输出优化后的描述，不要有任何前缀说明。
    """).strip()

    model = select_model(model_spec=(await system_options.get())["default_model"])
    response = await model.call(prompt)
    return response.content.strip()
