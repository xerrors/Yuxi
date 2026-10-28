"""从真实宿主文件证明 Backend 路由与现有工具结果协议。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.agents.runtime.sandbox import backend as backend_module
from yuxi.modules.agents.runtime.sandbox.backend import UserSandboxBackend, create_agent_composite_backend
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir
from yuxi.modules.workspace.paths import ensure_user_workspace, user_workspace_dir
from yuxi.modules.workspace.runtime_paths import SHARED_SKILLS_PATH, WORKSPACE_PATH


@pytest.fixture
def backend(tmp_path, monkeypatch):
    """执行端一旦被访问即失败，文件结果由磁盘回读核验。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "users"))
    monkeypatch.setenv("YUXI_SKILL_PROJECTION_DIR", str(tmp_path / "skills"))
    ensure_user_workspace("one")
    ensure_user_workspace("two")
    get_user_skills_root_dir("one").mkdir(parents=True)

    class NoVM:
        def __getattr__(self, key):
            raise AssertionError(f"local files must not call VM: {key}")

    novm = NoVM()
    monkeypatch.setattr(backend_module, "get_sandbox_provider", lambda: novm)
    return UserSandboxBackend(uid="one")


def test_workspace_read_write_edit_and_search_do_not_wake_vm(backend):
    """写入、分页、编辑和搜索操作同一份字节。"""
    path = f"{WORKSPACE_PATH}/project/a.txt"
    assert backend.write(path, "first\nneedle\nlast\n").error is None
    root = user_workspace_dir("one")
    assert (root / "project/a.txt").read_bytes() == b"first\nneedle\nlast\n"
    page = backend.read(path, offset=1, limit=1)
    assert page.file_data["content"] == "needle\n"
    assert (page.start_line, page.end_line, page.next_offset) == (2, 2, 2)
    assert backend.edit(path, "needle", "changed").occurrences == 1
    assert (root / "project/a.txt").read_bytes() == b"first\nchanged\nlast\n"
    assert backend.grep("changed", f"{WORKSPACE_PATH}/project").matches == [{"path": path, "line": 2, "text": "changed"}]
    assert [item["path"] for item in backend.glob("*.txt", f"{WORKSPACE_PATH}/project").matches] == [path]
    assert backend.read("/home/user/workspace/project/a.txt").file_data["content"].startswith("first")


def test_workspace_write_overwrites_existing_file_without_following_links(backend):
    """write_file 按工具契约整体覆盖已存在文件，且不穿过最终组件链接写目标。"""
    path = f"{WORKSPACE_PATH}/exists.txt"
    assert backend.write(path, "original").error is None
    assert backend.write(path, "replacement").error is None
    assert (user_workspace_dir("one") / "exists.txt").read_text() == "replacement"
    # 最终组件是链接时只替换链接本身，不访问链接目标字节。
    root = user_workspace_dir("one")
    (root / "target.txt").write_text("keep-me")
    (root / "alias.txt").symlink_to("target.txt")
    alias = f"{WORKSPACE_PATH}/alias.txt"
    assert backend.write(alias, "rewritten").error is None
    assert (root / "target.txt").read_text() == "keep-me"
    assert (root / "alias.txt").is_symlink() is False
    assert (root / "alias.txt").read_text() == "rewritten"


def test_symlink_cannot_escape_to_other_user_and_never_falls_back(backend):
    """恢复 symlink 缺陷会泄露另一用户字节。"""
    other = user_workspace_dir("two")
    (other / "secret.txt").write_text("other-user-secret")
    root = user_workspace_dir("one")
    (root / "escape").symlink_to(other, target_is_directory=True)
    path = f"{WORKSPACE_PATH}/escape/secret.txt"
    assert backend.read(path).error
    assert backend.write(path, "modified").error
    assert (other / "secret.txt").read_text() == "other-user-secret"
    assert backend.glob("*", WORKSPACE_PATH).error is None


def test_shared_skills_read_only_without_vm(backend):
    """授权投影可读且所有模型写入方法拒绝修改。"""
    source = get_user_skills_root_dir("one") / "demo"
    source.mkdir()
    (source / "SKILL.md").write_text("shared")
    path = f"{SHARED_SKILLS_PATH}/demo/SKILL.md"
    assert backend.read(path).file_data["content"] == "shared"
    assert backend.edit(path, "shared", "modified").error
    assert backend.upload_files([(path, b"modified")])[0].error
    assert (source / "SKILL.md").read_text() == "shared"


def test_artifact_must_be_workspace_regular_file(backend):
    """展示 Home 文件不会隐式恢复 VM。"""
    assert not backend.regular_file_exists("/home/user/.config/example")
    path = f"{WORKSPACE_PATH}/result.txt"
    backend.write(path, "result")
    assert backend.regular_file_exists(path)
    (user_workspace_dir("one") / "link.txt").symlink_to("result.txt")
    assert not backend.regular_file_exists(f"{WORKSPACE_PATH}/link.txt")


def test_composite_keeps_artifacts_root_and_user_identity(backend):
    """Backend 装配仍向摘要和文件工具提供相同产物根。"""
    context = SimpleNamespace(uid="one", runtime_scope_id="scope", workdir_relative_path="projects/11111111-1111-4111-8111-111111111111")
    result = create_agent_composite_backend(context)
    assert result.default.id == "one"
    assert result.artifacts_root == f"{WORKSPACE_PATH}/{context.workdir_relative_path}/outputs"


@pytest.mark.parametrize("path", ["/home/user/workspace/../two/secret", "https://host/a", "/home/user/workspace/a\\b"])
def test_invalid_path_has_no_filesystem_side_effect(backend, path):
    """词法越界和对象 URL 不进入文件执行边界。"""
    assert backend.write(path, "invalid").error


@pytest.mark.parametrize("local", [False, True], ids=["guest", "workspace"])
@pytest.mark.parametrize("count, limit", [(0, 2), (1, 2), (3, 2), (1001, None), (2, 2), (1000, None)])
def test_search_reports_truncation_from_actual_file(backend, tmp_path, monkeypatch, count, limit, local):
    """挂载文件与 Guest 脚本的超限、完整结果保持一致。"""
    import subprocess

    source = tmp_path / "guest.txt"
    source.write_text("needle\n" * count)

    def execute(command):
        """在独立临时目录执行 Guest 脚本并回读标准输出。"""
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        return SimpleNamespace(exit_code=result.returncode, output=result.stdout, truncated=False)

    monkeypatch.setattr(backend, "execute", execute)
    path = str(source)
    if local:
        (user_workspace_dir("one") / "search.txt").write_bytes(source.read_bytes())
        path = f"{WORKSPACE_PATH}/search.txt"
    result = backend.grep("needle", path, max_count=limit)
    assert result.error is None
    assert len(result.matches) == min(count, limit or 1000)
    assert result.truncated is (count > (limit or 1000))
    assert [match["line"] for match in result.matches] == list(range(1, min(count, limit or 1000) + 1))
    assert all(match["text"] == "needle" for match in result.matches)


@pytest.mark.parametrize("path", ["/home/user/workspace/search.txt", "/tmp/search.txt"])
@pytest.mark.parametrize("limit", [0, -1])
def test_search_rejects_nonpositive_limit_before_io(backend, path, limit):
    """非法搜索上限在两条路径均明确失败。"""
    result = backend.grep("needle", path, max_count=limit)
    assert result.error and "max_count" in result.error


@pytest.mark.parametrize("name", ["_GUEST_FILE_SCRIPT", "_GUEST_SEARCH_SCRIPT"])
def test_embedded_guest_scripts_compile(name):
    """内嵌 Guest 脚本的编辑缺陷必须在编译层失败，而不是运行时让文件工具整体失效。"""
    compile(getattr(backend_module, name), name, "exec")


@pytest.mark.parametrize("local", [False, True], ids=["guest", "workspace"])
def test_search_skips_files_over_per_file_budget(backend, tmp_path, monkeypatch, local):
    """恢复无大小上限的本地扫描时，两条路径都必须在正确原因上跳过超预算文件。"""
    import subprocess

    monkeypatch.setattr(backend_module, "_SEARCH_MAX_FILE_BYTES", 16)

    def execute(command):
        """在独立进程执行 Guest 脚本并回读标准输出。"""
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        return SimpleNamespace(exit_code=result.returncode, output=result.stdout, truncated=False)

    monkeypatch.setattr(backend, "execute", execute)
    if local:
        root = user_workspace_dir("one")
        (root / "small.txt").write_text("needle\n")
        (root / "big.txt").write_text("needle\n" + "x" * 32)
        result = backend.grep("needle", WORKSPACE_PATH, glob="*.txt")
        assert [match["path"] for match in result.matches] == [f"{WORKSPACE_PATH}/small.txt"]
    else:
        (tmp_path / "big.txt").write_text("needle\n" + "x" * 32)
        result = backend.grep("needle", str(tmp_path), glob="*.txt")
        assert result.matches == []


def test_search_skips_single_file_over_budget(backend, monkeypatch):
    """恢复单文件回退不核对大小时，直接对超预算文件的搜索必须在正确原因上跳过。"""
    monkeypatch.setattr(backend_module, "_SEARCH_MAX_FILE_BYTES", 16)
    (user_workspace_dir("one") / "big.txt").write_text("needle\n" + "x" * 32)
    assert backend.grep("needle", f"{WORKSPACE_PATH}/big.txt").matches == []


def test_transfer_errors_report_actual_cause(backend):
    """路径校验、缺失与只读失败不再折叠成同一种权限语义。"""
    results = backend.upload_files([("https://host/a", b"data")])
    assert results[0].error == "path must start with /"
    results = backend.download_files([f"{WORKSPACE_PATH}/missing.txt"])
    assert results[0].error == "file_not_found"
    results = backend.upload_files([(f"{SHARED_SKILLS_PATH}/demo/SKILL.md", b"modified")])
    assert results[0].error == "permission_denied"


def test_shared_skills_delete_fails_closed_before_upstream_delete(backend, monkeypatch):
    """共享投影删除在到达上游 rm 之前失败（负向：去掉同界检查时 pytest.fail 触发）。"""
    from deepagents.backends.sandbox import BaseSandbox

    def _upstream_must_not_run(self, file_path):
        pytest.fail("越界删除不应到达上游 delete")

    monkeypatch.setattr(BaseSandbox, "delete", _upstream_must_not_run)

    result = backend.delete(f"{SHARED_SKILLS_PATH}/demo/SKILL.md")

    assert result.error


def test_delete_normalizes_path_before_upstream_call(backend):
    """非法路径显式失败，不把相对路径交给上游 rm。"""
    assert backend.delete("relative/does-not-exist").error


@pytest.mark.asyncio
async def test_async_file_tools_delegate_to_native_sync_implementations(backend):
    """异步工具必须委托本类同步实现，而不是执行 BaseSandbox 的沙箱脚本（负向：删掉任一委托即调用上游脚本/文件 API）。"""
    calls: list[str] = []
    for name in ("read", "write", "edit", "ls", "glob", "grep"):
        original = getattr(backend, name)

        def _spy(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        setattr(backend, name, _spy)

    path = f"{WORKSPACE_PATH}/project/async.txt"
    assert (await backend.awrite(path, "first\nneedle\n")).error is None
    assert (await backend.aread(path)).file_data["content"].startswith("first")
    assert (await backend.aedit(path, "needle", "changed")).occurrences == 1
    assert (await backend.als(f"{WORKSPACE_PATH}/project")).entries
    assert (await backend.aglob("*.txt", f"{WORKSPACE_PATH}/project")).matches
    assert (await backend.agrep("changed", f"{WORKSPACE_PATH}/project")).matches

    assert calls == ["write", "read", "edit", "ls", "glob", "grep"]


def test_agent_filesystem_tools_do_not_expose_delete(backend):
    """检查真实注册的模型工具，恢复删除暴露时必须失败。"""
    from yuxi.modules.agents.runtime.middlewares.filesystem import create_agent_filesystem_middleware

    middleware = create_agent_filesystem_middleware(backend=backend)
    names = {tool.name for tool in middleware.tools}
    assert "read_file" in names
    assert "delete" not in names
