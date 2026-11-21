"""验证 HTTP 上传适配、真实长度与框架边界。"""

import ast
from io import BytesIO
from pathlib import Path
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from yuxi.api.uploads import prepare_upload_files, read_upload_with_limit
from yuxi.api.routers.workspace import workspace as workspace_router
from yuxi.api.routers.workspace import viewer as viewer_router
from yuxi.shared.files import FileInput


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"12345"])
async def test_read_upload_accepts_limit_and_rewinds(content):
    """读取从头开始，恰好等于限制时保留全部内容。"""
    upload = UploadFile(file=BytesIO(content), filename="file.txt")
    await upload.seek(len(content))
    assert await read_upload_with_limit(upload, max_size_bytes=5, too_large_message="too large", chunk_size=2) == content


@pytest.mark.asyncio
async def test_read_upload_rejects_cumulative_size_despite_false_metadata():
    """大小以累计读取字节为准，伪造 metadata 不可绕过。"""
    upload = UploadFile(file=BytesIO(b"123456"), filename="file.txt", size=1)
    with pytest.raises(ValueError, match="too large"):
        await read_upload_with_limit(upload, max_size_bytes=5, too_large_message="too large", chunk_size=2)


@pytest.mark.asyncio
async def test_prepare_upload_borrows_disk_stream_without_reading_or_closing(monkeypatch):
    """大文件复用请求暂存流，适配后没有文件内容读取或复制。"""
    with SpooledTemporaryFile(max_size=1, mode="w+b") as source:
        source.write(b"12345")
        upload = UploadFile(file=source, filename="file.txt", size=999)

        def reject_read(*args):
            """适配阶段读取内容会恢复全量内存读取风险。"""
            raise AssertionError("preparation must not read file content")

        with monkeypatch.context() as patch:
            patch.setattr(source, "read", reject_read)
            [prepared] = await prepare_upload_files([upload], max_size_bytes=5, too_large_message="too large")
        assert isinstance(prepared, FileInput)
        assert prepared.source is source
        assert source.tell() == 0
        assert prepared.source.read() == b"12345"
        assert not source.closed
    assert source.closed


@pytest.mark.asyncio
async def test_prepare_upload_rejects_actual_oversize_and_keeps_request_ownership():
    """服务接收前拒绝超限暂存文件，关闭仍由请求负责。"""
    source = BytesIO(b"123456")
    upload = UploadFile(file=source, filename="file.txt", size=1)
    with pytest.raises(ValueError, match="too large"):
        await prepare_upload_files([upload], max_size_bytes=5, too_large_message="too large")
    assert source.tell() == 0
    assert not source.closed


@pytest.mark.parametrize(
    "module,router,limit,service,data",
    [
        (
            workspace_router,
            "workspace",
            "MAX_WORKSPACE_UPLOAD_SIZE_BYTES",
            "upload_workspace_files",
            {"parent_path": "/"},
        ),
        (
            viewer_router,
            "filesystem_router",
            "MAX_VIEWER_UPLOAD_BYTES",
            "upload_viewer_files",
            {"parent_path": "/", "thread_id": "thread-1"},
        ),
    ],
)
@pytest.mark.parametrize("content,status", [(b"12345", 200), (b"123456", 400)])
def test_workspace_routes_convert_multipart_and_reject_oversize(monkeypatch, module, router, limit, service, data, content, status):
    """实际 multipart 经路由适配，服务只能得到中立文件输入。"""
    app = FastAPI()
    app.include_router(getattr(module, router))
    app.dependency_overrides[module.get_required_user] = lambda: SimpleNamespace(uid="user-1")
    if module in (viewer_router, workspace_router):
        app.dependency_overrides[module.get_db] = lambda: object()
    monkeypatch.setattr(module, limit, 5)
    observed = []

    async def consume(*, files, **kwargs):
        """读取服务实际收到的内容，用协议结果证明适配完整。"""
        assert all(isinstance(file, FileInput) for file in files)
        observed.extend(files)
        return {"content": files[0].source.read().decode(), "filename": files[0].filename}

    monkeypatch.setattr(module, service, consume)
    with TestClient(app) as client:
        response = client.post(f"{getattr(module, router).prefix}/upload", data=data, files={"files": ("file.txt", content)})
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json() == {"content": "12345", "filename": "file.txt"}
        assert observed[0].source.closed
    else:
        assert observed == []
        assert "detail" in response.json()


def test_workspace_upload_route_passes_relative_paths_in_file_order(monkeypatch):
    """multipart 相对路径必须与文件列表逐项对齐后交给服务层。"""
    app = FastAPI()
    app.include_router(workspace_router.workspace)
    app.dependency_overrides[workspace_router.get_required_user] = lambda: SimpleNamespace(uid="user-1")
    app.dependency_overrides[workspace_router.get_db] = lambda: SimpleNamespace()
    observed = {}

    async def consume(*, parent_path, files, relative_paths, current_user, db):
        """记录协议边界实际收到的最小事实。"""
        observed.update(
            parent_path=parent_path,
            filenames=[file.filename for file in files],
            relative_paths=relative_paths,
        )
        return {"success": True}

    monkeypatch.setattr(workspace_router, "upload_workspace_files", consume)
    with TestClient(app) as client:
        response = client.post(
            f"{workspace_router.workspace.prefix}/upload",
            data={"parent_path": "/", "relative_paths": ["root/a.txt", "root/sub/b.txt"]},
            files=[("files", ("a.txt", b"a")), ("files", ("b.txt", b"b"))],
        )

    assert response.status_code == 200, response.text
    assert observed == {
        "parent_path": "/",
        "filenames": ["a.txt", "b.txt"],
        "relative_paths": ["root/a.txt", "root/sub/b.txt"],
    }


def test_uploadfile_is_only_used_in_api_layer():
    """恢复任何非 API 层 UploadFile 使用都会违反协议边界。"""
    root = Path(__file__).resolve().parents[3] / "yuxi"
    violations = []
    for path in root.rglob("*.py"):
        if path.relative_to(root).parts[0] == "api":
            continue
        tree = ast.parse(path.read_text())
        if any(
            (isinstance(node, ast.alias) and node.name.endswith("UploadFile"))
            or (isinstance(node, ast.Name) and node.id == "UploadFile")
            or (isinstance(node, ast.Attribute) and node.attr == "UploadFile")
            for node in ast.walk(tree)
        ):
            violations.append(str(path.relative_to(root)))
    assert violations == []
    assert not (root / "infrastructure/uploads.py").exists()
