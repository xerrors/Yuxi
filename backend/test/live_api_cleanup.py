from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

import asyncpg
import httpx
from yuxi.modules.workspace.paths import normalize_managed_workdir_path, normalize_workdir_path, user_workdir_host_dir
from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES
from test.support.postgres import postgres_dsn

TEST_RESOURCE_PREFIX = "YUXI_TEST_"
TEST_SESSION_TITLE_PREFIX = f"{TEST_RESOURCE_PREFIX}SESSION_"
PYTEST_RESOURCE_PREFIXES = ("pytest", "py_test")
E2E_AGENT_SLUG_PREFIXES = (
    "ci-lifecycle-",
    "e2e-provider-",
    "e2e-async-agent-reasoning-",
    "e2e-personal-skill-",
    "e2e-read-file-",
    "pytest-personal-agent-",
)
SAFE_THREAD_ID = re.compile(r"^[A-Za-z0-9_-]+$")
RUN_CLEANUP_WAIT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class CleanupSessionResource:
    """描述一个待清理的测试 Session 及其 Project Workdir。"""

    session_record_id: int
    project_id: str
    thread_id: str
    uid: str
    status: str
    workdir_path: str | None


def make_test_session_title(label: str) -> str:
    """生成带统一前缀且适合展示的测试 Session 标题。"""

    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", str(label)).strip("-_") or "case"
    return f"{TEST_SESSION_TITLE_PREFIX}{normalized[:120]}_{uuid.uuid4().hex[:12]}"


def make_test_resource_id(label: str) -> str:
    """生成用于测试请求等资源的统一可检索标识。"""

    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", str(label)).strip("-_") or "resource"
    return f"{TEST_RESOURCE_PREFIX}{normalized[:40]}_{uuid.uuid4().hex}"


def make_test_session_metadata(test_name: str, **extra: object) -> dict[str, object]:
    """生成带测试资源标记的 Session metadata。"""

    metadata: dict[str, object] = {"_yuxi_test": True, "test": test_name}
    metadata.update(extra)
    return metadata


def _is_pytest_resource(name: object) -> bool:
    """判断资源名称是否属于 pytest 约定的测试数据。"""

    return isinstance(name, str) and name.casefold().startswith(PYTEST_RESOURCE_PREFIXES)


def _has_prefix(value: object, prefixes: tuple[str, ...]) -> bool:
    """判断字符串是否以任一约定前缀开头。"""

    return isinstance(value, str) and value.startswith(prefixes)


def _parse_metadata(value: object) -> dict[str, object]:
    """把数据库或 HTTP 返回的 metadata 统一解析为字典。"""

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def is_test_session_title(title: object) -> bool:
    """只识别当前测试生成器发布的统一标题。"""
    return isinstance(title, str) and title.startswith(TEST_SESSION_TITLE_PREFIX)


def _is_test_thread(thread: object) -> bool:
    """测试 Session 必须有明确 metadata 或统一标题，不能从 Agent 名称推断。"""
    if not isinstance(thread, dict):
        return False
    metadata = _parse_metadata(thread.get("metadata") or thread.get("extra_metadata"))
    return metadata.get("_yuxi_test") is True or is_test_session_title(thread.get("title"))


def _is_e2e_agent(agent: object, owner_uid: str) -> bool:
    """判断智能体是否是当前清理用户创建的 E2E 临时智能体。"""

    if not isinstance(agent, dict):
        return False
    slug = agent.get("slug") or agent.get("agent_id") or agent.get("id")
    return _has_prefix(slug, E2E_AGENT_SLUG_PREFIXES) and str(agent.get("created_by") or "") == owner_uid


def _resolve_test_workdir(uid: str, workdir_path: str) -> Path | None:
    """校验并返回 UserWorkspace 内可清理的托管 Workdir。"""
    try:
        normalized = normalize_workdir_path(workdir_path)
    except ValueError as exc:
        raise RuntimeError(f"Test agent_session cleanup refuses invalid Workdir: {workdir_path!r}") from exc
    parts = PurePosixPath(normalized).parts
    if len(parts) < 2 or parts[0] != "projects":
        try:
            normalize_managed_workdir_path(normalized)
        except ValueError as exc:
            raise RuntimeError(f"Test agent_session cleanup refuses non-managed Workdir: {workdir_path!r}") from exc

    try:
        workdir = user_workdir_host_dir(uid, normalized)
    except FileNotFoundError:
        return None
    except ValueError as exc:
        if str(exc) == "workdir_path does not reference an existing directory":
            return None
        raise RuntimeError(f"Test agent_session cleanup refuses invalid Workdir: {workdir_path!r}") from exc
    if not workdir.exists() and not workdir.is_symlink():
        return None
    if workdir.is_symlink():
        raise RuntimeError(f"Test agent_session cleanup refuses symlink Workdir: {workdir}")
    return workdir


def remove_test_workdir(uid: str, workdir_path: str) -> None:
    """在 UserWorkspace 边界内删除测试 Session 的 Project Workdir。"""

    workdir = _resolve_test_workdir(uid, workdir_path)
    if workdir is None:
        return

    shutil.rmtree(workdir)
    if workdir.exists() or workdir.is_symlink():
        raise RuntimeError(f"Test agent_session cleanup left Workdir behind: {workdir}")


async def list_test_session_resources(owner_uid: str) -> dict[str, CleanupSessionResource]:
    """读取当前测试用户的测试 Session、状态和真实 Workdir。"""

    conn = await asyncpg.connect(postgres_dsn())
    try:
        receipt_rows = await conn.fetch(
            "SELECT DISTINCT thread_id FROM agent_input_receipts WHERE uid = $1 AND left(idempotency_key, char_length($2)) = $2",
            owner_uid,
            TEST_RESOURCE_PREFIX,
        )
        receipt_thread_ids = {str(row["thread_id"] or "") for row in receipt_rows}
        rows = await conn.fetch(
            "SELECT c.id, c.project_id, c.thread_id, c.uid, c.status, c.title, "
            "p.workdir_path, p.directory_mode, p.selection_status, c.extra_metadata "
            "FROM sessions c JOIN projects p ON p.id = c.project_id AND p.uid = c.uid "
            "WHERE c.uid = $1",
            owner_uid,
        )
        marked_parent_ids: list[int] = []
        resources: dict[str, CleanupSessionResource] = {}
        for row in rows:
            thread_id = str(row["thread_id"] or "")
            if (
                not _is_test_thread(
                    {
                        "title": row["title"],
                        "metadata": row["extra_metadata"],
                    }
                )
                and thread_id not in receipt_thread_ids
            ):
                continue
            marked_parent_ids.append(int(row["id"]))
            resources[thread_id] = CleanupSessionResource(
                session_record_id=int(row["id"]),
                project_id=str(row["project_id"]),
                thread_id=thread_id,
                uid=str(row["uid"] or owner_uid),
                status=str(row["status"] or ""),
                workdir_path=(
                    str(row["workdir_path"])
                    if row["directory_mode"] == "managed" and row["selection_status"] == "implicit" and row["workdir_path"]
                    else None
                ),
            )

        if marked_parent_ids:
            child_rows = await conn.fetch(
                """
                WITH RECURSIVE descendants(id) AS (
                    SELECT child.id FROM sessions child
                    JOIN sessions parent ON child.parent_thread_id = parent.thread_id
                    WHERE parent.id = ANY($1::int[])
                    UNION
                    SELECT child.id FROM sessions child
                    JOIN sessions parent ON child.parent_thread_id = parent.thread_id
                    JOIN descendants ancestor ON ancestor.id = parent.id
                )
                SELECT child.id, child.project_id, child.thread_id, child.uid, child.status,
                       project.workdir_path, project.directory_mode, project.selection_status
                FROM descendants
                JOIN sessions child ON child.id = descendants.id
                JOIN projects project ON project.id = child.project_id AND project.uid = child.uid
                """,
                marked_parent_ids,
            )
            for row in child_rows:
                child_id = str(row["thread_id"] or "")
                resources[child_id] = CleanupSessionResource(
                    session_record_id=int(row["id"]),
                    project_id=str(row["project_id"]),
                    thread_id=child_id,
                    uid=str(row["uid"] or owner_uid),
                    status=str(row["status"] or ""),
                    workdir_path=(
                        str(row["workdir_path"])
                        if row["directory_mode"] == "managed" and row["selection_status"] == "implicit" and row["workdir_path"]
                        else None
                    ),
                )
        return resources
    finally:
        await conn.close()


async def validate_test_workdirs_exclusive(
    workdirs: dict[tuple[str, str], set[str]],
    target_project_ids: set[str],
) -> None:
    """确认待删 Workdir 未被目标外的 Project 共享。"""

    if not workdirs:
        return
    conn = await asyncpg.connect(postgres_dsn())
    try:
        await _validate_test_workdirs_exclusive(conn, workdirs, target_project_ids)
    finally:
        await conn.close()


async def _validate_test_workdirs_exclusive(
    conn: asyncpg.Connection,
    workdirs: dict[tuple[str, str], set[str]],
    target_project_ids: set[str],
) -> None:
    """使用调用方事务确认 Project Workdir 没有目标外 Owner。"""

    rows_by_uid: dict[str, list[asyncpg.Record]] = {}
    for uid, _workdir_path in workdirs:
        if uid not in rows_by_uid:
            rows_by_uid[uid] = await conn.fetch(
                "SELECT id, workdir_path FROM projects WHERE uid = $1",
                uid,
            )

    for (uid, workdir_path), project_ids in workdirs.items():
        try:
            target_path = PurePosixPath(normalize_workdir_path(workdir_path))
        except ValueError as exc:
            raise RuntimeError(f"Test agent_session cleanup refuses invalid Workdir: {workdir_path!r}") from exc
        owners: set[str] = set()
        for row in rows_by_uid[uid]:
            candidate_value = str(row["workdir_path"] or "")
            try:
                candidate_path = PurePosixPath(normalize_workdir_path(candidate_value))
            except ValueError as exc:
                raise RuntimeError(
                    f"Test agent_session cleanup cannot verify an existing Workdir owner: {row['id']}={candidate_value!r}"
                ) from exc
            overlaps = candidate_path == target_path or candidate_path in target_path.parents or target_path in candidate_path.parents
            if overlaps:
                owners.add(str(row["id"] or ""))
        unexpected = owners - target_project_ids
        if unexpected:
            raise RuntimeError(
                f"Test agent_session cleanup refuses shared or overlapping Workdir {workdir_path!r}; other projects: {', '.join(sorted(unexpected))}"
            )
        if not project_ids <= target_project_ids:
            raise RuntimeError(f"Test agent_session cleanup has an untracked Workdir owner: {workdir_path!r}")


async def validate_test_runs_terminal(thread_ids: set[str]) -> None:
    """等待历史 Run 释放运行时，并拒绝仍活跃的 Turn 或 Run。"""

    if not thread_ids:
        return
    conn = None
    last = []
    try:
        async with asyncio.timeout(RUN_CLEANUP_WAIT_SECONDS):
            conn = await asyncpg.connect(postgres_dsn())
            target_ids = sorted(thread_ids)
            while True:
                turns = await conn.fetch(
                    "SELECT id, status FROM agent_turns WHERE thread_id = ANY($1::text[]) "
                    "AND status IN ('running', 'waiting', 'cancelling')",
                    target_ids,
                )
                if turns:
                    details = ", ".join(f"{row['id']}={row['status']}" for row in turns)
                    raise RuntimeError(f"test Turn is not terminal: {details}")

                rows = await conn.fetch(
                    "SELECT id, status, runtime_cleanup_pending FROM agent_runs WHERE thread_id = ANY($1::text[]) "
                    "AND (status <> ALL($2::text[]) OR runtime_cleanup_pending)",
                    target_ids,
                    list(AGENT_RUN_TERMINAL_STATUSES),
                )
                last = [dict(row) for row in rows]
                nonterminal = [row for row in rows if row["status"] not in AGENT_RUN_TERMINAL_STATUSES]
                if nonterminal:
                    details = ", ".join(f"{row['id']}={row['status']}" for row in nonterminal)
                    raise RuntimeError(f"test Run is not terminal: {details}")
                if not rows:
                    return
                await asyncio.sleep(0.2)
    except TimeoutError:
        raise RuntimeError(f"test Run runtime cleanup did not finish: threads={sorted(thread_ids)}, last={last}") from None
    finally:
        if conn is not None:
            await conn.close()


async def list_test_pending_inputs(thread_ids: set[str]) -> list[tuple[str, str]]:
    """读取目标 Thread 尚未领取的 follow-up Input。"""

    if not thread_ids:
        return []
    conn = await asyncpg.connect(postgres_dsn())
    try:
        rows = await conn.fetch(
            "SELECT thread_id, id FROM agent_inputs WHERE thread_id = ANY($1::text[]) AND kind = 'follow_up' AND status = 'pending' ORDER BY received_seq",
            sorted(thread_ids),
        )
        return [(str(row["thread_id"]), str(row["id"])) for row in rows]
    finally:
        await conn.close()


async def delete_test_session_rows(thread_ids: set[str]) -> None:
    """物理删除已完成测试清理的 Session 及其历史关联行。"""

    if not thread_ids:
        return
    thread_ids_list = sorted(thread_ids)
    conn = await asyncpg.connect(postgres_dsn())
    try:
        async with conn.transaction():
            await _delete_test_session_rows(conn, thread_ids_list)

        await _assert_test_sessions_deleted(conn, thread_ids_list)
    finally:
        await conn.close()


async def delete_test_session_resources(
    workdirs: dict[tuple[str, str], set[str]],
    thread_ids: set[str],
    workdir_project_ids: set[str],
) -> None:
    """先提交测试 Session 删除，再在 Project Owner 锁内清理对应文件。"""

    if not thread_ids:
        return
    thread_ids_list = sorted(thread_ids)
    conn = await asyncpg.connect(postgres_dsn())
    try:
        async with conn.transaction():
            await conn.execute("LOCK TABLE projects IN SHARE MODE")
            await conn.execute("LOCK TABLE sessions IN SHARE MODE")
            await _validate_test_workdirs_exclusive(conn, workdirs, workdir_project_ids)
            await _delete_test_session_rows(conn, thread_ids_list)

        await _assert_test_sessions_deleted(conn, thread_ids_list)
        try:
            async with conn.transaction():
                # 与 linked Project 创建共享同一路径锁；拿锁后再回读 Owner，
                # 保证创建要么先提交并被看见，要么在删除后重新校验目录。
                for uid in sorted({uid for uid, _workdir_path in workdirs}):
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"project-workdir:{uid}",
                    )
                await conn.execute("LOCK TABLE sessions IN SHARE MODE")
                await _validate_test_workdirs_exclusive(conn, workdirs, workdir_project_ids)
                for uid, workdir_path in workdirs:
                    remove_test_workdir(uid, workdir_path)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Test agent_session rows were deleted, but filesystem cleanup failed: {exc}") from exc
    finally:
        await conn.close()


async def _delete_test_session_rows(conn: asyncpg.Connection, thread_ids_list: list[str]) -> None:
    """使用调用方事务删除测试 Session 的完整历史。"""

    session_rows = await conn.fetch(
        "SELECT c.id, c.project_id, p.selection_status FROM sessions c JOIN projects p ON p.id = c.project_id AND p.uid = c.uid WHERE c.thread_id = ANY($1::text[])",
        thread_ids_list,
    )
    session_record_ids = [int(row["id"]) for row in session_rows]
    implicit_project_ids = [str(row["project_id"]) for row in session_rows if row["project_id"] and row["selection_status"] == "implicit"]
    run_rows = await conn.fetch(
        "SELECT id FROM agent_runs WHERE thread_id = ANY($1::text[])",
        thread_ids_list,
    )
    run_ids = [str(row["id"]) for row in run_rows]
    turn_rows = await conn.fetch(
        "SELECT id FROM agent_turns WHERE thread_id = ANY($1::text[])",
        thread_ids_list,
    )
    turn_ids = [str(row["id"]) for row in turn_rows]
    input_rows = await conn.fetch(
        "SELECT id FROM agent_inputs WHERE thread_id = ANY($1::text[])",
        thread_ids_list,
    )
    input_ids = [str(row["id"]) for row in input_rows]
    message_rows = await conn.fetch(
        "SELECT id FROM messages WHERE session_record_id = ANY($1::int[])",
        session_record_ids,
    )
    message_ids = [int(row["id"]) for row in message_rows]

    await conn.execute(
        "DELETE FROM session_cooperation_events WHERE tree_root_thread_id = ANY($1::text[]) OR sender_thread_id = ANY($1::text[]) OR recipient_thread_id = ANY($1::text[])",
        thread_ids_list,
    )
    await conn.execute("DELETE FROM session_cooperation_runtimes WHERE tree_root_thread_id = ANY($1::text[])", thread_ids_list)
    await conn.execute("UPDATE sessions SET created_by_run_id = NULL WHERE id = ANY($1::int[])", session_record_ids)
    await conn.execute(
        "DELETE FROM agent_input_receipts WHERE thread_id = ANY($1::text[])",
        thread_ids_list,
    )
    await conn.execute("DELETE FROM agent_attachments WHERE input_id = ANY($1::text[])", input_ids)
    await conn.execute("DELETE FROM messages WHERE id = ANY($1::int[])", message_ids)
    await conn.execute(
        "UPDATE agent_turns SET current_run_id = NULL, result_run_id = NULL WHERE id = ANY($1::text[])",
        turn_ids,
    )
    await conn.execute("DELETE FROM agent_inputs WHERE id = ANY($1::text[])", input_ids)
    await conn.execute("DELETE FROM agent_runs WHERE id = ANY($1::text[])", run_ids)
    await conn.execute("DELETE FROM agent_turns WHERE id = ANY($1::text[])", turn_ids)
    await conn.execute("DELETE FROM scheduled_agent_runs WHERE thread_id = ANY($1::text[])", thread_ids_list)
    await conn.execute("DELETE FROM sessions WHERE id = ANY($1::int[])", session_record_ids)
    await conn.execute(
        "DELETE FROM projects WHERE id = ANY($1::text[]) AND NOT EXISTS (SELECT 1 FROM sessions WHERE sessions.project_id = projects.id)",
        implicit_project_ids,
    )


async def delete_orphaned_test_projects(owner_uid: str) -> None:
    """删除当前用户无 Session 引用且显式标记的测试 Project。"""

    conn = await asyncpg.connect(postgres_dsn())
    try:
        await conn.execute(
            "DELETE FROM projects WHERE uid = $1 AND left(idempotency_key, char_length($2)) = $2 AND NOT EXISTS (SELECT 1 FROM sessions WHERE sessions.project_id = projects.id)",
            owner_uid,
            TEST_RESOURCE_PREFIX,
        )
    finally:
        await conn.close()


async def _assert_test_sessions_deleted(conn: asyncpg.Connection, thread_ids_list: list[str]) -> None:
    """回读确认目标 Session 已物理删除。"""

    remaining = await conn.fetch(
        "SELECT thread_id FROM sessions WHERE thread_id = ANY($1::text[])",
        thread_ids_list,
    )
    if remaining:
        raise RuntimeError(
            "Test agent_session cleanup left sessions behind: " + ", ".join(sorted(str(row["thread_id"]) for row in remaining))
        )


async def cleanup_test_chat_resources(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    owner_uid: str,
) -> None:
    """删除测试对话、消息/run 历史、Project Workdir 和临时智能体。"""

    try:
        resources = await list_test_session_resources(owner_uid)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to list persisted test agent_session resources: {exc}") from exc

    target_thread_ids = set(resources)
    workdir_targets: dict[tuple[str, str], set[str]] = {}
    for resource in resources.values():
        if not SAFE_THREAD_ID.fullmatch(resource.thread_id):
            raise RuntimeError(f"Test agent_session cleanup received an unsafe thread id: {resource.thread_id!r}")
        if resource.workdir_path:
            _resolve_test_workdir(resource.uid, resource.workdir_path)
            workdir_targets.setdefault((resource.uid, resource.workdir_path), set()).add(resource.project_id)

    workdir_project_ids = {project_id for project_ids in workdir_targets.values() for project_id in project_ids}
    await validate_test_workdirs_exclusive(workdir_targets, workdir_project_ids)
    await validate_test_runs_terminal(target_thread_ids)

    for thread_id, input_id in await list_test_pending_inputs(target_thread_ids):
        cancel_response = await client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            headers={**headers, "Idempotency-Key": f"cleanup:{input_id}"},
            json={"events": [{"type": "yuxi.session.input.cancel_input", "input_id": input_id}]},
        )
        if cancel_response.status_code not in {200, 202}:
            raise RuntimeError(f"Failed to cancel pending test Input {input_id}: {cancel_response.text}")

    remaining_inputs = await list_test_pending_inputs(target_thread_ids)
    if remaining_inputs:
        raise RuntimeError(
            "Test agent_session cleanup left pending Inputs behind: " + ", ".join(input_id for _, input_id in remaining_inputs)
        )
    await validate_test_runs_terminal(target_thread_ids)

    for resource in resources.values():
        if resource.status != "active":
            continue
        archive_response = await client.post(f"/api/v1/agents/sessions/{resource.thread_id}/archive", headers=headers)
        if archive_response.status_code != 200:
            raise RuntimeError(f"Failed to archive persisted test Thread {resource.thread_id}: {archive_response.text}")

    await delete_test_session_resources(workdir_targets, target_thread_ids, workdir_project_ids)
    await delete_orphaned_test_projects(owner_uid)

    failures: list[str] = []
    agents_response = await client.get(
        "/api/agent",
        headers=headers,
    )
    if agents_response.status_code != 200:
        failures.append(f"Failed to list E2E agents for cleanup: {agents_response.text}")
    else:
        payload = agents_response.json()
        agents = payload.get("agents") if isinstance(payload, dict) else None
        if not isinstance(agents, list):
            failures.append("Test agent cleanup response is missing an agents list")
        else:
            for agent in agents:
                if not _is_e2e_agent(agent, owner_uid):
                    continue
                agent_slug = agent.get("slug") or agent.get("agent_id") or agent.get("id")
                if not agent_slug:
                    failures.append("Test agent cleanup entry is missing agent slug")
                    continue
                delete_response = await client.delete(f"/api/agent/{agent_slug}", headers=headers)
                if delete_response.status_code not in {200, 404}:
                    failures.append(f"Failed to delete test agent {agent_slug}: {delete_response.text}")

    if failures:
        raise RuntimeError("; ".join(failures))


async def cleanup_pytest_knowledge_resources(
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> None:
    """通过公开 API 删除 pytest 前缀的评估资源和知识库。"""

    list_response = await client.get("/api/knowledge/knowledge-bases", headers=headers)
    if list_response.status_code != 200:
        raise RuntimeError(f"Failed to list knowledge bases for cleanup: {list_response.text}")

    payload = list_response.json()
    if payload.get("message"):
        raise RuntimeError(f"Failed to list knowledge bases for cleanup: {payload['message']}")

    knowledge_bases = payload.get("knowledge_bases")
    if not isinstance(knowledge_bases, list):
        raise RuntimeError("Knowledge base cleanup response is missing a knowledge_bases list")

    failures: list[str] = []
    for knowledge_base in knowledge_bases:
        kb_id = knowledge_base.get("kb_id") if isinstance(knowledge_base, dict) else None
        if not kb_id:
            failures.append("Knowledge base cleanup entry is missing kb_id")
            continue

        resource_specs = (
            (f"/api/evaluation/knowledge-bases/{kb_id}/runs", "run_id", f"/api/evaluation/knowledge-bases/{kb_id}/runs"),
            (f"/api/evaluation/knowledge-bases/{kb_id}/datasets", "dataset_id", "/api/evaluation/datasets"),
        )
        for list_path, id_field, delete_prefix in resource_specs:
            response = await client.get(list_path, headers=headers)
            if response.status_code != 200:
                failures.append(f"Failed to list evaluation resources for {kb_id}: {response.text}")
                continue

            resources = response.json().get("data")
            if not isinstance(resources, list):
                failures.append(f"Evaluation cleanup response for {kb_id} is missing a data list")
                continue

            for resource in resources:
                if not isinstance(resource, dict) or not _is_pytest_resource(resource.get("name")):
                    continue
                resource_id = resource.get(id_field)
                if not resource_id:
                    failures.append(f"Evaluation cleanup resource for {kb_id} is missing {id_field}")
                    continue

                delete_response = await client.delete(f"{delete_prefix}/{resource_id}", headers=headers)
                if delete_response.status_code not in {200, 404}:
                    failures.append(f"Failed to delete evaluation resource {resource_id}: {delete_response.text}")

    for knowledge_base in knowledge_bases:
        if not isinstance(knowledge_base, dict) or not _is_pytest_resource(knowledge_base.get("name")):
            continue
        kb_id = knowledge_base.get("kb_id")
        if not kb_id:
            continue

        delete_response = await client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=headers)
        if delete_response.status_code not in {200, 404}:
            failures.append(f"Failed to delete knowledge knowledge_base {kb_id}: {delete_response.text}")

    if failures:
        raise RuntimeError("; ".join(failures))
