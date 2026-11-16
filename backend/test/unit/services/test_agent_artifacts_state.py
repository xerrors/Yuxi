from contextlib import asynccontextmanager
from importlib import import_module

import pytest
from yuxi.modules.agents.runtime.sandbox import backend as sandbox_backend
from yuxi.modules.agents.runtime.state import merge_artifacts
from yuxi.modules.extensions.tools.builtin.present_artifacts import (
    PresentArtifactsInput,
    _normalize_presented_artifact_path,
    present_artifacts,
)
from yuxi.modules.workspace.runtime_paths import CONVERSATION_HISTORY_DIR_NAME, LARGE_TOOL_RESULTS_DIR_NAME
from yuxi.shared.errors import PermissionDeniedError


def _runtime_with_thread(thread_id: str, uid: str = "user-1"):
    context = type(
        "RuntimeContext",
        (),
        {
            "thread_id": thread_id,
            "runtime_scope_id": thread_id,
            "workdir_relative_path": "projects/11111111-1111-4111-8111-111111111111",
            "workdir_path": "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111",
            "uid": uid,
            "run_id": "run-1",
            "worker_id": "worker-1",
        },
    )()
    return type("RuntimeStub", (), {"context": context})()


def _stub_output_exists(monkeypatch: pytest.MonkeyPatch, exists: bool = True) -> None:
    """替换文件存在性与已由服务测试覆盖的保存用例。"""
    class FakeBackend:
        def __init__(self, **kwargs):
            pass

        def regular_file_exists(self, _path: str) -> bool:
            return exists

    monkeypatch.setattr(sandbox_backend, "UserSandboxBackend", FakeBackend)
    tool_module = import_module("yuxi.modules.extensions.tools.builtin.present_artifacts")

    @asynccontextmanager
    async def database_session():
        """提供本测试不消费的数据库句柄。"""
        yield object()

    async def save(**kwargs):
        """确认保存用例收到当前执行身份与显式交付路径。"""
        assert kwargs["uid"] == "user-1"
        assert kwargs["run_id"] == "run-1"
        assert kwargs["worker_id"] == "worker-1"
        assert kwargs["operation_id"] == "call-1"
        return []

    monkeypatch.setattr(tool_module.pg_manager, "get_async_session_context", database_session)
    monkeypatch.setattr(tool_module, "save_run_deliverables", save)


def test_merge_artifacts_deduplicates_and_preserves_order():
    assert merge_artifacts(
        ["/home/user/workspace/outputs/a.md"],
        ["/home/user/workspace/outputs/a.md", "/home/user/workspace/outputs/b.md"],
    ) == [
        "/home/user/workspace/outputs/a.md",
        "/home/user/workspace/outputs/b.md",
    ]


def test_merge_artifacts_rejects_legacy_objects():
    """产物 state 只接受字符串，旧对象不能被悄悄转换。"""
    with pytest.raises(TypeError):
        merge_artifacts([], [{"path": "/b.md", "type": "file"}])
    assert merge_artifacts(None, None) == []
    assert merge_artifacts(["/a.png"], None) == ["/a.png"]


@pytest.mark.asyncio
async def test_present_artifacts_registers_paths_without_presentation_type(monkeypatch):
    """PNG 与其他文件共用路径登记，工具 schema 不提供展示类型。"""
    _stub_output_exists(monkeypatch)
    path = "/home/user/workspace/outputs/screenshot.png"
    assert set(PresentArtifactsInput.model_json_schema()["properties"]) == {"filepaths"}
    result = await present_artifacts.coroutine(filepaths=[path], runtime=_runtime_with_thread("thread-1"), tool_call_id="call-1")
    assert result.update["artifacts"] == [path]
    assert result.update["messages"][0].content == "已将交付物展示给用户，并保存到我的成果"


@pytest.mark.asyncio
async def test_present_artifacts_does_not_register_missing_image(monkeypatch):
    """图片声明仍需经过普通文件边界，失败结果明确标记错误。"""
    _stub_output_exists(monkeypatch, exists=False)
    result = await present_artifacts.coroutine(
        filepaths=["/home/user/workspace/outputs/missing.png"],
        runtime=_runtime_with_thread("thread-1"),
        tool_call_id="call-1",
    )
    assert "artifacts" not in result.update
    assert result.update["messages"][0].status == "error"
    assert "文件不存在或不是普通文件" in result.update["messages"][0].content


@pytest.mark.asyncio
async def test_present_artifacts_does_not_claim_success_when_save_is_denied(monkeypatch):
    """保存权限拒绝时不得显示文件卡片或宣称成果已经保存。"""
    _stub_output_exists(monkeypatch)

    async def denied(**kwargs):
        """模拟来源执行或共享 Skill 授权被撤销。"""
        raise PermissionDeniedError("成果保存无权限")

    tool_module = import_module("yuxi.modules.extensions.tools.builtin.present_artifacts")
    monkeypatch.setattr(tool_module, "save_run_deliverables", denied)
    result = await present_artifacts.coroutine(
        filepaths=["/home/user/workspace/outputs/report.md"],
        runtime=_runtime_with_thread("thread-1"),
        tool_call_id="call-1",
    )
    assert "artifacts" not in result.update
    assert result.update["messages"][0].status == "error"
    assert "成果保存无权限" in result.update["messages"][0].content


@pytest.mark.asyncio
async def test_present_artifacts_empty_publication_needs_no_database(monkeypatch):
    """空交付保持合法，不为没有成果的调用触发数据库副作用。"""
    _stub_output_exists(monkeypatch)

    async def unexpected_save(**kwargs):
        """空列表触发保存即表示产生了无必要的副作用。"""
        pytest.fail("空交付不应调用保存用例")

    tool_module = import_module("yuxi.modules.extensions.tools.builtin.present_artifacts")
    monkeypatch.setattr(tool_module, "save_run_deliverables", unexpected_save)
    result = await present_artifacts.coroutine(filepaths=[], runtime=_runtime_with_thread("thread-1"), tool_call_id="call-1")
    assert result.update["artifacts"] == []
    assert result.update["messages"][0].status == "success"


def test_normalize_presented_artifact_path_rejects_host_path():
    with pytest.raises(ValueError, match="可见范围"):
        _normalize_presented_artifact_path(
            "saves/threads/thread-1/user-data/outputs/report.md",
            _runtime_with_thread("thread-1"),
        )


def test_normalize_presented_artifact_path_accepts_virtual_path(monkeypatch: pytest.MonkeyPatch):
    thread_id = "artifacts-virtual-path"
    _stub_output_exists(monkeypatch)

    normalized = _normalize_presented_artifact_path(
        "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/outputs/summary.txt",
        _runtime_with_thread(thread_id),
    )

    assert normalized == "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/outputs/summary.txt"


def test_normalize_presented_artifact_path_accepts_any_visible_regular_file(monkeypatch: pytest.MonkeyPatch):
    thread_id = "artifacts-reject-path"
    _stub_output_exists(monkeypatch)

    assert (
        _normalize_presented_artifact_path(
            "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/uploads/note.txt",
            _runtime_with_thread(thread_id),
        )
        == "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/uploads/note.txt"
    )


def test_normalize_presented_artifact_path_does_not_special_case_internal_names(monkeypatch: pytest.MonkeyPatch):
    thread_id = "artifacts-reject-internal"
    _stub_output_exists(monkeypatch)

    for dir_name in [LARGE_TOOL_RESULTS_DIR_NAME, CONVERSATION_HISTORY_DIR_NAME, "large_tool_history"]:
        path = f"/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/outputs/{dir_name}/stage.txt"
        assert _normalize_presented_artifact_path(path, _runtime_with_thread(thread_id)) == path
