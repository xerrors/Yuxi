"""知识检索结果的引用与可见性适配。"""

import re

from yuxi.modules.knowledge.mcp.client import KnowledgeMCPError, KnowledgeMCPUnavailable, list_knowledge_bases

KNOWLEDGE_REFERENCE_TOOLS = frozenset({"query_kb", "open_kb_document", "find_kb_document"})


def knowledge_sources_from_tool(metadata, payload, visible_kb_ids):
    """在知识域识别检索工具并复核资源范围。"""
    tool_name = metadata.get("tool_name")
    kb_id = (metadata.get("input") or {}).get("kb_id")
    if tool_name not in KNOWLEDGE_REFERENCE_TOOLS:
        return []
    if isinstance(kb_id, list):
        if tool_name != "query_kb":
            return []
        kb_id = [target.strip() for target in kb_id if isinstance(target, str) and target.strip() in visible_kb_ids]
    elif kb_id not in visible_kb_ids:
        return []
    return knowledge_reference_sources(payload, tool_name, kb_id)


async def knowledge_reference_visibility(uid, messages):
    """只为本轮知识检索读取当前可见文档能力。"""
    if not any((message.extra_metadata or {}).get("tool_name") in KNOWLEDGE_REFERENCE_TOOLS for message in messages):
        return {}
    try:
        return {
            item["kb_id"]: {
                "supports_documents": item["supports_documents"],
                "supports_document_read": item["supports_document_read"],
            }
            for item in await list_knowledge_bases(uid)
        }
    except (KnowledgeMCPError, KnowledgeMCPUnavailable):
        return {}


def enrich_knowledge_sources(sources, document_support):
    """将当前文档读取能力附到知识来源。"""
    for source in sources:
        if source["kind"] == "knowledge":
            source.update(document_support[source["kb_id"]])


def knowledge_reference_sources(payload: dict | list, tool_name: str, kb_id: str | list[str]) -> list[dict]:
    """保留 chunk 权威范围，编号原文窗口可进一步定位摘录。"""
    if tool_name == "query_kb":
        if isinstance(payload, list):
            items = payload
        else:
            items = payload.get("results") or payload.get("data", {}).get("chunks") or []
    elif isinstance(payload, dict):
        items = payload.get("windows", []) if tool_name == "find_kb_document" else [payload]
    else:
        return []

    sources = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source_kb_id = kb_id
        if isinstance(kb_id, list):
            source_kb_id = item.get("kb_id")
            if source_kb_id not in kb_id:
                continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        metadata = item.get("metadata") or {}
        file_id = item.get("file_id") or metadata.get("file_id") or (payload.get("file_id") if isinstance(payload, dict) else "")
        start = metadata.get("start_line") or item.get("start_line")
        end = metadata.get("end_line") or item.get("end_line")
        numbered = tool_name != "query_kb"
        if numbered:
            content = re.sub(r"(?m)^[ \t]*\d+\t", "", content)
        sources.append(
            {
                "kind": "knowledge",
                "title": metadata.get("source") or metadata.get("title") or file_id or "知识库片段",
                "kb_id": source_kb_id,
                "file_id": file_id or "",
                "chunk_id": metadata.get("chunk_id") or item.get("id") or "",
                "generation": metadata.get("generation"),
                "start_line": start if type(start) is int and start > 0 else None,
                "end_line": end if type(end) is int and end > 0 else None,
                "numbered": numbered,
                "content": content,
            }
        )
    return sources
