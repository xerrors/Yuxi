"""个人空间最近交付物的 PostgreSQL、真实文件与 HTTP 集成验证。"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from test.integration.api.helpers import create_default_agent_thread, delete_created_threads, get_thread_workdir_path
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.extensions.tools.builtin.present_artifacts import present_artifacts
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.runtime_paths import runtime_workdir_path, workspace_scope_from_runtime_path
from yuxi.shared.datetime import format_utc_datetime, utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_recent_artifacts_follow_successful_presentations_and_live_visibility(
    test_client, standard_user, admin_headers, postgres_sessions
):
    """按工具结束时间去重，忽略未交付和已删除文件，并复查来源会话权限。"""
    headers = standard_user["headers"]
    uid = standard_user["user"]["uid"]
    url = "/api/agent/artifacts/recent"
    thread_id = await create_default_agent_thread(test_client, headers, title_prefix="recent-deliverables")
    project_id = None
    saved_scope = None
    try:
        workdir = await get_thread_workdir_path(thread_id)
        workspace = Workspace(uid)
        root = runtime_workdir_path(workdir)
        runtime = SimpleNamespace(context=SimpleNamespace(uid=uid, workdir_path=root, workdir_relative_path=workdir))
        paths = {name: f"{root}/{name}.md" for name in ("old", "new", "removed", "unpresented", "failed")}
        for name in paths:
            workspace.replace_authorized_file(f"/{workdir}/{name}.md", name.encode())
        returned = present_artifacts.func(filepaths=[paths["old"], paths["new"], paths["removed"]], runtime=runtime, tool_call_id="1")
        assert returned.update["artifacts"] == [paths["old"], paths["new"], paths["removed"]]

        now = utc_now()
        async with postgres_sessions() as db:
            thread = await db.scalar(select(Session).where(Session.thread_id == thread_id))
            project = await db.get(Project, thread.project_id)
            project.selection_status = "selectable"
            project_id = project.id
            turn = AgentTurn(id=str(uuid4()), thread_id=thread_id, uid=uid, status="completed")
            db.add(turn)
            await db.flush()
            run = AgentRun(
                id=str(uuid4()),
                turn_id=turn.id,
                thread_id=thread_id,
                runtime_scope_id=thread_id,
                session_record_id=thread.id,
                agent_slug=thread.agent_id,
                uid=uid,
                status="completed",
            )
            db.add(run)
            await db.flush()
            turn.result_run_id = turn.current_run_id = run.id
            for offset, tool, status, files in [
                (0, "present_artifacts", "completed", returned.update["artifacts"]),
                (1, "present_artifacts", "completed", [paths["new"]]),
                (2, "present_artifacts", "failed", [paths["failed"]]),
                (3, "write_file", "completed", [paths["unpresented"]]),
            ]:
                db.add(
                    Message(
                        session_record_id=thread.id,
                        turn_id=turn.id,
                        run_id=run.id,
                        role="tool",
                        content="",
                        message_type="tool_audit",
                        execution_status=status,
                        finished_at=now + timedelta(seconds=offset),
                        extra_metadata={"tool_name": tool, "input": {"filepaths": files}},
                    )
                )
            await db.commit()

        workspace.delete_authorized_path(f"/{workdir}/removed.md", root="/")
        response = await test_client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert [(item["path"], item["presented_at"]) for item in items] == [
            (paths["new"], format_utc_datetime(now + timedelta(seconds=1))),
            (paths["old"], format_utc_datetime(now)),
        ]
        assert items[0]["name"] == "new.md"
        assert items[0]["thread_id"] == thread_id
        preview = await test_client.get(f"/api/v1/agents/sessions/{thread_id}/artifacts/{paths['new'].lstrip('/')}", headers=headers)
        assert preview.status_code == 200 and preview.content == b"new"
        other = await test_client.get(url, headers=admin_headers)
        assert other.status_code == 200 and all(item["thread_id"] != thread_id for item in other.json()["items"])

        archived = await test_client.delete(f"/api/projects/{project_id}", headers=headers)
        assert archived.status_code == 200 and archived.json()["archived_threads"] == 1, archived.text
        overview = await test_client.get(url, headers=headers)
        assert overview.status_code == 200 and overview.json()["items"] == items
        preview = await test_client.get(f"/api/v1/agents/sessions/{thread_id}/artifacts/{paths['new'].lstrip('/')}", headers=headers)
        assert preview.status_code == 200 and preview.content == b"new"

        saved = await test_client.post(
            f"/api/v1/agents/sessions/{thread_id}/artifacts/save",
            headers=headers,
            json={"path": paths["new"], "destination_path": "/saved_artifacts"},
        )
        assert saved.status_code == 200, saved.text
        artifact = saved.json()
        saved_scope = workspace_scope_from_runtime_path(artifact["saved_path"])
        async with postgres_sessions() as db:
            db.add(
                Message(
                    session_record_id=run.session_record_id,
                    turn_id=run.turn_id,
                    run_id=run.id,
                    role="tool",
                    content="",
                    message_type="tool_audit",
                    execution_status="completed",
                    finished_at=now + timedelta(seconds=4),
                    extra_metadata={"tool_name": "present_artifacts", "input": {"filepaths": [artifact["saved_path"]]}},
                )
            )
            await db.commit()
        removed = await test_client.delete("/api/workspace/file", headers=headers, params={"path": f"/{workdir}"})
        assert removed.status_code == 200, removed.text
        missing = await test_client.get(f"/api/v1/agents/sessions/{thread_id}/artifacts/{paths['new'].lstrip('/')}", headers=headers)
        assert missing.status_code == 404, missing.text
        workspace.replace_authorized_file(f"/{workdir}", b"replacement")
        try:
            old_url = f"/api/v1/agents/sessions/{thread_id}/artifacts/{paths['new'].lstrip('/')}"
            download_missing = await test_client.get(old_url, headers=headers)
            assert download_missing.status_code == 404, download_missing.text
            save_missing = await test_client.post(
                f"/api/v1/agents/sessions/{thread_id}/artifacts/save",
                headers=headers,
                json={"path": paths["new"], "destination_path": "/saved_artifacts"},
            )
            assert save_missing.status_code == 404, save_missing.text
        finally:
            workspace.delete_authorized_path(f"/{workdir}", root="/")
        download = await test_client.get(artifact["saved_artifact_url"], headers=headers)
        assert download.status_code == 200 and download.content == b"new"
        overview = await test_client.get(url, headers=headers)
        assert overview.status_code == 200
        assert [item["path"] for item in overview.json()["items"]] == [artifact["saved_path"]]

        async with postgres_sessions() as db:
            thread = await db.scalar(select(Session).where(Session.thread_id == thread_id))
            thread.app_id = "isolated-app"
            await db.commit()
        hidden = await test_client.get(url, headers=headers)
        assert hidden.status_code == 200 and hidden.json()["items"] == []
        async with postgres_sessions() as db:
            thread = await db.scalar(select(Session).where(Session.thread_id == thread_id))
            thread.app_id = None
            thread.status = "deleted"
            await db.commit()
        deleted = await test_client.get(url, headers=headers)
        assert deleted.status_code == 200 and deleted.json()["items"] == []
    finally:
        if saved_scope is not None:
            removed = await test_client.delete("/api/workspace/file", headers=headers, params={"path": saved_scope})
            assert removed.status_code == 200, removed.text
        if project_id is not None:
            # 通用清理器只接收测试创建的隐式项目。
            async with postgres_sessions() as db:
                project = await db.get(Project, project_id)
                project.selection_status = "implicit"
                await db.commit()
        await delete_created_threads(thread_id)
