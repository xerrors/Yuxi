"""个人工作区文件的浏览、搜索、编辑、上传与下载用例。"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import tempfile
from pathlib import Path, PurePosixPath

from yuxi.infrastructure.file_preview import detect_preview_type, preview_too_large
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.office_conversion import OfficeConversionError
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.errors import FileTransferLimitError
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.paths import ensure_user_workspace, normalize_workspace_scope, validate_directory_name
from yuxi.modules.workspace.preview import preview_workspace_file
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.modules.workspace.runtime_paths import runtime_workspace_path
from yuxi.modules.workspace.services.file_entries import file_entry, sort_file_entries
from yuxi.shared.errors import (
    ContentTooLargeError,
    DomainError,
    InvalidInputError,
    InvalidOperationError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES, FileInput, PreparedFile, PreviewResult, detect_media_type

EDITABLE_WORKSPACE_SUFFIXES = {".md", ".markdown", ".mdx", ".txt"}
MAX_WORKSPACE_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
MAX_WORKSPACE_UPLOAD_FILES = 50
# 完整虚拟路径（含 parent_path）的段数上限，避免创建既有递归删除无法回收的目录树。
MAX_WORKSPACE_UPLOAD_PATH_DEPTH = 64
MAX_WORKSPACE_UPLOAD_NAME_BYTES = 255
MAX_WORKSPACE_UPLOAD_PATH_BYTES = 4096
MAX_WORKSPACE_DOWNLOAD_SIZE_BYTES = 1024 * 1024 * 1024

# 搜索返回条数上限，避免超大工作区一次性返回过多结果
WORKSPACE_SEARCH_MAX_RESULTS = 100
# 递归列目录复用同一条有界扫描通道，浏览场景需要比关键词搜索更大的可见上限。
WORKSPACE_TREE_MAX_RESULTS = 5000
WORKSPACE_SCOPE_ROOT = "/"


async def search_workspace_files(*, query: str, current_user: User) -> dict:
    """按文件名在个人工作区内递归搜索，仅返回文件条目。"""
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return {"entries": []}

    backend = _workspace_backend(current_user)
    try:
        matches = await asyncio.to_thread(
            backend.search_authorized_tree,
            WORKSPACE_SCOPE_ROOT,
            normalized_query,
            include_directories=False,
            max_results=WORKSPACE_SEARCH_MAX_RESULTS,
        )
    except FileNotFoundError:
        return {"entries": []}
    return {"entries": [_entry_from_metadata(item["path"], item) for item in matches]}


async def list_workspace_tree(
    *,
    path: str,
    recursive: bool = False,
    files_only: bool = False,
    current_user: User,
) -> dict:
    """列出个人工作区目录树；projects 下展示全部实际文件（含会话自动创建的 Workdir）。

    项目是否可选、是否绑定由项目选择器接口负责，展示不再按绑定状态过滤，
    上传/新建/保存产物成功后必然可见（issue #1184）。
    """
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    try:
        if recursive:
            scanned = await asyncio.to_thread(
                backend.search_authorized_tree,
                workspace_path,
                "",
                include_directories=not files_only,
                max_results=WORKSPACE_TREE_MAX_RESULTS,
            )
            entries = [_entry_from_metadata(item["path"], item) for item in scanned]
        else:
            entries = await asyncio.to_thread(
                _list_workspace_directory,
                backend,
                workspace_path,
                files_only=files_only,
            )
    except FileNotFoundError:
        return {"entries": []}
    except NotADirectoryError as exc:
        raise InvalidOperationError("当前路径不是目录") from exc
    except PermissionError as exc:
        raise PermissionDeniedError("Access denied") from exc
    return {"entries": entries}


async def read_workspace_file_bytes(*, path: str, current_user: User) -> tuple[str, bytes]:
    """在 no-follow Workspace 边界内读取知识库导入文件。"""
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    try:
        content = await asyncio.to_thread(
            backend.read_authorized_file,
            workspace_path,
            MAX_WORKSPACE_UPLOAD_SIZE_BYTES,
        )
    except FileTransferLimitError as exc:
        raise InvalidOperationError("文件过大，当前仅支持 100 MB 以内的工作区文件") from exc
    except FileNotFoundError as exc:
        raise ResourceNotFoundError(f"工作区文件不存在: {path}") from exc
    except IsADirectoryError as exc:
        raise InvalidOperationError(f"当前路径不是文件: {path}") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise PermissionDeniedError("Access denied") from exc
    return PurePosixPath(workspace_path).name, content


async def read_workspace_file_content(*, path: str, current_user: User) -> dict | PreviewResult:
    """读取工作区文件并渲染预览；超出预览预算时返回不支持结果而不是抛错。"""
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    try:
        raw_content = await asyncio.to_thread(
            backend.read_authorized_file,
            workspace_path,
            MAX_FILE_PREVIEW_BYTES,
        )
    except FileTransferLimitError:
        return preview_too_large().payload()
    except FileNotFoundError as exc:
        raise ResourceNotFoundError("文件不存在") from exc
    except IsADirectoryError as exc:
        raise InvalidOperationError("当前路径不是文件") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise PermissionDeniedError("Access denied") from exc

    try:
        return await preview_workspace_file(
            path,
            raw_content,
            office_cache_key=f"workspace:{current_user.uid}:{workspace_path}",
        )
    except OfficeConversionError as exc:
        raise InvalidOperationError(str(exc)) from exc


async def write_workspace_file_content(*, path: str, content: str, current_user: User) -> dict:
    """覆盖写入文本类工作区文件，内容或类型不可编辑时显式拒绝。"""
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    if PurePosixPath(workspace_path).suffix.lower() not in EDITABLE_WORKSPACE_SUFFIXES:
        raise InvalidOperationError("当前文件类型不支持编辑")

    try:
        raw_content = await asyncio.to_thread(
            backend.read_authorized_file,
            workspace_path,
            MAX_WORKSPACE_UPLOAD_SIZE_BYTES,
        )
    except FileNotFoundError as exc:
        raise ResourceNotFoundError("文件不存在") from exc
    except IsADirectoryError as exc:
        raise InvalidOperationError("当前路径是目录") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise PermissionDeniedError("Access denied") from exc
    preview_type, supported, _message = detect_preview_type(path, raw_content)
    # 后缀可编辑不代表内容可编辑：按字节识别为图片/PDF 等其他类型的文件仍拒绝覆盖。
    if preview_type not in {"markdown", "text"} or not supported:
        raise InvalidOperationError("当前文件类型不支持编辑")
    try:
        raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidOperationError("当前文件不是 UTF-8 文本") from exc

    try:
        item = await asyncio.to_thread(backend.write_authorized_file, workspace_path, content.encode("utf-8"))
    except FileNotFoundError as exc:
        raise ResourceNotFoundError("文件不存在") from exc
    except IsADirectoryError as exc:
        raise InvalidOperationError("当前路径是目录") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise PermissionDeniedError("Access denied") from exc

    return {
        "success": True,
        "path": _normalize_workspace_path(path).as_posix(),
        "entry": _entry_from_metadata(workspace_path, item),
    }


async def delete_workspace_path(*, path: str, current_user: User, db) -> dict:
    """删除工作区内的文件或目录，根目录不可删除。

    目标是仍被会话引用的 Project Workdir（或其祖先目录）时拒绝整次删除：
    递归删除会清空会话文件而 Project 绑定与 Session 引用仍然存活，会话
    随即指向不存在的目录；已删除会话（status=deleted）或已软删 Project
    的残留目录不再阻塞。判断只依据引用路径的祖先关系，删除 Workdir
    内部单个文件的正常清理不受影响。
    """
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    if workspace_path == WORKSPACE_SCOPE_ROOT:
        raise InvalidOperationError("工作区根目录不允许删除")

    target = PurePosixPath(str(workspace_path).strip("/"))
    referenced = await ProjectRepository(db).list_referenced_workdir_paths_for_user(str(current_user.uid))
    if any(target == PurePosixPath(item) or target in PurePosixPath(item).parents for item in referenced):
        raise InvalidOperationError(
            "该目录包含会话正在使用的工作目录，整体删除会清空会话文件；请先删除相关会话或项目",
            code="workspace_session_workdir_protected",
        )

    try:
        await asyncio.to_thread(
            backend.delete_authorized_path,
            workspace_path,
            root=WORKSPACE_SCOPE_ROOT,
        )
    except FileNotFoundError as exc:
        raise ResourceNotFoundError("文件不存在") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise PermissionDeniedError("Access denied") from exc

    return {"success": True, "path": _normalize_workspace_path(path).as_posix()}


async def create_workspace_directory(*, parent_path: str, name: str, current_user: User) -> dict:
    """在指定父目录下创建单个工作区文件夹。"""
    backend = _workspace_backend(current_user)
    directory_name = _validate_child_name(name, field_name="文件夹名")
    virtual_parent = _workspace_path(parent_path)
    target = f"{virtual_parent.rstrip('/')}/{directory_name}"

    try:
        item = await asyncio.to_thread(
            backend.create_authorized_directory,
            virtual_parent,
            directory_name,
            root=WORKSPACE_SCOPE_ROOT,
        )
    except FileExistsError as exc:
        raise InvalidOperationError("同名文件或文件夹已存在", code="workspace_directory_exists") from exc
    except FileNotFoundError as exc:
        raise ResourceNotFoundError("目标目录不存在") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise PermissionDeniedError("Access denied") from exc

    return {"success": True, "entry": _entry_from_metadata(target, item)}


async def upload_workspace_files(
    *,
    parent_path: str,
    files: list[FileInput],
    relative_paths: list[str] | None = None,
    current_user: User,
) -> dict:
    """把借用的文件流写入用户工作区，失败时撤销本批次已写入文件。"""
    if not files:
        raise InvalidOperationError("请选择至少一个文件")
    if len(files) > MAX_WORKSPACE_UPLOAD_FILES:
        raise InvalidOperationError(f"一次最多上传 {MAX_WORKSPACE_UPLOAD_FILES} 个文件")
    if relative_paths is not None and len(relative_paths) != len(files):
        raise InvalidInputError("相对路径数量与文件数量不一致")

    backend = _workspace_backend(current_user)
    parent = _workspace_path(parent_path)
    try:
        parent_stat = await asyncio.to_thread(backend.stat_authorized_path, parent, root=WORKSPACE_SCOPE_ROOT)
    except FileNotFoundError as exc:
        raise ResourceNotFoundError("目标目录不存在") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise PermissionDeniedError("Access denied") from exc
    if not parent_stat["is_dir"]:
        raise InvalidOperationError("目标路径不是目录")
    parent_depth = len(PurePosixPath(parent).parts) - 1
    if parent_depth >= MAX_WORKSPACE_UPLOAD_PATH_DEPTH:
        raise InvalidInputError("目标目录层级过深")

    # 写入前完整规划目标并预检，保证校验阶段不产生文件副作用。
    upload_targets = _plan_upload_targets(
        parent=parent,
        files=files,
        relative_paths=relative_paths,
        parent_depth=parent_depth,
    )
    await _ensure_upload_targets_available(backend, upload_targets)

    completed_entries: list[tuple[str, dict]] = []
    try:
        for file, target in upload_targets:
            item = await _write_workspace_upload(file, backend, target)
            completed_entries.append((target, item))
    except DomainError:
        # 批次任一失败即回收已写入文件；单点回收失败不掩盖原始错误。
        for target, _item in completed_entries:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(
                    backend.delete_authorized_path,
                    target,
                    root=WORKSPACE_SCOPE_ROOT,
                )
        raise

    entries = [_entry_from_metadata(target, item) for target, item in completed_entries]
    return {"success": True, "entries": entries}


async def download_workspace_file(*, path: str, current_user: User) -> PreparedFile:
    """把工作区文件有界复制到服务临时文件，失败时回收临时文件。"""
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    file_name = PurePosixPath(workspace_path).name or "download"
    media_type = detect_media_type(file_name)
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-workspace-download-", suffix=PurePosixPath(file_name).suffix)
    os.close(descriptor)
    completed = False
    try:
        await asyncio.to_thread(
            backend.download_authorized_file_to_path,
            workspace_path,
            temp_path,
            MAX_WORKSPACE_DOWNLOAD_SIZE_BYTES,
        )
        completed = True
    except FileNotFoundError as exc:
        raise ResourceNotFoundError("文件不存在") from exc
    except FileTransferLimitError as exc:
        raise ContentTooLargeError("文件超过下载大小限制") from exc
    except (PermissionError, IsADirectoryError, NotADirectoryError, ValueError) as exc:
        raise PermissionDeniedError("Access denied") from exc
    finally:
        # 下载失败时回收服务临时文件；成功时其所有权移交给 PreparedFile。
        if not completed:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)
    return PreparedFile(
        path=temp_path,
        media_type=media_type,
        filename=file_name,
        explicit_disposition=True,
    )


def _workspace_backend(user: User) -> Workspace:
    """物化并返回 uid 级 no-follow 文件系统。"""
    backend = Workspace(str(user.uid))
    try:
        ensure_user_workspace(str(user.uid))
    except (OSError, ValueError) as exc:
        raise PermissionDeniedError("Access denied") from exc
    return backend


def _normalize_workspace_path(path: str | None) -> PurePosixPath:
    raw_path = (path or "/").strip() or "/"
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"
    try:
        return PurePosixPath(normalize_workspace_scope(raw_path))
    except ValueError as exc:
        raise PermissionDeniedError("Access denied") from exc


def _workspace_path(path: str | None) -> str:
    return _normalize_workspace_path(path).as_posix()


def _validate_child_name(name: str, *, field_name: str) -> str:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise InvalidInputError(f"{field_name} 不能为空")
    try:
        validate_directory_name(clean_name)
    except ValueError as exc:
        raise InvalidInputError(f"{field_name} 不能包含路径分隔符") from exc
    return clean_name


def _normalize_upload_relative_path(value: str, *, parent_depth: int = 0) -> str:
    """校验文件夹上传的相对路径，只接受普通 POSIX 路径组件。"""
    raw = str(value or "")
    if not raw or raw.startswith("/") or raw.endswith("/") or "\\" in raw or "\0" in raw:
        raise InvalidInputError("相对路径无效")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidInputError("相对路径无效")
    if parent_depth + len(parts) > MAX_WORKSPACE_UPLOAD_PATH_DEPTH:
        raise InvalidInputError("相对路径层级过深")
    try:
        raw_bytes = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidInputError("相对路径无效") from exc
    if len(raw_bytes) > MAX_WORKSPACE_UPLOAD_PATH_BYTES:
        raise InvalidInputError("相对路径过长")
    # 拒绝 "C:" 形态的 Windows 盘符首段；validate_directory_name 不拦截冒号。
    if len(parts[0]) == 2 and parts[0][1] == ":" and parts[0][0].isalpha():
        raise InvalidInputError("相对路径无效")
    for part in parts:
        # scope 的既有规范化会裁掉路径尾部空白，目录也必须能单独寻址。
        if part[-1].isspace():
            raise InvalidInputError("路径组件不能以空白字符结尾", code="workspace_upload_path_invalid")
        if len(part.encode("utf-8")) > MAX_WORKSPACE_UPLOAD_NAME_BYTES:
            raise InvalidInputError("相对路径组件过长")
        try:
            validate_directory_name(part)
        except ValueError as exc:
            raise InvalidInputError("相对路径无效") from exc
    return raw


def _entry_from_metadata(workspace_path: str, item: dict) -> dict:
    entry = file_entry(workspace_path, item)
    entry["virtual_path"] = runtime_workspace_path(workspace_path)
    if entry["is_dir"]:
        entry["size"] = 0
        if entry["path"] != "/":
            entry["virtual_path"] += "/"
    return entry


def _list_workspace_directory(
    backend: Workspace,
    target: str,
    *,
    files_only: bool = False,
) -> list[dict]:
    children = backend.list_authorized_directory(target, root=WORKSPACE_SCOPE_ROOT)
    entries = []
    for child in children:
        child_path = f"{target.rstrip('/')}/{child['name']}"
        if not files_only or not child["is_dir"]:
            entries.append(_entry_from_metadata(child_path, child))
    return sort_file_entries(entries)


def _plan_upload_targets(
    *,
    parent: str,
    files: list[FileInput],
    relative_paths: list[str] | None,
    parent_depth: int,
) -> list[tuple[FileInput, str]]:
    """解析每个上传文件的目标虚拟路径，拒绝重复目标与同批文件/文件夹路径冲突。"""
    upload_targets: list[tuple[FileInput, str]] = []
    seen_targets: set[str] = set()
    for index, file in enumerate(files):
        if relative_paths is None:
            child_name = _validate_child_name(Path(file.filename or "").name, field_name="文件名")
        else:
            child_name = _normalize_upload_relative_path(relative_paths[index], parent_depth=parent_depth)
        target = f"{parent.rstrip('/')}/{child_name}"
        if target in seen_targets:
            raise InvalidOperationError(f"上传目标重复: {target}")
        seen_targets.add(target)
        upload_targets.append((file, target))

    # 祖先冲突在全部去重通过后整批检查，两段遍历保持与校验顺序绑定的错误优先级。
    for _file, target in upload_targets:
        if any(str(ancestor) in seen_targets for ancestor in PurePosixPath(target).parents):
            raise InvalidOperationError(f"文件与文件夹路径冲突: {target}", code="workspace_upload_path_conflict")
    return upload_targets


async def _ensure_upload_targets_available(backend: Workspace, upload_targets: list[tuple[FileInput, str]]) -> None:
    """写入前拒绝已存在的目标和类型冲突，避免留下半批文件。"""
    for _file, target in upload_targets:
        try:
            await asyncio.to_thread(backend.stat_authorized_path, target, root=WORKSPACE_SCOPE_ROOT)
        except FileNotFoundError:
            continue
        except (PermissionError, NotADirectoryError, ValueError) as exc:
            raise InvalidOperationError(f"目标路径不可用: {target}") from exc
        except OSError as exc:
            if exc.errno == errno.ENAMETOOLONG:
                raise InvalidInputError("文件路径过长") from exc
            raise
        raise InvalidOperationError(f"同名文件或文件夹已存在: {target}")


async def _write_workspace_upload(file: FileInput, backend: Workspace, target: str) -> dict:
    """等待文件 I/O 完成后归还输入流，保留原子写入与冲突语义。"""
    try:
        return await await_io(
            asyncio.to_thread(
                backend.upload_authorized_file_from_stream,
                target,
                file.source,
                max_bytes=MAX_WORKSPACE_UPLOAD_SIZE_BYTES,
                overwrite=False,
            )
        )
    except FileExistsError as exc:
        raise InvalidOperationError(f"同名文件或文件夹已存在: {target}") from exc
    except NotADirectoryError as exc:
        raise InvalidOperationError(f"目标路径与已有文件冲突: {target}") from exc
    except FileTransferLimitError as exc:
        raise InvalidOperationError("文件过大，当前仅支持 100 MB 以内的文件") from exc
    except (ValueError, PermissionError) as exc:
        raise InvalidOperationError(str(exc)) from exc
