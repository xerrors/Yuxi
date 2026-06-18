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


def _query_keywords_callable():
    return _tool_callable(tools.query_keywords)


def _find_kb_document_callable():
    return _tool_callable(tools.find_kb_document)


def _open_kb_document_callable():
    return _tool_callable(tools.open_kb_document)


async def _run_tool(callback, **kwargs):
    result = callback(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _run_query_kb(**kwargs):
    return await _run_tool(_query_kb_callable(), **kwargs)


async def _run_query_keywords(**kwargs):
    return await _run_tool(_query_keywords_callable(), **kwargs)


async def _run_find_kb_document(**kwargs):
    return await _run_tool(_find_kb_document_callable(), **kwargs)


async def _run_open_kb_document(**kwargs):
    return await _run_tool(_open_kb_document_callable(), **kwargs)


def _build_test_window(content: str, offset: int = 0, limit: int = 1800) -> dict:
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


def _patch_retrievers(monkeypatch, *, kb_type: str = "milvus", retriever=None):
    monkeypatch.setattr(
        tools._get_knowledge_base(),
        "get_retrievers",
        lambda: {
            "db-1": {
                "name": "FAQ",
                "retriever": retriever or object(),
                "metadata": {"kb_type": kb_type},
            }
        },
        raising=False,
    )


async def _fake_visible_kbs(runtime):
    del runtime
    return [{"kb_id": "db-1", "name": "FAQ"}]


@pytest.mark.asyncio
async def test_query_kb_returns_search_schema_without_sandbox_paths(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        assert kwargs == {}
        return [
            {
                "content": "auth guide",
                "metadata": {
                    "file_id": "file-1",
                    "source": "auth-guide.pdf",
                    "filepath": "/tmp/sandbox/auth-guide.pdf",
                },
            }
        ]

    _patch_retrievers(monkeypatch, retriever=_fake_retriever)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_id="db-1", query_text="auth", runtime=runtime)

    assert result["kb_id"] == "db-1"
    assert result["results"][0]["id"] == "file-1:1"
    assert result["results"][0]["kb_id"] == "db-1"
    assert result["results"][0]["file_id"] == "file-1"
    assert result["results"][0]["content"] == "auth guide"
    assert result["results"][0]["metadata"]["source"] == "auth-guide.pdf"
    assert "filepath" not in result["results"][0]["metadata"]
    assert "parsed_path" not in result["results"][0]["metadata"]


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
                    "chunk_id": "dify-segment-1",
                    "source": "Dify Doc",
                },
            }
        ]

    _patch_retrievers(monkeypatch, kb_type="dify", retriever=_fake_retriever)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_id="db-1", query_text="auth", runtime=runtime)

    assert result == {
        "kb_id": "db-1",
        "results": [
            {
                "id": "dify-segment-1",
                "kb_id": "db-1",
                "file_id": "dify-doc-1",
                "content": "auth guide",
                "metadata": {
                    "file_id": "dify-doc-1",
                    "chunk_id": "dify-segment-1",
                    "source": "Dify Doc",
                    "score": 0.98,
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_query_kb_returns_plain_result_without_path_injection(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        return "Milvus context"

    _patch_retrievers(monkeypatch, retriever=_fake_retriever)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_id="db-1", query_text="auth", runtime=runtime)

    assert result == "Milvus context"


@pytest.mark.asyncio
async def test_query_kb_maps_full_doc_id_and_chunk_metadata(monkeypatch) -> None:
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

    _patch_retrievers(monkeypatch, retriever=_fake_retriever)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_id="db-1", query_text="auth", runtime=runtime)

    assert result["results"][0] == {
        "id": "chunk-1",
        "kb_id": "db-1",
        "file_id": "file-1",
        "content": "auth guide",
        "metadata": {"chunk_index": 3},
    }


@pytest.mark.asyncio
async def test_query_keywords_forwards_precise_match_kwargs(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_retriever(query_text: str, **kwargs):
        captured["query_text"] = query_text
        captured["kwargs"] = kwargs
        return [
            {
                "content": "precise hit",
                "metadata": {"file_id": "file-1", "source": "doc.md", "is_precise_match": True},
            }
        ]

    _patch_retrievers(monkeypatch, retriever=_fake_retriever)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_keywords(kb_id="db-1", keywords=["扭转减振器", "故障"], runtime=runtime)

    # 拼接为空格分隔的查询文本，并强制 keyword + 精准匹配 + 原始关键词列表
    assert captured["query_text"] == "扭转减振器 故障"
    assert captured["kwargs"] == {
        "search_mode": "keyword",
        "precise_match": True,
        "phrase_match_terms": ["扭转减振器", "故障"],
    }
    assert result["kb_id"] == "db-1"
    assert result["results"][0]["metadata"]["is_precise_match"] is True


@pytest.mark.asyncio
async def test_query_keywords_rejects_empty_or_whitespace_keywords(monkeypatch) -> None:
    async def _must_not_be_called(*args, **kwargs):
        raise AssertionError("retriever should not be called for empty keywords")

    _patch_retrievers(monkeypatch, retriever=_must_not_be_called)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())

    assert await _run_query_keywords(kb_id="db-1", keywords=[], runtime=runtime) == "请提供关键词列表"
    assert await _run_query_keywords(kb_id="db-1", keywords=["", "   "], runtime=runtime) == "请提供关键词列表"


@pytest.mark.asyncio
async def test_find_kb_document_returns_context_windows(monkeypatch) -> None:
    _patch_retrievers(monkeypatch)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    async def _fake_find_file_content(
        kb_id: str,
        file_id: str,
        patterns: list[str],
        *,
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_windows: int = 5,
        window_size: int = 80,
    ):
        assert kb_id == "db-1"
        assert file_id == "file-1"
        assert patterns == ["token"]
        assert use_regex is False
        assert case_sensitive is False
        assert max_windows == 5
        assert window_size == 80
        return {
            "semantic": False,
            "match_mode": "keyword",
            "total_matches": 2,
            "windows": [
                {
                    "start_line": 1,
                    "end_line": 3,
                    "matched_lines": [2],
                    "content": "     1\tintro\n     2\ttoken value\n     3\toutro",
                }
            ],
        }

    monkeypatch.setattr(tools._get_knowledge_base(), "find_file_content", _fake_find_file_content, raising=False)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_find_kb_document(
        kb_id="db-1",
        file_id="file-1",
        patterns=["token"],
        runtime=runtime,
    )

    assert result == {
        "kb_id": "db-1",
        "file_id": "file-1",
        "semantic": False,
        "match_mode": "keyword",
        "total_matches": 2,
        "windows": [
            {
                "start_line": 1,
                "end_line": 3,
                "matched_lines": [2],
                "content": "     1\tintro\n     2\ttoken value\n     3\toutro",
            }
        ],
    }


@pytest.mark.asyncio
async def test_find_kb_document_rejects_dify(monkeypatch) -> None:
    _patch_retrievers(monkeypatch, kb_type="dify")
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_find_kb_document(
        kb_id="db-1",
        file_id="file-1",
        patterns=["token"],
        runtime=runtime,
    )

    assert "Dify 知识库" in result


@pytest.mark.asyncio
async def test_open_kb_document_reads_markdown_content_by_default_window(monkeypatch) -> None:
    lines = [f"line {index}" for index in range(1, 2001)]

    _patch_retrievers(monkeypatch)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    async def _fake_open_file_content(kb_id: str, file_id: str, offset: int = 0, limit: int = 1800):
        assert kb_id == "db-1"
        assert file_id == "file-1"
        return _build_test_window("\n".join(lines), offset=offset, limit=limit)

    monkeypatch.setattr(tools._get_knowledge_base(), "open_file_content", _fake_open_file_content, raising=False)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(kb_id="db-1", file_id="file-1", runtime=runtime)

    assert result["kb_id"] == "db-1"
    assert result["file_id"] == "file-1"
    assert result["start_line"] == 1
    assert result["end_line"] == 1800
    assert result["total_lines"] == 2000
    assert result["window_size"] == 1800
    assert result["has_more_before"] is False
    assert result["has_more_after"] is True
    assert result["next_offset"] == 1800
    assert "     1\tline 1" in result["content"]
    assert "  1800\tline 1800" in result["content"]


@pytest.mark.asyncio
async def test_open_kb_document_prefers_line_over_offset(monkeypatch) -> None:
    lines = [f"line {index}" for index in range(1, 1001)]

    _patch_retrievers(monkeypatch)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    async def _fake_open_file_content(kb_id: str, file_id: str, offset: int = 0, limit: int = 1800):
        assert kb_id == "db-1"
        assert file_id == "file-1"
        return _build_test_window("\n".join(lines), offset=offset, limit=limit)

    monkeypatch.setattr(tools._get_knowledge_base(), "open_file_content", _fake_open_file_content, raising=False)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(
        kb_id="db-1",
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
        del runtime
        return [{"kb_id": "db-2", "name": "FAQ"}]

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(kb_id="db-1", file_id="file-1", runtime=runtime)

    assert "不存在或当前会话未启用" in result


@pytest.mark.asyncio
async def test_open_kb_document_requires_markdown_content(monkeypatch) -> None:
    _patch_retrievers(monkeypatch)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    async def _fake_open_file_content(kb_id: str, file_id: str, offset: int = 0, limit: int = 1800):
        del kb_id, file_id, offset, limit
        raise Exception("文件 file-1 没有解析后的 Markdown 内容")

    monkeypatch.setattr(tools._get_knowledge_base(), "open_file_content", _fake_open_file_content, raising=False)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(kb_id="db-1", file_id="file-1", runtime=runtime)

    assert "没有解析后的 Markdown 内容" in result
