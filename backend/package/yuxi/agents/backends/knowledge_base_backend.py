from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileDownloadResponse, FileInfo, FileUploadResponse, WriteResult

from yuxi import config as conf
from yuxi import knowledge_base
from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
from yuxi.storage.minio import get_minio_client

KBS_PATH = "/home/gem/kbs"
_INVALID_SEGMENT_RE = re.compile(r"[\\/\x00-\x1f\x7f]+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class _MaterializedFile:
    virtual_path: str
    cache_path: Path
    source_path: str
    modified_at: str | None = None


@dataclass(frozen=True)
class _ResolvedVirtualNode:
    db_id: str
    file_id: str
    path: str
    parent_path: str
    name: str
    is_folder: bool


@dataclass(frozen=True)
class _KnowledgeBaseVirtualLayout:
    kb_virtual_names: dict[str, str]
    files_by_db: dict[str, dict[str, dict[str, Any]]]
    nodes_by_db: dict[str, list[_ResolvedVirtualNode]]
    nodes_by_file_id: dict[str, _ResolvedVirtualNode]
    source_filepaths: dict[str, str]
    parsed_filepaths: dict[str, str]


def _normalize_virtual_path(path: str | None) -> str:
    raw = str(path or "").strip() or "/"
    normalized = "/" + raw.lstrip("/")
    pure = PurePosixPath(normalized)
    if ".." in pure.parts:
        raise ValueError("path traversal is not allowed")
    return str(pure)


def _sanitize_segment(value: str | None, fallback: str) -> str:
    cleaned = str(value or "").strip()
    cleaned = _INVALID_SEGMENT_RE.sub("_", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    return cleaned


def _candidate_name(file_meta: dict[str, Any]) -> str:
    filename = str(file_meta.get("filename") or "").strip()
    if filename:
        return filename

    original = str(file_meta.get("original_filename") or "").strip()
    if original:
        return original

    for key in ("path", "markdown_file"):
        raw = str(file_meta.get(key) or "").strip()
        if raw:
            try:
                parsed = PurePosixPath(raw)
                name = parsed.name
            except Exception:  # noqa: BLE001
                name = ""
            if name:
                return name

    return str(file_meta.get("file_id") or "")


def _unique_name(base_name: str, *, stable_id: str, used_names: set[str]) -> str:
    if base_name not in used_names:
        used_names.add(base_name)
        return base_name

    suffix = f"__{stable_id[:8]}"
    candidate = f"{base_name}{suffix}"
    while candidate in used_names:
        suffix = f"__{stable_id}"
        candidate = f"{base_name}{suffix}"
    used_names.add(candidate)
    return candidate


def _materialize_text_view(content: bytes, file_path: str, *, offset: int = 0, limit: int = 2000) -> str:
    if not content:
        return "System reminder: File exists but has empty contents"

    if b"\x00" in content:
        return f"Error: File '{file_path}' is binary and cannot be rendered as text"

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return f"Error: File '{file_path}' is binary and cannot be rendered as text"

    lines = text.splitlines()
    start = max(0, int(offset))
    selected = lines[start : start + int(limit)]
    return "\n".join(f"{start + idx + 1:6d}\t{line}" for idx, line in enumerate(selected))


async def resolve_visible_knowledge_bases_for_context(context) -> list[dict[str, Any]]:
    user_id = getattr(context, "user_id", None)
    if not user_id:
        setattr(context, "_visible_knowledge_bases", [])
        return []

    try:
        raw_user_id = int(user_id)
    except (TypeError, ValueError):
        setattr(context, "_visible_knowledge_bases", [])
        return []

    result = await knowledge_base.get_databases_by_raw_id(raw_user_id)
    databases = result.get("databases") or []
    enabled_knowledges = getattr(context, "knowledges", None)
    if enabled_knowledges is not None:
        enabled_names = {str(name).strip() for name in enabled_knowledges if str(name).strip()}
        databases = [db for db in databases if str(db.get("name") or "").strip() in enabled_names]

    setattr(context, "_visible_knowledge_bases", databases)
    return databases


def _all_files_meta() -> dict[str, dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    for kb_instance in getattr(knowledge_base, "kb_instances", {}).values():
        for file_id, meta in getattr(kb_instance, "files_meta", {}).items():
            aggregated[str(file_id)] = meta
    return aggregated


def _resolve_kb_virtual_names(visible_kbs: list[dict[str, Any]]) -> dict[str, str]:
    kb_name_candidates = {
        str(db.get("db_id") or db.get("name") or f"kb-{index}"): _sanitize_segment(
            db.get("name"),
            str(db.get("db_id") or f"kb-{index}"),
        )
        for index, db in enumerate(visible_kbs)
    }
    used_root_names: set[str] = set()
    kb_virtual_names: dict[str, str] = {}
    sorted_kbs = sorted(
        visible_kbs,
        key=lambda item: (str(item.get("name") or ""), str(item.get("db_id") or "")),
    )
    for db in sorted_kbs:
        db_id = str(db.get("db_id") or "")
        if not db_id:
            continue
        base_name = kb_name_candidates.get(db_id) or db_id
        kb_virtual_names[db_id] = _unique_name(base_name, stable_id=db_id, used_names=used_root_names)
    return kb_virtual_names


def _resolve_db_virtual_nodes(
    *,
    db_id: str,
    kb_root: str,
    records: dict[str, dict[str, Any]],
) -> list[_ResolvedVirtualNode]:
    # Keep sibling deduplication local to each parent directory so the generated tree is deterministic.
    children_by_parent: dict[str | None, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for file_id, meta in records.items():
        parent_id = str(meta.get("parent_id") or "").strip() or None
        children_by_parent[parent_id].append((file_id, meta))

    resolved_nodes: list[_ResolvedVirtualNode] = []

    def walk(parent_id: str | None, parent_path: str) -> None:
        used_names: set[str] = set()
        siblings = children_by_parent.get(parent_id, [])
        sorted_siblings = sorted(
            siblings,
            key=lambda item: (_sanitize_segment(_candidate_name(item[1]), item[0]), item[0]),
        )
        for file_id, meta in sorted_siblings:
            base_name = _sanitize_segment(_candidate_name(meta), file_id)
            unique_name = _unique_name(base_name, stable_id=file_id, used_names=used_names)
            child_path = f"{parent_path.rstrip('/')}/{unique_name}" if parent_path != "/" else f"/{unique_name}"
            node = _ResolvedVirtualNode(
                db_id=db_id,
                file_id=file_id,
                path=child_path,
                parent_path=parent_path,
                name=unique_name,
                is_folder=bool(meta.get("is_folder")),
            )
            resolved_nodes.append(node)
            if node.is_folder:
                walk(file_id, child_path)

    walk(None, kb_root)
    return resolved_nodes


def _build_parsed_filepath_map(
    *,
    nodes_by_file_id: dict[str, _ResolvedVirtualNode],
    files_by_db: dict[str, dict[str, dict[str, Any]]],
    kb_virtual_names: dict[str, str],
) -> dict[str, str]:
    # Parsed files mirror the source tree and only add a fixed `/parsed` segment plus `.md` suffix.
    parsed_paths: dict[str, str] = {}
    for file_id, node in nodes_by_file_id.items():
        if node.is_folder:
            continue

        record = files_by_db.get(node.db_id, {}).get(file_id, {})
        if not str(record.get("markdown_file") or "").strip():
            continue

        kb_root = f"/{kb_virtual_names[node.db_id]}"
        parsed_root = f"{kb_root}/parsed"
        parsed_parent = (
            f"{parsed_root}{node.parent_path[len(kb_root) :]}" if node.parent_path.startswith(kb_root) else parsed_root
        )
        parsed_name = _sanitize_segment(f"{node.name}.md", f"{file_id}.md")
        parsed_path = f"{parsed_parent.rstrip('/')}/{parsed_name}" if parsed_parent != "/" else f"/{parsed_name}"
        parsed_paths[file_id] = f"{KBS_PATH}{parsed_path}"

    return parsed_paths


def _resolve_virtual_layout(
    *,
    visible_kbs: list[dict[str, Any]] | None,
    files_meta: dict[str, dict[str, Any]] | None = None,
) -> _KnowledgeBaseVirtualLayout:
    # Single source of truth for the virtual KB tree.
    # The readonly backend and query_kb filepath injection must stay fully aligned.
    kb_virtual_names = _resolve_kb_virtual_names(list(visible_kbs or []))
    if not kb_virtual_names:
        return _KnowledgeBaseVirtualLayout(
            kb_virtual_names={},
            files_by_db={},
            nodes_by_db={},
            nodes_by_file_id={},
            source_filepaths={},
            parsed_filepaths={},
        )

    source_files = _all_files_meta() if files_meta is None else files_meta
    files_by_db: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for file_id, meta in source_files.items():
        db_id = str(meta.get("database_id") or "")
        if db_id in kb_virtual_names:
            files_by_db[db_id][str(file_id)] = meta

    nodes_by_db: dict[str, list[_ResolvedVirtualNode]] = {}
    nodes_by_file_id: dict[str, _ResolvedVirtualNode] = {}
    source_filepaths: dict[str, str] = {}

    for db_id, kb_virtual_name in kb_virtual_names.items():
        kb_root = f"/{kb_virtual_name}"
        records = files_by_db.get(db_id, {})
        nodes = _resolve_db_virtual_nodes(db_id=db_id, kb_root=kb_root, records=records)
        nodes_by_db[db_id] = nodes
        for node in nodes:
            nodes_by_file_id[node.file_id] = node
            if not node.is_folder:
                source_filepaths[node.file_id] = f"{KBS_PATH}{node.path}"

    return _KnowledgeBaseVirtualLayout(
        kb_virtual_names=kb_virtual_names,
        files_by_db=dict(files_by_db),
        nodes_by_db=nodes_by_db,
        nodes_by_file_id=nodes_by_file_id,
        source_filepaths=source_filepaths,
        parsed_filepaths=_build_parsed_filepath_map(
            nodes_by_file_id=nodes_by_file_id,
            files_by_db=files_by_db,
            kb_virtual_names=kb_virtual_names,
        ),
    )


def resolve_file_relative_virtual_path(
    *,
    file_id: str,
    visible_kbs: list[dict[str, Any]] | None,
    files_meta: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    normalized_id = str(file_id or "").strip()
    if not normalized_id:
        return None

    layout = _resolve_virtual_layout(
        visible_kbs=visible_kbs,
        files_meta=files_meta,
    )
    node = layout.nodes_by_file_id.get(normalized_id)
    if node is None or node.is_folder:
        return None
    return node.path


def build_knowledge_base_filepath_map(
    *,
    visible_kbs: list[dict[str, Any]] | None,
    files_meta: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str]:
    layout = _resolve_virtual_layout(
        visible_kbs=visible_kbs,
        files_meta=files_meta,
    )
    return layout.source_filepaths.copy()


def _inject_kb_filepaths(
    chunks: list[dict[str, Any]],
    filepath_map: dict[str, str],
    parsed_filepath_map: dict[str, str],
) -> list[dict[str, Any]]:
    if not filepath_map and not parsed_filepath_map:
        return chunks

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue

        metadata = chunk.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            continue

        file_id = str(metadata.get("file_id") or chunk.get("file_id") or "").strip()
        if not file_id:
            continue

        if not metadata.get("filepath"):
            filepath = filepath_map.get(file_id)
            if filepath:
                metadata["filepath"] = filepath

        if not metadata.get("parsed_path"):
            parsed_path = parsed_filepath_map.get(file_id)
            if parsed_path:
                metadata["parsed_path"] = parsed_path

        chunk["metadata"] = metadata

    return chunks


async def inject_filepaths_into_retrieval_result(
    *,
    retrieval_chunks: list[dict[str, Any]],
    visible_kbs: list[dict[str, Any]] | None,
    target_db_id: str | None,
    target_kb_name: str | None = None,
) -> list[dict[str, Any]]:
    scope_kbs = list(visible_kbs or [])
    if not scope_kbs and target_db_id:
        scope_kbs = [{"db_id": target_db_id, "name": target_kb_name or target_db_id}]

    layout = _resolve_virtual_layout(visible_kbs=scope_kbs)
    return _inject_kb_filepaths(
        retrieval_chunks,
        layout.source_filepaths,
        layout.parsed_filepaths,
    )


class KnowledgeBaseReadonlyBackend(FilesystemBackend):
    def __init__(self, *, visible_kbs: list[dict[str, Any]] | None, cache_root: Path | str | None = None):
        self._cache_root = Path(cache_root or (Path(conf.save_dir) / "knowledge_base_data" / "kb-cache")).resolve()
        self._cache_root.mkdir(parents=True, exist_ok=True)
        super().__init__(root_dir=self._cache_root, virtual_mode=True)
        self._visible_kbs = list(visible_kbs or [])
        self._entries_by_dir: dict[str, list[FileInfo]] = defaultdict(list)
        self._dir_paths: set[str] = {"/"}
        self._files: dict[str, _MaterializedFile] = {}
        self._all_files: list[FileInfo] = []
        self._build_virtual_tree()

    def has_entries(self) -> bool:
        return bool(self._visible_kbs)

    def _build_virtual_tree(self) -> None:
        layout = _resolve_virtual_layout(visible_kbs=self._visible_kbs)

        for db_id, kb_virtual_name in layout.kb_virtual_names.items():
            kb_root = f"/{kb_virtual_name}"
            self._add_entry("/", kb_root, is_dir=True)
            records = layout.files_by_db.get(db_id, {})
            self._build_source_tree(db_id=db_id, records=records, nodes=layout.nodes_by_db.get(db_id, []))
            self._build_parsed_tree(db_id=db_id, kb_root=kb_root, records=records)

        for path, entries in list(self._entries_by_dir.items()):
            entries.sort(key=lambda item: str(item.get("path") or ""))
            self._entries_by_dir[path] = entries
        self._all_files = sorted(self._all_files, key=lambda item: str(item.get("path") or ""))

    def _build_source_tree(
        self,
        *,
        db_id: str,
        records: dict[str, dict[str, Any]],
        nodes: list[_ResolvedVirtualNode],
    ) -> None:
        resolved_parent_paths: dict[str, str] = {}
        resolved_source_names: dict[str, str] = {}

        for node in sorted(nodes, key=lambda item: item.path):
            meta = records.get(node.file_id, {})
            modified_at = meta.get("updated_at") or meta.get("created_at")
            if node.is_folder:
                self._add_entry(node.parent_path, node.path, is_dir=True, modified_at=modified_at)
                continue

            self._add_entry(
                node.parent_path,
                node.path,
                is_dir=False,
                size=int(meta.get("size") or 0),
                modified_at=modified_at,
            )
            resolved_parent_paths[node.file_id] = node.parent_path
            resolved_source_names[node.file_id] = node.name
            cache_path = self._cache_root / db_id / "source" / node.file_id / node.name
            self._files[node.path] = _MaterializedFile(
                virtual_path=node.path,
                cache_path=cache_path,
                source_path=str(meta.get("path") or ""),
                modified_at=modified_at,
            )
            self._all_files.append(
                {
                    "path": node.path,
                    "is_dir": False,
                    "size": int(meta.get("size") or 0),
                    "modified_at": str(modified_at or ""),
                }
            )

        self._resolved_parent_paths = getattr(self, "_resolved_parent_paths", {})
        self._resolved_parent_paths[db_id] = resolved_parent_paths
        self._resolved_source_names = getattr(self, "_resolved_source_names", {})
        self._resolved_source_names[db_id] = resolved_source_names

    def _build_parsed_tree(self, *, db_id: str, kb_root: str, records: dict[str, dict[str, Any]]) -> None:
        parsed_records = {
            file_id: meta
            for file_id, meta in records.items()
            if not meta.get("is_folder") and str(meta.get("markdown_file") or "").strip()
        }
        if not parsed_records:
            return

        parsed_root = f"{kb_root}/parsed"
        self._add_entry(kb_root, parsed_root, is_dir=True)
        parent_paths = getattr(self, "_resolved_parent_paths", {}).get(db_id, {})
        source_names = getattr(self, "_resolved_source_names", {}).get(db_id, {})

        grouped: dict[str, list[tuple[str, dict[str, Any], str]]] = defaultdict(list)
        for file_id, meta in parsed_records.items():
            source_parent = parent_paths.get(file_id, kb_root)
            parsed_parent = (
                f"{parsed_root}{source_parent[len(kb_root) :]}" if source_parent.startswith(kb_root) else parsed_root
            )
            source_name = source_names.get(file_id) or _sanitize_segment(meta.get("filename"), file_id)
            safe_name = source_name or file_id
            base_name = _sanitize_segment(f"{safe_name}.md", f"{file_id}.md")
            grouped[parsed_parent].append((file_id, meta, base_name))

        for parsed_parent, items in grouped.items():
            current = PurePosixPath(parsed_parent)
            while str(current) not in {"", "."} and str(current) != "/":
                current_str = str(current)
                parent_str = str(current.parent) if str(current.parent) != "." else "/"
                if current_str not in self._dir_paths:
                    self._add_entry(parent_str, current_str, is_dir=True)
                current = current.parent

            used_names: set[str] = set()
            for file_id, meta, base_name in sorted(items, key=lambda item: (item[2], item[0])):
                unique_name = _unique_name(base_name, stable_id=file_id, used_names=used_names)
                file_path = f"{parsed_parent.rstrip('/')}/{unique_name}" if parsed_parent != "/" else f"/{unique_name}"
                self._add_entry(
                    parsed_parent,
                    file_path,
                    is_dir=False,
                    size=0,
                    modified_at=meta.get("updated_at") or meta.get("created_at"),
                )
                cache_path = self._cache_root / db_id / "parsed" / f"{file_id}.md"
                self._files[file_path] = _MaterializedFile(
                    virtual_path=file_path,
                    cache_path=cache_path,
                    source_path=str(meta.get("markdown_file") or ""),
                    modified_at=meta.get("updated_at") or meta.get("created_at"),
                )
                self._all_files.append(
                    {
                        "path": file_path,
                        "is_dir": False,
                        "size": 0,
                        "modified_at": str(meta.get("updated_at") or meta.get("created_at") or ""),
                    }
                )

    def _add_entry(
        self,
        parent_path: str,
        child_path: str,
        *,
        is_dir: bool,
        size: int = 0,
        modified_at: str | None = None,
    ) -> None:
        normalized_parent = _normalize_virtual_path(parent_path)
        normalized_child = _normalize_virtual_path(child_path)
        entry_path = f"{normalized_child}/" if is_dir else normalized_child
        entry: FileInfo = {
            "path": entry_path,
            "is_dir": is_dir,
            "size": int(size or 0),
            "modified_at": str(modified_at or ""),
        }
        if entry_path not in {str(item.get("path")) for item in self._entries_by_dir[normalized_parent]}:
            self._entries_by_dir[normalized_parent].append(entry)
        if is_dir:
            self._dir_paths.add(normalized_child)
            self._entries_by_dir.setdefault(normalized_child, [])

    def _ensure_local_file(self, descriptor: _MaterializedFile) -> Path:
        if descriptor.cache_path.exists():
            return descriptor.cache_path

        source = descriptor.source_path.strip()
        if not source:
            raise FileNotFoundError(descriptor.virtual_path)
        if not is_minio_url(source):
            raise FileNotFoundError(descriptor.virtual_path)

        bucket_name, object_name = parse_minio_url(source)
        payload = get_minio_client().download_file(bucket_name, object_name)
        descriptor.cache_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor.cache_path.write_bytes(payload)
        return descriptor.cache_path

    def ls_info(self, path: str) -> list[FileInfo]:
        normalized_path = _normalize_virtual_path(path)
        return list(self._entries_by_dir.get(normalized_path, []))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        normalized_path = _normalize_virtual_path(file_path)
        descriptor = self._files.get(normalized_path)
        if descriptor is None:
            if normalized_path in self._dir_paths:
                return f"Error: Path '{file_path}' is a directory"
            return f"Error: File '{file_path}' not found"

        try:
            content = self._ensure_local_file(descriptor).read_bytes()
        except FileNotFoundError:
            return f"Error: File '{file_path}' not found"
        except Exception as exc:  # noqa: BLE001
            detail = str(exc).strip() or "unknown error"
            return f"Error: Failed to read '{file_path}': {detail}"

        return _materialize_text_view(content, file_path, offset=offset, limit=limit)

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        normalized_path = _normalize_virtual_path(path)
        if ".." in PurePosixPath(pattern).parts:
            raise ValueError("Path traversal not allowed in glob pattern")
        normalized_pattern = pattern.lstrip("/") or "*"
        prefix = "/" if normalized_path == "/" else f"{normalized_path.rstrip('/')}/"

        matches: list[FileInfo] = []
        for item in self._all_files:
            item_path = str(item.get("path") or "")
            if normalized_path != "/" and not item_path.startswith(prefix):
                continue
            relative = item_path[len(prefix) :] if normalized_path != "/" else item_path.lstrip("/")
            if fnmatch.fnmatch(relative, normalized_pattern):
                matches.append(dict(item))
        return matches

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[dict[str, Any]] | str:
        normalized_path = _normalize_virtual_path(path or "/")
        prefix = "/" if normalized_path == "/" else f"{normalized_path.rstrip('/')}/"
        if normalized_path in self._files:
            targets = [normalized_path]
        else:
            targets = [
                item["path"]
                for item in self._all_files
                if normalized_path == "/" or str(item["path"]).startswith(prefix)
            ]

        matches: list[dict[str, Any]] = []
        for target in targets:
            display_target = str(target)
            relative = display_target.lstrip("/") if normalized_path == "/" else display_target[len(prefix) :]
            if glob and not fnmatch.fnmatch(relative, glob):
                continue
            descriptor = self._files.get(display_target)
            if descriptor is None:
                continue
            try:
                content = self._ensure_local_file(descriptor).read_bytes()
                if b"\x00" in content:
                    continue
                text = content.decode("utf-8")
            except Exception:  # noqa: BLE001
                continue
            for line_num, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    matches.append({"path": display_target, "line": line_num, "text": line})
        return matches

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error="Knowledge base path is read-only.")

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return EditResult(error="Knowledge base path is read-only.")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _content in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                normalized_path = _normalize_virtual_path(path)
            except ValueError:
                responses.append(FileDownloadResponse(path=path, content=None, error="invalid_path"))
                continue

            descriptor = self._files.get(normalized_path)
            if descriptor is None:
                if normalized_path in self._dir_paths:
                    responses.append(FileDownloadResponse(path=path, content=None, error="is_directory"))
                else:
                    responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
                continue

            try:
                content = self._ensure_local_file(descriptor).read_bytes()
            except FileNotFoundError:
                responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
                continue
            except ValueError:
                responses.append(FileDownloadResponse(path=path, content=None, error="invalid_path"))
                continue

            responses.append(FileDownloadResponse(path=path, content=content, error=None))
        return responses
