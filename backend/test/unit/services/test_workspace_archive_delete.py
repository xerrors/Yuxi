from __future__ import annotations

from types import SimpleNamespace

import pytest

import yuxi.modules.workspace.services.files as svc
from yuxi.shared.errors import InvalidOperationError


@pytest.mark.asyncio
async def test_personal_workspace_refuses_conversation_archive_but_deletes_tool_media(monkeypatch) -> None:
    """压缩留档目录及其内容拒绝删除；同级 tool_media 仍可删除。"""
    calls = []

    def delete_authorized_path(path, *, root):
        del root
        calls.append(path)

    monkeypatch.setattr(svc, "_workspace_backend", lambda _user: SimpleNamespace(delete_authorized_path=delete_authorized_path))
    user = SimpleNamespace(id="db-id-1", uid="user-1")
    with pytest.raises(InvalidOperationError) as error:
        await svc.delete_workspace_path(
            path="/projects/20261124/sess-a/outputs/conversation_history/summary.md",
            current_user=user,
        )
    assert error.value.code == "conversation_archive_protected"
    assert calls == []
    result = await svc.delete_workspace_path(
        path="/projects/20261124/sess-a/outputs/tool_media/shot.png",
        current_user=user,
    )
    assert result["success"] is True
    assert calls == ["/projects/20261124/sess-a/outputs/tool_media/shot.png"]
