"""正文对象引用的版本化身份，与可重建的分块代次分开。"""

import hashlib
import json

from yuxi.infrastructure.minio.object_urls import is_minio_url


def source_object_uri(minio_url: str | None, path: str | None, file_type: str | None) -> str | None:
    """只承认原件对象字段，历史 URL 来源不能伪装成内部对象。"""
    value = minio_url or (path if file_type != "url" else None)
    return value if value and is_minio_url(value) else None


def body_revision(kb_id: str, file_id: str, markdown_file: str | None) -> str:
    """同一不可变正文指针保持修订；索引 generation 不参与身份。"""
    value = [kb_id, file_id, markdown_file]
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return "md2:" + digest


def original_revision(kb_id: str, file_id: str, original_file: str) -> str:
    """原件身份独立于转写、正文与媒体分段。"""
    value = [kb_id, file_id, original_file]
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return "src2:" + digest


def media_revision(kb_id: str, file_id: str, source_revision: str, manifest_uri: str) -> str:
    """媒体产物修订绑定实际原件身份和不可变清单。"""
    value = [kb_id, file_id, source_revision, manifest_uri]
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return "media2:" + digest


def matches_current_source(revision, file) -> bool:
    """仅判断技术来源仍匹配，不以存档新旧裁决业务政策权威。"""
    if (revision.kb_id, revision.file_id) != (file.kb_id, file.file_id) or file.deleted_at is not None or revision.deleted_at is not None:
        return False
    if revision.kind == "document":
        return file.markdown_file is not None and revision.revision == body_revision(file.kb_id, file.file_id, file.markdown_file)
    uri = source_object_uri(file.minio_url, file.path, file.file_type)
    current = original_revision(file.kb_id, file.file_id, uri) if uri else None
    if revision.kind == "original":
        return current is not None and revision.revision == current
    basis = revision.basis_document_revision
    return (
        current is not None
        and revision.source_revision == current
        and (basis is None or (file.markdown_file is not None and basis == body_revision(file.kb_id, file.file_id, file.markdown_file)))
    )
