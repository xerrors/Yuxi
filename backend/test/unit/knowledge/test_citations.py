from types import SimpleNamespace

from yuxi.knowledge.citations import (
    build_citation_entries,
    count_prior_citations,
    render_citation_hint,
)


def _tool_message(name: str, content: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, content=content)


def _results(count: int, source: str = "a.pdf") -> list[dict]:
    return [
        {"kb_id": "kb-1", "file_id": f"file-{i}", "id": f"chunk-{i}", "metadata": {"source": source}}
        for i in range(count)
    ]


def test_build_citation_entries_numbers_results_from_one():
    results = [
        {"kb_id": "kb-1", "file_id": "file-1", "id": "chunk-a", "metadata": {"source": "a.pdf"}},
        {"kb_id": "kb-1", "file_id": "file-2", "id": "chunk-b", "metadata": {"source": "b.pdf"}},
    ]

    entries = build_citation_entries(results)

    assert [entry["index"] for entry in entries] == [1, 2]
    assert [entry["source"] for entry in entries] == ["a.pdf", "b.pdf"]


def test_build_citation_entries_renumbers_after_invalid_result():
    """编号必须与最终输出给模型的片段连续对应，脏数据不能留下空洞。"""
    results = [
        {"kb_id": "kb-1", "file_id": "file-1", "id": "chunk-a", "metadata": {"source": "a.pdf"}},
        "not-a-dict",
        {"kb_id": "kb-1", "file_id": "file-2", "id": "chunk-b", "metadata": {"source": "b.pdf"}},
    ]

    entries = build_citation_entries(results)

    assert [entry["index"] for entry in entries] == [1, 2]
    assert entries[1]["chunk_id"] == "chunk-b"


def test_build_citation_entries_falls_back_to_metadata_file_id_and_placeholder_source():
    results = [{"kb_id": "kb-1", "id": "chunk-a", "metadata": {"file_id": "file-9"}}]

    entry = build_citation_entries(results)[0]

    assert entry["file_id"] == "file-9"
    assert entry["source"] == "未知来源"


def test_build_citation_entries_prefers_top_level_file_id_over_metadata():
    results = [
        {
            "kb_id": "kb-1",
            "file_id": "file-1",
            "id": "chunk-a",
            "metadata": {"file_id": "stale", "source": "a.pdf"},
        }
    ]

    assert build_citation_entries(results)[0]["file_id"] == "file-1"


def test_render_citation_hint_lists_only_retrieved_numbers():
    entries = [
        {"index": 1, "kb_id": "kb-1", "file_id": "f1", "chunk_id": "c1", "source": "a.pdf"},
        {"index": 2, "kb_id": "kb-1", "file_id": "f2", "chunk_id": "c2", "source": "b.pdf"},
    ]

    hint = render_citation_hint(entries)

    assert "[1] a.pdf" in hint
    assert "[2] b.pdf" in hint
    assert "不要自己写来源名称" in hint
    assert '<cite type="file">编号</cite>' in hint


def test_render_citation_hint_returns_empty_without_entries():
    assert render_citation_hint([]) == ""


def test_build_citation_entries_continues_numbering_from_offset():
    """多轮检索必须继续递增，否则同一个编号会指向不同片段。"""
    entries = build_citation_entries(_results(2), offset=3)

    assert [entry["index"] for entry in entries] == [4, 5]


def test_count_prior_citations_sums_previous_query_kb_results():
    messages = [
        _tool_message("query_kb", {"results": _results(3)}),
        _tool_message("query_kb", {"results": _results(2)}),
    ]

    assert count_prior_citations(messages) == 5


def test_count_prior_citations_reads_serialized_tool_messages():
    import json

    messages = [_tool_message("query_kb", json.dumps({"results": _results(2)}))]

    assert count_prior_citations(messages) == 2


def test_count_prior_citations_ignores_other_tools_and_broken_content():
    messages = [
        _tool_message("grep", {"results": _results(4)}),
        _tool_message("query_kb", "not json"),
        _tool_message("query_kb", None),
        _tool_message(None, {"results": _results(1)}),
    ]

    assert count_prior_citations(messages) == 0


def test_count_prior_citations_returns_zero_without_messages():
    assert count_prior_citations(None) == 0
    assert count_prior_citations([]) == 0


def test_count_prior_citations_prefers_written_cite_when_history_is_truncated():
    """上下文压缩会丢掉早期工具消息，残留消息里的编号必须能兜住基数。"""
    results = _results(3)
    for position, item in enumerate(results):
        item["cite"] = position + 10
    messages = [_tool_message("query_kb", {"results": results})]

    assert count_prior_citations(messages) == 12


def test_count_prior_citations_ignores_invalid_cite_values():
    results = _results(2)
    results[0]["cite"] = "not-a-number"
    results[1]["cite"] = -3
    messages = [_tool_message("query_kb", {"results": results})]

    assert count_prior_citations(messages) == 2
