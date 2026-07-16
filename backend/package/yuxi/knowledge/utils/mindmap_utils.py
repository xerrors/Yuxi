"""思维导图工具函数。"""

import copy
import json
import textwrap
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from yuxi import config
from yuxi.knowledge.runtime import knowledge_base
from yuxi.models import select_model
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.utils import logger

MINDMAP_FILE_PAGE_SIZE = 500
MINDMAP_GENERATION_FILE_LIMIT = 200

MINDMAP_SYSTEM_PROMPT = """你是一个专业的知识整理助手。

你的任务是分析用户提供的文件列表，生成一个层次分明的思维导图结构。

**核心规则：每个文件名只能出现一次！不允许重复！**

要求：
1. 思维导图要有清晰的层级结构（2-4层）
2. 根节点是知识库名称
3. 第一层是主要分类（如：技术文档、规章制度、数据资源等）
4. 第二层是子分类
5. **叶子节点必须是具体的文件名称**
6. **每个文件名在整个思维导图中只能出现一次，不得重复！**
7. 如果一个文件可能属于多个分类，只选择最合适的一个分类放置
8. 使用合适的emoji图标增强可读性
9. 返回JSON格式，遵循以下结构：

```json
{
  "content": "知识库名称",
  "children": [
    {
      "content": "🎯 主分类1",
      "children": [
        {
          "content": "子分类1.1",
          "children": [
            {"content": "文件名1.txt", "children": []},
            {"content": "文件名2.pdf", "children": []}
          ]
        }
      ]
    },
    {
      "content": "💻 主分类2",
      "children": [
        {"content": "文件名3.docx", "children": []},
        {"content": "文件名4.md", "children": []}
      ]
    }
  ]
}
```

**重要约束：**
- 每个文件名在整个JSON中只能出现一次
- 不要按多个维度分类导致文件重复
- 选择最主要、最合适的分类维度
- 每个叶子节点的children必须是空数组[]
- 分类名称要简洁明了
- 使用emoji增强视觉效果
"""

MINDMAP_INCREMENTAL_SYSTEM_PROMPT = """你是一个专业的知识整理助手。

你的任务是将新文件整合到已有的思维导图结构中。

**核心规则：**
1. 保留现有思维导图的分类结构不变
2. 将新文件添加到最合适的已有分类下
3. 如果新文件不属于任何现有分类，可以创建新的分类节点
4. 每个文件名只能出现一次，不允许重复
5. 如果已有分类名称需要微调以容纳新文件，可以适当调整
6. 返回完整的思维导图JSON（包含原有结构 + 新文件）

返回JSON格式同标准思维导图结构。
"""


def build_database_file_list(files: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "file_id": file_id,
            "filename": file_info.get("filename", ""),
            "type": file_info.get("type", ""),
            "status": file_info.get("status", ""),
            "created_at": file_info.get("created_at", ""),
        }
        for file_id, file_info in files.items()
    ]


def _file_record_to_mindmap_file(record: Any) -> dict[str, Any]:
    created_at = getattr(record, "created_at", None)
    return {
        "file_id": getattr(record, "file_id"),
        "filename": getattr(record, "filename", None) or "",
        "type": getattr(record, "file_type", None) or "",
        "status": getattr(record, "status", None) or "",
        "created_at": created_at.isoformat() if created_at else "",
    }


async def _list_mindmap_files_page(
    kb_id: str, *, page_size: int = MINDMAP_FILE_PAGE_SIZE
) -> tuple[dict[str, dict], int]:
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    records, total = await KnowledgeFileRepository().list_documents(
        kb_id=kb_id,
        page=1,
        page_size=page_size,
        files_only=True,
    )
    return {record.file_id: _file_record_to_mindmap_file(record) for record in records}, total


async def _load_mindmap_current_files(kb_id: str, tracked_file_ids: list[str]) -> tuple[dict[str, dict], int]:
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    current_files, total = await _list_mindmap_files_page(kb_id)
    tracked_ids = [file_id for file_id in tracked_file_ids if file_id]
    if not tracked_ids:
        return current_files, total

    tracked_records = await KnowledgeFileRepository().list_by_file_ids(tracked_ids)
    for record in tracked_records:
        if record.kb_id == kb_id and not record.is_folder:
            current_files[record.file_id] = _file_record_to_mindmap_file(record)
    return current_files, total


def collect_mindmap_files(all_files: dict[str, dict[str, Any]], file_ids: list[str]) -> list[dict[str, str]]:
    return [
        {
            "filename": all_files[file_id].get("filename", ""),
            "type": all_files[file_id].get("type", ""),
        }
        for file_id in file_ids
        if file_id in all_files
    ]


def build_mindmap_user_message(db_name: str, files_info: list[dict[str, str]], user_prompt: str = "") -> str:
    files_text = "\n".join([f"- {file_info['filename']} ({file_info['type']})" for file_info in files_info])
    return textwrap.dedent(f"""请为知识库\"{db_name}\"生成思维导图结构。

        文件列表（共{len(files_info)}个文件）：
        {files_text}

        {f"用户补充说明：{user_prompt}" if user_prompt else ""}

        **重要提醒：**
        1. 这个知识库共有{len(files_info)}个文件
        2. 每个文件名只能在思维导图中出现一次
        3. 不要让同一个文件出现在多个分类下
        4. 为每个文件选择最合适的唯一分类

        请生成合理的思维导图结构。""")


def build_mindmap_incremental_user_message(
    db_name: str, mindmap_data: dict[str, Any], added_files: list[dict[str, str]], user_prompt: str = ""
) -> str:
    existing_structure = json.dumps(mindmap_data, ensure_ascii=False, indent=2)
    files_text = "\n".join([f"- {f['filename']} ({f['type']})" for f in added_files])
    return textwrap.dedent(f"""请将以下新文件整合到知识库\"{db_name}\"的现有思维导图中。

        现有思维导图结构：
        {existing_structure}

        新增文件列表（共{len(added_files)}个文件）：
        {files_text}

        {f"用户补充说明：{user_prompt}" if user_prompt else ""}

        **重要提醒：**
        1. 保留现有分类结构，将新文件添加到最合适的已有分类下
        2. 如果新文件不适合任何现有分类，创建新的分类节点
        3. 每个文件名只能出现一次
        4. 返回完整的思维导图JSON（包含原有结构 + 新文件）

        请整合新文件到现有结构中。""")


def parse_mindmap_content(content: str) -> dict[str, Any]:
    if "```json" in content:
        json_start = content.find("```json") + 7
        json_end = content.find("```", json_start)
        content = content[json_start:json_end].strip()
    elif "```" in content:
        json_start = content.find("```") + 3
        json_end = content.find("```", json_start)
        content = content[json_start:json_end].strip()

    mindmap_data = json.loads(content)
    if not isinstance(mindmap_data, dict) or "content" not in mindmap_data:
        raise ValueError("思维导图结构不正确")
    return mindmap_data


def detect_mindmap_changes(
    mindmap_data: dict[str, Any] | None,
    mindmap_file_ids: dict[str, str] | None,
    current_files: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """对比思维导图追踪的文件与知识库当前文件，返回变更信息。"""
    # 兼容旧数据：如果存在思维导图但缺少追踪的 file_ids，通过叶子节点反向重建映射
    if mindmap_data and not mindmap_file_ids:
        leaf_filenames = _collect_leaf_filenames(mindmap_data)
        mindmap_file_ids = {
            fid: info.get("filename", "")
            for fid, info in current_files.items()
            if info.get("filename", "") in leaf_filenames
        }

    if not mindmap_data or not mindmap_file_ids:
        added_files = [
            {"file_id": fid, "filename": info.get("filename", ""), "type": info.get("type", "")}
            for fid, info in current_files.items()
        ]
        return {
            "has_mindmap": mindmap_data is not None,
            "tracked_files": list(mindmap_file_ids.keys()) if mindmap_file_ids else [],
            "current_files": list(current_files.keys()),
            "added_files": added_files,
            "removed_file_ids": [],
            "unchanged_count": 0,
            "needs_update": len(added_files) > 0,
        }

    tracked_ids = set(mindmap_file_ids.keys())
    current_ids = set(current_files.keys())

    removed_file_ids = list(tracked_ids - current_ids)
    added_file_ids = current_ids - tracked_ids
    added_files = [
        {"file_id": fid, "filename": current_files[fid].get("filename", ""), "type": current_files[fid].get("type", "")}
        for fid in sorted(added_file_ids)
        if fid in current_files
    ]
    unchanged_count = len(tracked_ids & current_ids)

    return {
        "has_mindmap": True,
        "tracked_files": list(tracked_ids),
        "current_files": list(current_ids),
        "added_files": added_files,
        "removed_file_ids": removed_file_ids,
        "unchanged_count": unchanged_count,
        "needs_update": len(added_files) > 0 or len(removed_file_ids) > 0,
    }


def _prune_mindmap_node(node: dict[str, Any], removed_filenames: set[str], root_name: str) -> dict[str, Any] | None:
    """递归修剪思维导图节点，移除指定文件名的叶子节点。"""
    content = node.get("content", "")
    children = node.get("children", [])

    if not children:
        if content in removed_filenames:
            return None
        return node

    pruned_children = []
    for child in children:
        result = _prune_mindmap_node(child, removed_filenames, root_name)
        if result is not None:
            pruned_children.append(result)

    if not pruned_children:
        if content == root_name:
            node["children"] = []
            return node
        return None

    node["children"] = pruned_children
    return node


def remove_files_from_mindmap(mindmap_data: dict[str, Any], removed_filenames: set[str]) -> dict[str, Any]:
    """从思维导图树中移除指定文件名的叶子节点，无需 AI 调用。"""
    if not removed_filenames:
        return mindmap_data

    mindmap_copy = copy.deepcopy(mindmap_data)
    root_name = mindmap_copy.get("content", "")
    result = _prune_mindmap_node(mindmap_copy, removed_filenames, root_name)
    return result if result is not None else {"content": root_name, "children": []}


async def get_mindmap_database_files(kb_id: str) -> dict[str, Any]:
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    current_files, total = await _list_mindmap_files_page(kb_id)
    return {
        "message": "success",
        "kb_id": kb_id,
        "slug": kb_id,
        "db_name": kb.name,
        "files": build_database_file_list(current_files),
        "total": total,
        "truncated": total > len(current_files),
    }


async def get_mindmap_diff(kb_id: str) -> dict[str, Any]:
    """获取思维导图变更检测结果。"""
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    current_files, total = await _load_mindmap_current_files(kb_id, list((kb.mindmap_file_ids or {}).keys()))

    changes = detect_mindmap_changes(kb.mindmap, kb.mindmap_file_ids, current_files)
    changes["current_total"] = total
    changes["current_files_truncated"] = total > len(current_files)
    changes["kb_id"] = kb_id
    changes["slug"] = kb_id
    changes["message"] = "success"
    return changes


async def update_mindmap_incremental(kb_id: str, user_prompt: str = "") -> dict[str, Any]:
    """增量更新思维导图：纯删除场景无需 AI，有新增时调用 AI 整合。"""
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None or not kb.mindmap:
        raise HTTPException(status_code=400, detail="知识库没有现有思维导图，请使用全量生成")

    current_files, total = await _load_mindmap_current_files(kb_id, list((kb.mindmap_file_ids or {}).keys()))
    db_name = kb.name or "知识库"

    changes = detect_mindmap_changes(kb.mindmap, kb.mindmap_file_ids, current_files)
    changes["current_files_truncated"] = total > len(current_files)

    if not changes["needs_update"]:
        return {
            "message": "success",
            "mindmap": kb.mindmap,
            "kb_id": kb_id,
            "slug": kb_id,
            "db_name": db_name,
            "no_ai_needed": True,
            "no_changes": True,
        }

    mindmap_data = kb.mindmap
    if kb.mindmap_file_ids:
        updated_file_ids = dict(kb.mindmap_file_ids)
    else:
        leaf_filenames = _collect_leaf_filenames(mindmap_data)
        updated_file_ids = {
            fid: info.get("filename", "")
            for fid, info in current_files.items()
            if info.get("filename", "") in leaf_filenames
        }

    if changes["removed_file_ids"]:
        removed_filenames = {updated_file_ids[fid] for fid in changes["removed_file_ids"] if fid in updated_file_ids}
        mindmap_data = remove_files_from_mindmap(mindmap_data, removed_filenames)
        for fid in changes["removed_file_ids"]:
            updated_file_ids.pop(fid, None)

    if changes["added_files"]:
        added_files_info = collect_mindmap_files(current_files, [f["file_id"] for f in changes["added_files"]])
        if added_files_info:
            model = select_model(model_spec=config.default_model)
            messages = [
                {"role": "system", "content": MINDMAP_INCREMENTAL_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_mindmap_incremental_user_message(
                        db_name, mindmap_data, added_files_info, user_prompt
                    ),
                },
            ]
            response = await model.call(messages, stream=False)
            content = response.content if hasattr(response, "content") else str(response)

            try:
                mindmap_data = parse_mindmap_content(content)
            except ValueError as e:
                logger.error(f"增量AI返回的JSON解析失败: {e}, 原始内容: {content}")
                raise HTTPException(status_code=500, detail=f"AI返回格式错误: {str(e)}") from e

        for f in changes["added_files"]:
            updated_file_ids[f["file_id"]] = f["filename"]

    now = datetime.now(UTC).isoformat()
    metadata = {
        "generated_at": now,
        "file_count": len(updated_file_ids),
        "incremental": True,
    }

    try:
        await KnowledgeBaseRepository().update(
            kb_id,
            {
                "mindmap": mindmap_data,
                "mindmap_file_ids": updated_file_ids,
                "mindmap_metadata": metadata,
            },
        )
        logger.info(f"思维导图增量更新成功: {kb_id}")
    except Exception as save_error:
        logger.error(f"保存思维导图失败: {save_error}")

    no_ai = not changes["added_files"]
    return {
        "message": "success",
        "mindmap": mindmap_data,
        "kb_id": kb_id,
        "slug": kb_id,
        "db_name": db_name,
        "no_ai_needed": no_ai,
    }


async def generate_database_mindmap(
    kb_id: str, file_ids: list[str] | None = None, user_prompt: str = "", incremental: bool = False
) -> dict[str, Any]:
    if incremental:
        return await update_mindmap_incremental(kb_id, user_prompt)

    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    db_name = kb.name or "知识库"
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    file_repo = KnowledgeFileRepository()
    if file_ids:
        original_count = len(file_ids)
        selected_file_ids = list(file_ids[:MINDMAP_GENERATION_FILE_LIMIT])
        if len(file_ids) > MINDMAP_GENERATION_FILE_LIMIT:
            logger.info(
                f"文件数量超过限制，已从{original_count}个文件中选择前{MINDMAP_GENERATION_FILE_LIMIT}个文件生成思维导图"
            )
        records = await file_repo.list_by_file_ids(selected_file_ids)
        all_files = {
            record.file_id: _file_record_to_mindmap_file(record)
            for record in records
            if record.kb_id == kb_id and not record.is_folder
        }
    else:
        all_files, original_count = await _list_mindmap_files_page(kb_id, page_size=MINDMAP_GENERATION_FILE_LIMIT)
        selected_file_ids = list(all_files.keys())

    if not selected_file_ids:
        raise HTTPException(status_code=400, detail="知识库中没有文件")

    files_info = collect_mindmap_files(all_files, selected_file_ids)
    if not files_info:
        raise HTTPException(status_code=400, detail="选择的文件不存在")

    logger.info(f"开始生成思维导图，知识库: {db_name}, 文件数量: {len(files_info)}")

    model = select_model(model_spec=config.default_model)
    messages = [
        {"role": "system", "content": MINDMAP_SYSTEM_PROMPT},
        {"role": "user", "content": build_mindmap_user_message(db_name, files_info, user_prompt)},
    ]
    response = await model.call(messages, stream=False)
    content = response.content if hasattr(response, "content") else str(response)

    try:
        mindmap_data = parse_mindmap_content(content)
    except ValueError as e:
        logger.error(f"AI返回的JSON解析失败: {e}, 原始内容: {content}")
        raise HTTPException(status_code=500, detail=f"AI返回格式错误: {str(e)}") from e

    logger.info("思维导图生成成功")

    now = datetime.now(UTC).isoformat()
    mindmap_file_ids = {fid: all_files[fid].get("filename", "") for fid in selected_file_ids if fid in all_files}
    mindmap_metadata = {
        "generated_at": now,
        "file_count": len(files_info),
        "incremental": False,
    }

    try:
        await KnowledgeBaseRepository().update(
            kb_id,
            {
                "mindmap": mindmap_data,
                "mindmap_file_ids": mindmap_file_ids,
                "mindmap_metadata": mindmap_metadata,
            },
        )
        logger.info(f"思维导图已保存到知识库: {kb_id}")
    except Exception as save_error:
        logger.error(f"保存思维导图失败: {save_error}")

    return {
        "message": "success",
        "mindmap": mindmap_data,
        "kb_id": kb_id,
        "slug": kb_id,
        "db_name": db_name,
        "file_count": len(files_info),
        "original_file_count": original_count,
        "truncated": len(files_info) < original_count,
    }


async def get_mindmap_databases_overview(uid: str) -> dict[str, Any]:
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    file_repo = KnowledgeFileRepository()
    databases = await knowledge_base.get_databases_by_uid(uid)
    db_list = []
    for db_info in databases.get("databases", []):
        kb_id = db_info.get("kb_id") or db_info.get("slug")
        if not kb_id:
            continue

        file_count = (await file_repo.get_kb_file_stats(kb_id))["file_count"]
        db_list.append(
            {
                "kb_id": kb_id,
                "slug": kb_id,
                "name": db_info.get("name", ""),
                "description": db_info.get("description", ""),
                "kb_type": db_info.get("kb_type", ""),
                "file_count": file_count,
            }
        )

    return {"message": "success", "databases": db_list, "total": len(db_list)}


async def get_database_mindmap_data(kb_id: str) -> dict[str, Any]:
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    return {
        "message": "success",
        "mindmap": kb.mindmap,
        "kb_id": kb_id,
        "slug": kb_id,
        "db_name": kb.name,
        "mindmap_file_ids": kb.mindmap_file_ids,
        "mindmap_metadata": kb.mindmap_metadata,
    }


def _collect_leaf_filenames(node: dict[str, Any]) -> set[str]:
    """递归收集思维导图中所有叶子节点的文件名。"""
    children = node.get("children", [])
    if not children:
        return {node.get("content", "")}
    result: set[str] = set()
    for child in children:
        result |= _collect_leaf_filenames(child)
    return result


async def remove_file_from_mindmap(kb_id: str, file_id: str, filename: str | None = None) -> None:
    """从思维导图中移除已删除文件的叶子节点（纯树手术，无 AI 调用）。

    Args:
        kb_id: 知识库 ID
        file_id: 被删除文件的 ID
        filename: 被删除文件的文件名（可选，用于旧数据兼容）
    """
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if not kb or not kb.mindmap:
        return

    removed_filename: str | None = None

    if kb.mindmap_file_ids and file_id in kb.mindmap_file_ids:
        removed_filename = kb.mindmap_file_ids[file_id]
    elif filename:
        leaf_filenames = _collect_leaf_filenames(kb.mindmap)
        if filename in leaf_filenames:
            removed_filename = filename

    if not removed_filename:
        return

    updated_mindmap = remove_files_from_mindmap(kb.mindmap, {removed_filename})
    updated_file_ids = (
        {fid: name for fid, name in kb.mindmap_file_ids.items() if fid != file_id} if kb.mindmap_file_ids else None
    )

    try:
        await KnowledgeBaseRepository().update(
            kb_id,
            {
                "mindmap": updated_mindmap,
                "mindmap_file_ids": updated_file_ids,
            },
        )
        logger.info(f"思维导图中已移除文件: {removed_filename}")
    except Exception as e:
        logger.error(f"从思维导图移除文件失败: {e}")


async def batch_remove_files_from_mindmap(kb_id: str, removals: list[tuple[str, str]]) -> None:
    """批量从思维导图中移除已删除文件的叶子节点（单次 DB 读写，无 AI 调用）。

    Args:
        kb_id: 知识库 ID
        removals: [(file_id, filename), ...] 待移除的文件列表
    """
    if not removals:
        return

    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if not kb or not kb.mindmap:
        return

    stale_filenames: set[str] = set()
    stale_file_ids: set[str] = set()

    for file_id, filename in removals:
        if kb.mindmap_file_ids and file_id in kb.mindmap_file_ids:
            stale_filenames.add(kb.mindmap_file_ids[file_id])
            stale_file_ids.add(file_id)
        elif filename:
            stale_filenames.add(filename)
            stale_file_ids.add(file_id)

    if not stale_filenames:
        return

    updated_mindmap = remove_files_from_mindmap(kb.mindmap, stale_filenames)
    updated_file_ids = (
        {fid: name for fid, name in kb.mindmap_file_ids.items() if fid not in stale_file_ids}
        if kb.mindmap_file_ids
        else None
    )

    try:
        await KnowledgeBaseRepository().update(
            kb_id,
            {
                "mindmap": updated_mindmap,
                "mindmap_file_ids": updated_file_ids,
            },
        )
        logger.info(f"思维导图批量清理完成: {kb_id}, 移除 {len(stale_filenames)} 个文件")
    except Exception as e:
        logger.error(f"从思维导图批量移除文件失败: {e}")
