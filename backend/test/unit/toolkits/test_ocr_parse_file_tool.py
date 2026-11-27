from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yuxi.modules.documents.service as ocr_service
from yuxi.modules.extensions.tools.builtin.ocr_parse_file import ocr_parse_file

builtin_tools = importlib.import_module("yuxi.modules.extensions.tools.builtin.ocr_parse_file")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    """工具输出使用真实且隔离的 UserWorkspace 文件边界。"""
    root = tmp_path / "workspace"
    (root / _runtime().context.workdir_relative_path).mkdir(parents=True)
    monkeypatch.setattr("yuxi.modules.workspace.filesystem.user_workspace_dir", lambda _uid: root)
    return root


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["download", "preview"])
async def test_cancelled_ocr_waits_for_local_io_without_publishing(workspace_root, monkeypatch, stage):
    """下载或预览期间取消，临时目录等线程结束才释放且不发布产物。"""
    import asyncio
    import threading

    _mock_system_options(monkeypatch)
    source = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/scan.png"
    started, release = threading.Event(), threading.Event()
    temporary_paths = []

    def wait(path):
        temporary_paths.append(Path(path))
        started.set()
        assert release.wait(5)
        assert Path(path).parent.is_dir()

    class Backend:
        def __init__(self, **kwargs):
            pass

        def download_authorized_file_to_path(self, path, target, limit):
            if stage == "download":
                wait(target)
            Path(target).write_bytes(b"image")

    async def parse(source, output_dir, **kwargs):
        return _write_parse_result(output_dir, "text")

    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if stage == "preview" and path.name == "document.md":
            wait(path)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(builtin_tools, "UserSandboxBackend", Backend)
    monkeypatch.setattr(ocr_service, "parse", parse)
    monkeypatch.setattr(Path, "read_text", read)
    task = asyncio.create_task(ocr_parse_file.coroutine(file_path=source, runtime=_runtime(), ocr_engine="disable"))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert temporary_paths[0].parent.is_dir()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not temporary_paths[0].parent.exists()
    assert list(workspace_root.rglob("document.md")) == []


def _patch_sandbox_backend(monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]):
    class FakeBackend:
        def __init__(self, **kwargs):
            self.scope = kwargs

        def download_authorized_file_to_path(self, path, target_path, max_bytes):
            if path not in files:
                raise ValueError("not a regular file")
            content = files[path]
            assert len(content) <= max_bytes
            Path(target_path).write_bytes(content)
            return len(content)

    monkeypatch.setattr(builtin_tools, "UserSandboxBackend", FakeBackend, raising=False)
    return files


def _runtime(
    *,
    thread_id: str = "thread-1",
    uid: str = "user-1",
) -> SimpleNamespace:
    configurable = {
        "thread_id": thread_id,
        "runtime_scope_id": thread_id,
        "workdir_relative_path": "projects/11111111-1111-4111-8111-111111111111",
        "workdir_path": "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111",
        "uid": uid,
    }
    return SimpleNamespace(
        config={"configurable": configurable},
        context=SimpleNamespace(
            thread_id=thread_id,
            runtime_scope_id=thread_id,
            workdir_relative_path="projects/11111111-1111-4111-8111-111111111111",
            workdir_path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111",
            uid=uid,
        ),
        state={},
    )


@pytest.mark.asyncio
async def test_ocr_parse_file_downloads_and_parses_real_docx(workspace_root, monkeypatch):
    """真实下载要求既有目标文件，解析后的正文必须落入 Workdir。"""
    from docx import Document

    _mock_system_options(monkeypatch)
    # 该用例只触达 Workspace 挂载，执行后端不应被唤醒或要求其部署配置。
    import yuxi.modules.agents.runtime.sandbox.backend as backend_module

    class NoProvider:
        def __getattr__(self, key):
            raise AssertionError(f"workspace-only IO must not wake the provider: {key}")

    monkeypatch.setattr(backend_module, "get_sandbox_provider", lambda: NoProvider())
    source = workspace_root / "projects/11111111-1111-4111-8111-111111111111/uploads/input.docx"
    source.parent.mkdir(parents=True)
    document = Document()
    document.add_paragraph("OCR COPY REGRESSION 文档正文")
    document.save(source)

    result = await ocr_parse_file.coroutine(
        file_path=f"/home/user/workspace/{source.relative_to(workspace_root)}",
        runtime=_runtime(),
        ocr_engine="disable",
    )

    output = workspace_root / result["parsed_path"].removeprefix("/home/user/workspace/")
    assert "OCR COPY REGRESSION 文档正文" in output.read_text()
    assert source.is_file()


@pytest.mark.asyncio
async def test_ocr_parse_file_writes_markdown_to_outputs(tmp_path, workspace_root, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    _mock_system_options(monkeypatch)

    def resolve_engine(engine_id, default_engine):
        del default_engine
        return engine_id

    monkeypatch.setattr(ocr_service, "resolve_ocr_engine_id", resolve_engine)
    thread_id = "thread-1"
    uid = "user-1"
    source_virtual_path = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/scan.png"
    _patch_sandbox_backend(monkeypatch, {source_virtual_path: b"fake image"})
    captured: dict[str, object] = {}

    async def fake_parse_document(source: str, output_dir: Path, params: dict | None = None, db=None) -> str:
        del db
        captured["source"] = source
        captured["params"] = params
        from yuxi.infrastructure.document_parsing import ParseResult

        result = _write_parse_result(output_dir, "![图](images/chart.png)\n识别结果\n" + ("长文本" * 500))
        (output_dir / "images").mkdir()
        (output_dir / "images/chart.png").write_bytes(b"image")
        (output_dir / "metadata.json").write_text("{}")
        (output_dir / "empty").mkdir()
        return ParseResult(output_dir, result.markdown_path, ("images/chart.png",))

    monkeypatch.setattr(ocr_service, "parse", fake_parse_document)

    result = await ocr_parse_file.coroutine(
        file_path=source_virtual_path,
        ocr_engine="mineru_ocr",
        runtime=_runtime(thread_id=thread_id, uid=uid),
    )

    output_virtual_path = result["parsed_path"]
    assert output_virtual_path.startswith("/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/outputs/ocr/scan_")
    assert output_virtual_path.endswith("/document.md")
    output_file = workspace_root / output_virtual_path.removeprefix("/home/user/workspace/")
    markdown = output_file.read_text()
    assert "识别结果" in markdown
    assert (output_file.parent / "images/chart.png").read_bytes() == b"image"
    assert (output_file.parent / "metadata.json").read_text() == "{}"
    assert (output_file.parent / "empty").is_dir()
    assert result["source_path"] == source_virtual_path
    assert result["parsed_path"] == output_virtual_path
    assert result["ocr_engine"] == "mineru_ocr"
    assert result["char_count"] == len(markdown)
    assert result["truncated"] is True
    assert len(result["preview"]) <= 1200
    assert Path(str(captured["source"])).suffix == ".png"
    assert captured["params"] == {"ocr_engine": "mineru_ocr"}


@pytest.mark.asyncio
async def test_ocr_parse_file_uses_default_engine(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    _mock_system_options(monkeypatch)

    def resolve_engine(engine_id, default_engine):
        assert engine_id is None
        assert default_engine == "rapid_ocr"
        return "rapid_ocr"

    monkeypatch.setattr(ocr_service, "resolve_ocr_engine_id", resolve_engine)
    thread_id = "thread-1"
    uid = "user-1"
    source_virtual_path = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/uploads/upload.pdf"
    _patch_sandbox_backend(monkeypatch, {source_virtual_path: b"fake pdf"})
    captured: dict[str, object] = {}

    async def fake_parse_document(source: str, output_dir: Path, params: dict | None = None, db=None) -> str:
        del source, db
        captured["params"] = params
        return _write_parse_result(output_dir, "OCR content")

    monkeypatch.setattr(ocr_service, "parse", fake_parse_document)

    result = await ocr_parse_file.coroutine(
        file_path=source_virtual_path,
        runtime=_runtime(thread_id=thread_id, uid=uid),
    )

    assert result["ocr_engine"] == "rapid_ocr"
    assert captured["params"] == {"ocr_engine": "rapid_ocr"}


@pytest.mark.asyncio
async def test_ocr_parse_file_accepts_disable_for_pdf(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    _mock_system_options(monkeypatch)
    thread_id = "thread-1"
    uid = "user-1"
    source_virtual_path = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/uploads/text-layer.pdf"
    _patch_sandbox_backend(monkeypatch, {source_virtual_path: b"fake pdf"})
    captured: dict[str, object] = {}

    async def fake_parse_document(source: str, output_dir: Path, params: dict | None = None, db=None) -> str:
        del source, db
        captured["params"] = params
        return _write_parse_result(output_dir, "PDF text layer")

    monkeypatch.setattr(ocr_service, "parse", fake_parse_document)

    result = await ocr_parse_file.coroutine(
        file_path=source_virtual_path,
        ocr_engine="disable",
        runtime=_runtime(thread_id=thread_id, uid=uid),
    )

    assert result["ocr_engine"] == "disable"
    assert captured["params"] == {"ocr_engine": "disable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_path",
    [
        "/etc/passwd",
        "/home/user/workspace/../secrets.png",
    ],
)
async def test_ocr_parse_file_rejects_path_outside_user_data(tmp_path, monkeypatch: pytest.MonkeyPatch, file_path: str) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    _mock_system_options(monkeypatch)

    with pytest.raises(ValueError, match="只允许解析"):
        await ocr_parse_file.coroutine(file_path=file_path, runtime=_runtime())


@pytest.mark.asyncio
async def test_ocr_parse_file_rejects_directory(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    _mock_system_options(monkeypatch)
    thread_id = "thread-1"
    uid = "user-1"
    dir_virtual_path = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/directory"
    _patch_sandbox_backend(monkeypatch, {})

    with pytest.raises(ValueError, match="不存在或不是普通文件"):
        await ocr_parse_file.coroutine(file_path=dir_virtual_path, runtime=_runtime(thread_id=thread_id, uid=uid))


def _mock_system_options(monkeypatch: pytest.MonkeyPatch) -> None:
    from yuxi.modules.system.options import system_options
    from yuxi.modules.system.services.options import Option

    async def get_options(option, _db=None):
        assert option is system_options
        return {"default_ocr_engine": "rapid_ocr"}

    monkeypatch.setattr(Option, "get", get_options)


def _write_parse_result(output_dir, markdown):
    """构造解析产物，让工具断言真实文件写入。"""
    from yuxi.infrastructure.document_parsing import ParseResult

    output_dir.mkdir()
    path = output_dir / "document.md"
    path.write_text(markdown)
    return ParseResult(output_dir, path, ())


@pytest.mark.asyncio
async def test_ocr_directory_copy_failure_leaves_no_partial_output(workspace_root, monkeypatch):
    """复制中途失败时回收已写入的文件，工具不留下半套目录。"""
    import os

    _mock_system_options(monkeypatch)
    source = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/scan.png"
    _patch_sandbox_backend(monkeypatch, {source: b"input"})

    def copy(source_fd, target_fd):
        partial = os.open("chart.png", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=target_fd)
        os.write(partial, b"partial")
        os.close(partial)
        raise OSError("copy rejected")

    async def parse(source, output_dir, **kwargs):
        return _write_parse_result(output_dir, "text")

    monkeypatch.setattr(ocr_service, "parse", parse)
    monkeypatch.setattr("yuxi.modules.workspace.filesystem.copy_directory_fd", copy)
    with pytest.raises(OSError, match="copy rejected"):
        await ocr_parse_file.coroutine(file_path=source, runtime=_runtime())
    assert list(workspace_root.rglob("document.md")) == []
    assert list(workspace_root.rglob("chart.png")) == []
    assert list(workspace_root.rglob("scan_*")) == []
