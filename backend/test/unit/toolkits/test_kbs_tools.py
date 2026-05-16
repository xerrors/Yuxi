from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from yuxi.agents.toolkits.kbs import tools


def _tool_callable(tool):
    callback = getattr(tool, "coroutine", None)
    if callback is not None:
        return callback

    callback = getattr(tool, "func", None)
    if callback is not None:
        return callback

    raise AssertionError(f"{tool.name} tool has no callable entry")


def _query_kb_callable():
    return _tool_callable(tools.query_kb)


def _open_kb_document_callable():
    return _tool_callable(tools.open_kb_document)


async def _run_tool(callback, **kwargs):
    result = callback(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _run_query_kb(**kwargs):
    return await _run_tool(_query_kb_callable(), **kwargs)


async def _run_open_kb_document(**kwargs):
    return await _run_tool(_open_kb_document_callable(), **kwargs)


def _build_test_window(content: str, offset: int = 0, limit: int = 800) -> dict:
    lines = content.splitlines()
    start = min(max(offset, 0), len(lines))
    selected = lines[start : start + limit]
    end = start + len(selected)
    return {
        "start_line": start + 1 if selected else 0,
        "end_line": end,
        "total_lines": len(lines),
        "offset": start,
        "window_size": limit,
        "has_more_before": start > 0,
        "has_more_after": end < len(lines),
        "next_offset": end if end < len(lines) else None,
        "content": "\n".join(f"{start + idx + 1:6d}\t{line}" for idx, line in enumerate(selected)),
    }


@pytest.mark.asyncio
async def test_query_kb_returns_milvus_chunks_without_sandbox_paths(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        return [
            {
                "content": "auth guide",
                "metadata": {
                    "file_id": "file-1",
                    "source": "auth-guide.pdf",
                },
            }
        ]

    monkeypatch.setattr(
        tools.knowledge_base,
        "get_retrievers",
        lambda: {
            "db-1": {
                "name": "FAQ",
                "retriever": _fake_retriever,
                "metadata": {"kb_type": "milvus"},
            }
        },
    )

    async def _fake_visible_kbs(runtime):
        return [{"db_id": "db-1", "name": "FAQ"}]

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_name="FAQ", query_text="auth", runtime=runtime)

    assert result == [
        {
            "content": "auth guide",
            "metadata": {
                "file_id": "file-1",
                "source": "auth-guide.pdf",
            },
        }
    ]
    assert "filepath" not in result[0]["metadata"]
    assert "parsed_path" not in result[0]["metadata"]


@pytest.mark.asyncio
async def test_query_kb_allows_dify_knowledge_base(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        return [
            {
                "content": "auth guide",
                "score": 0.98,
                "metadata": {
                    "file_id": "dify-doc-1",
                    "source": "Dify Doc",
                },
            }
        ]

    monkeypatch.setattr(
        tools.knowledge_base,
        "get_retrievers",
        lambda: {
            "db-1": {
                "name": "FAQ",
                "retriever": _fake_retriever,
                "metadata": {"kb_type": "dify"},
            }
        },
    )

    async def _fake_visible_kbs(runtime):
        return [{"db_id": "db-1", "name": "FAQ"}]

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_name="FAQ", query_text="auth", runtime=runtime)

    assert result == [
        {
            "content": "auth guide",
            "score": 0.98,
            "metadata": {
                "file_id": "dify-doc-1",
                "source": "Dify Doc",
            },
        }
    ]


@pytest.mark.asyncio
async def test_query_kb_returns_lightrag_result_without_path_injection(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        return "LightRAG context"

    monkeypatch.setattr(
        tools.knowledge_base,
        "get_retrievers",
        lambda: {
            "db-1": {
                "name": "FAQ",
                "retriever": _fake_retriever,
                "metadata": {"kb_type": "lightrag"},
            }
        },
    )

    async def _fake_visible_kbs(runtime):
        return [{"db_id": "db-1", "name": "FAQ"}]

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_name="FAQ", query_text="auth", runtime=runtime)

    assert result == "LightRAG context"


@pytest.mark.asyncio
async def test_query_kb_normalizes_file_metadata_for_open(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        return [
            {
                "content": "auth guide",
                "full_doc_id": "file-1",
                "chunk_id": "chunk-1",
                "chunk_index": 3,
            }
        ]

    monkeypatch.setattr(
        tools.knowledge_base,
        "get_retrievers",
        lambda: {
            "db-1": {
                "name": "FAQ",
                "retriever": _fake_retriever,
                "metadata": {"kb_type": "lightrag"},
            }
        },
    )

    async def _fake_visible_kbs(runtime):
        return [{"db_id": "db-1", "name": "FAQ"}]

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_name="FAQ", query_text="auth", runtime=runtime)

    assert result[0]["metadata"] == {
        "file_id": "file-1",
        "chunk_id": "chunk-1",
        "chunk_index": 3,
    }


@pytest.mark.asyncio
async def test_open_kb_document_reads_markdown_content_by_default_window(monkeypatch) -> None:
    lines = [f"line {index}" for index in range(1, 1001)]

    monkeypatch.setattr(
        tools.knowledge_base,
        "get_retrievers",
        lambda: {
            "db-1": {
                "name": "FAQ",
                "retriever": object(),
                "metadata": {"kb_type": "milvus"},
            }
        },
    )

    async def _fake_visible_kbs(runtime):
        return [{"db_id": "db-1", "name": "FAQ"}]

    async def _fake_open_file_content(db_id: str, file_id: str, offset: int = 0, limit: int = 800):
        assert db_id == "db-1"
        assert file_id == "file-1"
        return _build_test_window("\n".join(lines), offset=offset, limit=limit)

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)
    monkeypatch.setattr(tools.knowledge_base, "open_file_content", _fake_open_file_content)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(resource_id="db-1", file_id="file-1", runtime=runtime)

    assert result["resource_id"] == "db-1"
    assert result["file_id"] == "file-1"
    assert result["start_line"] == 1
    assert result["end_line"] == 800
    assert result["total_lines"] == 1000
    assert result["window_size"] == 800
    assert result["has_more_before"] is False
    assert result["has_more_after"] is True
    assert result["next_offset"] == 800
    assert "     1\tline 1" in result["content"]
    assert "   800\tline 800" in result["content"]


@pytest.mark.asyncio
async def test_open_kb_document_prefers_line_over_offset(monkeypatch) -> None:
    lines = [f"line {index}" for index in range(1, 1001)]

    monkeypatch.setattr(
        tools.knowledge_base,
        "get_retrievers",
        lambda: {
            "db-1": {
                "name": "FAQ",
                "retriever": object(),
                "metadata": {"kb_type": "milvus"},
            }
        },
    )

    async def _fake_visible_kbs(runtime):
        return [{"db_id": "db-1", "name": "FAQ"}]

    async def _fake_open_file_content(db_id: str, file_id: str, offset: int = 0, limit: int = 800):
        assert db_id == "db-1"
        assert file_id == "file-1"
        return _build_test_window("\n".join(lines), offset=offset, limit=limit)

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)
    monkeypatch.setattr(tools.knowledge_base, "open_file_content", _fake_open_file_content)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(
        resource_id="db-1",
        file_id="file-1",
        line=801,
        offset=0,
        window_size=10,
        runtime=runtime,
    )

    assert result["offset"] == 800
    assert result["start_line"] == 801
    assert result["end_line"] == 810
    assert result["has_more_before"] is True
    assert result["has_more_after"] is True
    assert result["next_offset"] == 810
    assert "   801\tline 801" in result["content"]


@pytest.mark.asyncio
async def test_open_kb_document_rejects_invisible_resource(monkeypatch) -> None:
    async def _fake_visible_kbs(runtime):
        return [{"db_id": "db-2", "name": "FAQ"}]

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(resource_id="db-1", file_id="file-1", runtime=runtime)

    assert "不存在或当前会话未启用" in result


@pytest.mark.asyncio
async def test_open_kb_document_requires_markdown_content(monkeypatch) -> None:
    monkeypatch.setattr(
        tools.knowledge_base,
        "get_retrievers",
        lambda: {
            "db-1": {
                "name": "FAQ",
                "retriever": object(),
                "metadata": {"kb_type": "milvus"},
            }
        },
    )

    async def _fake_visible_kbs(runtime):
        return [{"db_id": "db-1", "name": "FAQ"}]

    async def _fake_open_file_content(db_id: str, file_id: str, offset: int = 0, limit: int = 800):
        del db_id, file_id, offset, limit
        raise Exception("文件 file-1 没有解析后的 Markdown 内容")

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)
    monkeypatch.setattr(tools.knowledge_base, "open_file_content", _fake_open_file_content)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(resource_id="db-1", file_id="file-1", runtime=runtime)

    assert "没有解析后的 Markdown 内容" in result
