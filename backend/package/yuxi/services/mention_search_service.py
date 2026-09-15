from __future__ import annotations

import asyncio

from yuxi.agents.backends.paths import runtime_path_for_workdir_scope, runtime_user_data_path
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.personal_trash_service import lock_user_files, require_no_pending_file_operations
from yuxi.services.workdir_service import resolve_authorized_workdir
from yuxi.workspace.filesystem import Workspace
from yuxi.workspace.paths import validate_thread_id

MENTION_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
MAX_MENTION_RESULTS = 50
MAX_MENTION_CANDIDATES = 500


class MentionThreadNotFoundError(LookupError):
    """当前用户不可见指定 mention thread。"""


class InvalidMentionThreadError(ValueError):
    """mention thread id 不满足运行时 identity 约束。"""


def _rank_entries(entries: list[dict], query: str, source: str) -> list[dict]:
    query_lower = query.lower()
    name_matches = []
    path_matches = []
    for entry in entries:
        name = str(entry["name"])
        path = str(entry["path"])
        name_lower = name.lower()
        path_lower = path.lower()
        if query_lower in name_lower:
            if name_lower == query_lower:
                score = 1000.0
            else:
                score = 500.0
                if name_lower.startswith(query_lower):
                    score += 50.0
                if name_lower.endswith(query_lower):
                    score += 20.0
                score -= min(max(name_lower.find(query_lower), 0), 30.0)
                score -= min(len(name) * 0.5, 50.0)
            name_matches.append((score, entry))
        elif query_lower in path_lower:
            path_matches.append((len(path), entry))

    name_matches.sort(key=lambda item: -item[0])
    path_matches.sort(key=lambda item: item[0])
    ranked = [entry for _score, entry in name_matches] + [entry for _length, entry in path_matches]
    return [
        {
            "name": str(entry["name"]),
            "path": f"{entry['path']}/" if entry["is_dir"] else str(entry["path"]),
            "is_dir": bool(entry["is_dir"]),
            "source": source,
        }
        for entry in ranked
    ]


def _runtime_entry(entry: dict, runtime_path: str) -> dict:
    """把搜索结果转换为可直接写入 Agent mention 的 runtime 路径。"""
    path = f"{runtime_path}/" if entry["is_dir"] else runtime_path
    return {**entry, "path": path}


async def _search_workspace(uid: str, query: str) -> list[dict]:
    try:
        entries = await asyncio.to_thread(
            Workspace(uid).search_authorized_tree,
            "/",
            query,
            exclude_directories=MENTION_EXCLUDE_DIRS,
            exclude_hidden=True,
            max_results=MAX_MENTION_CANDIDATES,
        )
    except FileNotFoundError:
        return []
    ranked = _rank_entries(entries, query, "workspace")
    return [_runtime_entry(entry, runtime_user_data_path(str(entry["path"]))) for entry in ranked]


async def _search_workdir(workdir, query: str) -> list[dict]:
    try:
        entries = await asyncio.to_thread(
            workdir.search,
            query,
            exclude_directories=MENTION_EXCLUDE_DIRS,
            exclude_hidden=True,
            max_results=MAX_MENTION_CANDIDATES,
        )
    except FileNotFoundError:
        return []
    ranked = _rank_entries(entries, query, "thread")
    return [
        _runtime_entry(
            entry,
            runtime_path_for_workdir_scope(workdir.relative_path, str(entry["path"])),
        )
        for entry in ranked
    ]


async def search_mentions(
    *,
    thread_id: str | None,
    query: str,
    sources: str | None,
    current_user,
    db,
) -> list[dict]:
    """用同一个有界实时扫描搜索当前 Workdir 与 UserWorkspace。"""
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return []

    uid = str(current_user.uid)
    await lock_user_files(db, uid)
    await require_no_pending_file_operations(db, uid)
    effective_thread_id: str | None = None
    if thread_id:
        conversation = await ConversationRepository(db).get_conversation_by_thread_id(thread_id)
        if conversation:
            if conversation.uid != uid or conversation.status == "deleted":
                raise MentionThreadNotFoundError("对话线程不存在")
            effective_thread_id = thread_id
        else:
            try:
                validate_thread_id(thread_id)
            except ValueError as exc:
                raise InvalidMentionThreadError("非法的 thread_id 格式") from exc

    source_list = [item.strip().lower() for item in sources.split(",")] if sources else None
    selected_sources = source_list or (["thread", "workspace"] if effective_thread_id else ["workspace"])
    results: list[dict] = []
    if "thread" in selected_sources and effective_thread_id:
        access = await resolve_authorized_workdir(thread_id=effective_thread_id, uid=uid, db=db)
        results.extend(await _search_workdir(access.workdir, normalized_query))
    if "workspace" in selected_sources and len(results) < MAX_MENTION_RESULTS:
        results.extend(await _search_workspace(uid, normalized_query))
    return results[:MAX_MENTION_RESULTS]
