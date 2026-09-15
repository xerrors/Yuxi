"""用户级 Memory 的授权写入与主 Agent 上下文读取。"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from yuxi.config.user import UserConfig
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.workspace.filesystem import Workspace

MEMORY_PATH = "/agents/MEMORY.md"
MEMORY_PROMPT_MAX_BYTES = 24 * 1024
MEMORY_FILE_MAX_BYTES = 128 * 1024
MEMORY_ARGUMENT_MAX_BYTES = 4 * 1024


async def load_memory_prompt(uid: str) -> str | None:
    """开关开启时读取当前用户 Memory 的有界 prompt 前缀。"""
    normalized_uid = str(uid or "").strip()
    if not normalized_uid:
        return None

    async with pg_manager.get_async_session_context() as db:
        config = await UserConfig.load(db, normalized_uid)
    if not config.schema.enable_memory:
        return None

    try:
        content, truncated = await asyncio.to_thread(
            Workspace(normalized_uid).read_authorized_file_prefix,
            MEMORY_PATH,
            MEMORY_PROMPT_MAX_BYTES,
        )
    except FileNotFoundError:
        # 用户可在 Workspace 中删除 MEMORY.md，视为无记忆而非错误
        return None
    prompt = content.decode("utf-8", errors="replace").strip()
    if not prompt:
        return None
    if truncated:
        prompt = f"{prompt}\n\n[MEMORY.md 内容已截断；可在 Workspace 中整理源文件]"
    return prompt


async def remember_memory(
    *,
    uid: str,
    thread_id: str,
    run_id: str,
    request_id: str,
    worker_id: str,
    content: str,
    replaces: str | None = None,
) -> dict:
    """由当前有效顶层 Run 原子更新固定用户 Memory 文件。"""
    normalized_uid = str(uid or "").strip()
    normalized_thread_id = str(thread_id or "").strip()
    normalized_run_id = str(run_id or "").strip()
    normalized_request_id = str(request_id or "").strip()
    normalized_worker_id = str(worker_id or "").strip()
    if not all((normalized_uid, normalized_thread_id, normalized_run_id, normalized_request_id, normalized_worker_id)):
        raise ValueError("Memory 写入缺少可信运行身份")

    normalized_content = _validate_argument(content, name="content").strip()
    normalized_replaces = None if replaces is None else _validate_argument(replaces, name="replaces")

    async with pg_manager.get_async_session_context() as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"user-memory:{normalized_uid}"},
        )
        run = await AgentRunRepository(db).lock_memory_write(
            normalized_run_id,
            uid=normalized_uid,
            worker_id=normalized_worker_id,
            conversation_thread_id=normalized_thread_id,
            request_id=normalized_request_id,
        )
        if run is None:
            raise ValueError("Memory 写入对应的 AgentRun 不存在")

        config = await UserConfig.load(db, normalized_uid)
        if not config.schema.enable_memory:
            raise ValueError("Memory 已关闭")

        current = await asyncio.to_thread(_read_memory_file, normalized_uid)
        updated, status, start_line, end_line = _build_memory_update(current, normalized_content, normalized_replaces)
        if status == "unchanged":
            size = len(current.encode("utf-8"))
        else:
            metadata = await asyncio.to_thread(_replace_memory_file, normalized_uid, updated)
            size = metadata["size"]

        return {
            "status": status,
            "path": MEMORY_PATH,
            "size": size,
            "start_line": start_line,
            "end_line": end_line,
        }


async def search_thread_messages(*, uid: str, query: str, limit: int = 5, consumer_thread_id: str) -> dict:
    """搜索 Memory 已启用用户的可见主 Agent 历史。"""
    normalized_uid = str(uid or "").strip()
    if not normalized_uid:
        raise ValueError("历史搜索缺少用户身份")
    async with pg_manager.get_async_session_context() as db:
        config = await UserConfig.load(db, normalized_uid)
        if not config.schema.enable_memory:
            raise ValueError("Memory 已关闭")
        result = await ConversationRepository(db).search_memory_messages(
            uid=normalized_uid,
            query=query,
            limit=limit,
        )

        await consume_memory_history(
            db, normalized_uid, consumer_thread_id, {item["thread_id"] for item in result["items"]}
        )
        return result


async def read_thread_messages(
    *,
    uid: str,
    thread_id: str,
    message_id: int | None = None,
    limit: int = 20,
    include_tools: bool = False,
    consumer_thread_id: str,
) -> dict:
    """读取 Memory 已启用用户的可见主 Agent 历史。"""
    normalized_uid = str(uid or "").strip()
    if not normalized_uid:
        raise ValueError("历史读取缺少用户身份")
    async with pg_manager.get_async_session_context() as db:
        config = await UserConfig.load(db, normalized_uid)
        if not config.schema.enable_memory:
            raise ValueError("Memory 已关闭")
        result = await ConversationRepository(db).read_memory_messages(
            uid=normalized_uid,
            thread_id=thread_id,
            message_id=message_id,
            limit=limit,
            include_tools=include_tools,
        )

        await consume_memory_history(db, normalized_uid, consumer_thread_id, {result["thread_id"]})
        return result


async def consume_memory_history(db, uid: str, consumer_thread_id: str, source_thread_ids: set[str]) -> None:
    """历史正文返回前复核并传播个人文件来源时间，不把新会话当作来源洗白。"""
    from fastapi import HTTPException
    from yuxi.repositories.personal_trash_repository import PersonalTrashRepository
    from yuxi.services.personal_file_evidence_service import (
        MEMORY_HISTORY_STARTED_AT,
        require_current_personal_file_history,
    )

    if not consumer_thread_id:
        raise ValueError("历史读取缺少可信消费会话")
    try:
        earliest = await require_current_personal_file_history(db, uid, consumer_thread_id)
        consumers = await PersonalTrashRepository(db).lock_conversations(uid, consumer_thread_id)
        for thread_id in sorted(source_thread_ids | {consumer_thread_id}):
            earliest = min(earliest, await require_current_personal_file_history(db, uid, thread_id))
        for conversation in consumers:
            metadata = dict(conversation.extra_metadata or {})
            metadata[MEMORY_HISTORY_STARTED_AT] = earliest.isoformat()
            conversation.extra_metadata = metadata
        await db.commit()
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


def _validate_argument(value: str, *, name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise ValueError(f"{name} 不能为空")
    if len(raw.encode("utf-8")) > MEMORY_ARGUMENT_MAX_BYTES:
        raise ValueError(f"{name} 超过 {MEMORY_ARGUMENT_MAX_BYTES} bytes")
    return raw


def _read_memory_file(uid: str) -> str:
    try:
        content, truncated = Workspace(uid).read_authorized_file_prefix(MEMORY_PATH, MEMORY_FILE_MAX_BYTES)
    except FileNotFoundError:
        # 文件被用户删除时按空记忆处理，写入路径会重建
        return ""
    if truncated:
        raise ValueError("memory_too_large: 请先在 Workspace 中整理 MEMORY.md")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("memory_invalid_encoding: MEMORY.md 必须使用 UTF-8") from exc


def _build_memory_update(current: str, content: str, replaces: str | None) -> tuple[str, str, int, int]:
    if replaces is None:
        normalized_content = _normalize_for_duplicate_check(content)
        if normalized_content in _normalize_for_duplicate_check(current):
            start_line, end_line = _find_content_line_range(current, content)
            return current, "unchanged", start_line, end_line

        appended_content = content.strip()
        content_line_count = len(appended_content.splitlines())
        trimmed_current = current.rstrip()
        if not trimmed_current:
            updated = f"{appended_content}\n"
            start_line = 1
        else:
            updated = f"{trimmed_current}\n\n{appended_content}\n"
            prefix = f"{trimmed_current}\n\n"
            start_line = prefix.count("\n") + 1
        end_line = start_line + max(0, content_line_count - 1)

        return updated, "updated", start_line, end_line

    matches = current.count(replaces)
    if matches != 1:
        raise ValueError(f"memory_conflict: replaces 必须唯一匹配，当前匹配 {matches} 处")

    before, _, after = current.partition(replaces)
    start_line = before.count("\n") + 1
    updated = f"{before}{content}{after}"
    end_line = start_line + max(0, len(content.splitlines()) - 1)
    return updated, "updated", start_line, end_line


def _replace_memory_file(uid: str, content: str) -> dict:
    encoded = content.encode("utf-8")
    if len(encoded) > MEMORY_FILE_MAX_BYTES:
        raise ValueError("memory_too_large: 更新后的 MEMORY.md 超过大小上限")
    return Workspace(uid).replace_authorized_file(MEMORY_PATH, encoded)


def _normalize_for_duplicate_check(value: str) -> str:
    return " ".join(value.split())


def _find_content_line_range(text: str, target: str) -> tuple[int, int]:
    trimmed_target = target.strip()
    if trimmed_target and trimmed_target in text:
        before, _, _ = text.partition(trimmed_target)
        start_line = before.count("\n") + 1
        end_line = start_line + max(0, len(trimmed_target.splitlines()) - 1)
        return start_line, end_line

    lines = text.splitlines()
    normalized_target = _normalize_for_duplicate_check(target)
    for idx, line in enumerate(lines, start=1):
        if normalized_target in _normalize_for_duplicate_check(line):
            return idx, idx

    return 1, max(1, len(lines))
