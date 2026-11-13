"""引用输出与工具快照的独立边界案例。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.modules.agents.services.references import collect_reference_sources, reference_answer_lines, validate_reference_matches
from yuxi.shared.errors import OperationTimeoutError


def tool(name, payload, *, kb_id="kb-a", message_id=1):
    """构造已完成工具的持久快照。"""
    return SimpleNamespace(
        id=message_id,
        run_id="run-a",
        content="",
        extra_metadata={"tool_name": name, "output": json.dumps(payload, ensure_ascii=False), "input": {"kb_id": kb_id}},
    )


def match(**values):
    """构造严格模型 JSON。"""
    return json.dumps(
        {
            "citations": [
                {
                    "answer_start_line": 1,
                    "answer_end_line": 1,
                    "source_id": "s1",
                    "quote": "证据",
                    **values,
                }
            ]
        },
        ensure_ascii=False,
    )


def test_knowledge_results_keep_resource_identity_and_authoritative_span():
    """标准 query_kb 不依赖知识库名称，重排 chunk 保留原文块范围。"""
    sources = collect_reference_sources(
        [
            tool(
                "query_kb",
                {
                    "kb_id": "kb-a",
                    "results": [
                        {
                            "id": "chunk-g3",
                            "file_id": "file-a",
                            "content": "重排后的证据",
                            "metadata": {"source": "报告.md", "generation": 3, "start_line": 20, "end_line": 29},
                        }
                    ],
                },
            )
        ],
        {"kb-a"},
    )
    assert sources[0]["file_id"] == "file-a"
    assert sources[0]["chunk_id"] == "chunk-g3"
    assert sources[0]["generation"] == 3
    citation = validate_reference_matches(match(), "回答", sources)[0]
    assert (citation["source_start_line"], citation["source_end_line"]) == (20, 29)


def test_numbered_open_window_preserves_empty_lines_and_precise_quote_location():
    """行号剥离不吞空行，摘录位置使用真实窗口起始行。"""
    sources = collect_reference_sources(
        [
            tool(
                "open_kb_document",
                {
                    "kb_id": "kb-a",
                    "file_id": "file-a",
                    "start_line": 10,
                    "end_line": 13,
                    "content": "    10\t标题\n    11\t\n    12\t证据\n    13\t后续",
                },
            )
        ],
        {"kb-a"},
    )
    assert sources[0]["content"] == "标题\n\n证据\n后续"
    citation = validate_reference_matches(match(), "回答", sources)[0]
    assert (citation["source_start_line"], citation["source_end_line"]) == (12, 12)


@pytest.mark.parametrize("visible", [{"kb-a", "kb-b", "kb-unrequested"}, {"kb-a"}, set()])
def test_multi_kb_sources_use_each_result_identity_and_current_visibility(visible):
    """多库同名文件不串库，撤权与未请求库的结果不会进入引用。"""
    payload = {
        "kb_ids": ["kb-a", "kb-b"],
        "results": [
            {"kb_id": kb_id, "id": "same-chunk", "file_id": "same-file", "content": "同名证据"}
            for kb_id in ("kb-a", "kb-b", "kb-unrequested")
        ],
    }
    sources = collect_reference_sources([tool("query_kb", payload, kb_id=[" kb-a ", "kb-b", "kb-a"])], visible)
    expected_ids = [kb_id for kb_id in ("kb-a", "kb-b") if kb_id in visible]
    assert [(source["kb_id"], source["file_id"], source["chunk_id"], source["content"]) for source in sources] == [
        (kb_id, "same-file", "same-chunk", "同名证据") for kb_id in expected_ids
    ]


def test_find_windows_include_parent_file_id():
    """find_kb_document 的窗口继承文件身份。"""
    sources = collect_reference_sources(
        [
            tool(
                "find_kb_document",
                {
                    "file_id": "file-a",
                    "windows": [{"start_line": 7, "end_line": 7, "content": "     7\t证据"}],
                },
            )
        ],
        {"kb-a"},
    )
    assert sources[0]["file_id"] == "file-a"
    assert sources[0]["start_line"] == 7


def test_revoked_knowledge_and_non_retrieval_tools_provide_no_sources():
    """无权来源和普通工具不能进入引用输入。"""
    payload = {"results": [{"content": "证据", "file_id": "file-a"}]}
    assert collect_reference_sources([tool("query_kb", payload)], set()) == []
    assert collect_reference_sources([tool("list_kbs", payload)], {"kb-a"}) == []
    assert collect_reference_sources([tool("open_kb_document", {"content": "证据", "file_id": "file-a"}, kb_id=["kb-a"])], {"kb-a"}) == []


@pytest.mark.asyncio
async def test_reference_loading_reads_knowledge_permissions_only_for_knowledge_tools(monkeypatch):
    """网页或空检索不发起知识库 MCP 会话，知识工具结果仍解析实时权限。"""
    from yuxi.modules.agents.services import references

    listed = AsyncMock(
        return_value=[{"kb_id": "kb-a", "name": "A", "description": "", "supports_documents": True, "supports_document_read": True}]
    )
    retrievals = []

    async def list_retrievals(_turn_id):
        """返回当前场景的持久工具快照。"""
        return retrievals

    repo = SimpleNamespace(
        get_result=AsyncMock(return_value=(SimpleNamespace(id="message"), SimpleNamespace(id="run"))),
        list_retrievals=list_retrievals,
    )

    async def get_turn(*_args, **_kwargs):
        """提供存在的 Turn，聚焦检索来源分支。"""
        return object()

    monkeypatch.setattr(references, "require_thread", AsyncMock())
    monkeypatch.setattr(references, "AgentTurnRepository", lambda _db: SimpleNamespace(get_for_scope=get_turn))
    monkeypatch.setattr(references, "TurnReferenceRepository", lambda _db: repo)
    from yuxi.modules.knowledge.mcp import references as knowledge_references

    monkeypatch.setattr(knowledge_references, "list_knowledge_bases", listed)
    scope = SimpleNamespace(uid="user-a", app_id=None)

    retrievals[:] = [tool("web_search", {"results": [{"url": "https://example.com", "content": "网页证据"}]})]
    _message, _run, web_sources = await references._load_reference_input(SimpleNamespace(), scope, "thread-a", "turn-a")
    listed.assert_not_awaited()
    assert [source["url"] for source in web_sources] == ["https://example.com"]

    retrievals[:] = []
    await references._load_reference_input(SimpleNamespace(), scope, "thread-a", "turn-a")
    listed.assert_not_awaited()

    retrievals[:] = [tool("query_kb", {"results": [{"content": "知识证据", "file_id": "file-a"}]})]
    _message, _run, sources = await references._load_reference_input(SimpleNamespace(), scope, "thread-a", "turn-a")
    listed.assert_awaited_once_with("user-a")
    assert sources[0]["supports_documents"] is True


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///secret", "https://user:pass@example.org", "http://"])
def test_unsafe_web_addresses_are_not_citable(url):
    """网页链接边界拒绝执行协议和内嵌凭据。"""
    assert collect_reference_sources([tool("web_search", {"results": [{"url": url, "content": "证据"}]})], set()) == []


def test_empty_error_and_duplicate_web_results():
    """只保留有内容的成功网页证据，重复快照合并。"""
    payload = {"results": [{"url": "https://example.org/a", "title": "资料", "content": "证据"}]}
    sources = collect_reference_sources(
        [
            tool("web_search", payload),
            tool("web_search", payload, message_id=2),
            tool("web_search", {"error": "failed", **payload}),
            tool("web_search", {"results": [{"url": "https://example.org/b", "content": ""}]}),
        ],
        set(),
    )
    assert len(sources) == 1
    assert sources[0]["tool_message_id"] == 1


@pytest.mark.parametrize(
    "values",
    [
        {"source_id": "invented"},
        {"quote": "编造摘录"},
        {"quote": " "},
        {"answer_start_line": 0},
        {"answer_end_line": 99},
        {"answer_start_line": 2, "answer_end_line": 1},
        {"answer_start_line": 2, "answer_end_line": 2},
        {"answer_start_line": "1"},
        {"answer_start_line": True},
        {"unexpected": "field"},
    ],
)
def test_invalid_model_matches_fail_for_the_correct_boundary(values):
    """虚构来源、原文和范围不能成为持久引用。"""
    with pytest.raises(ValueError):
        validate_reference_matches(match(**values), "回答\n\n其他回答", [{"id": "s1", "content": "证据"}])


def test_multiple_sources_and_no_evidence_are_valid_results():
    """同一结论允许多个来源，缺乏证据时不强补引用。"""
    raw = json.dumps({"citations": [json.loads(match())["citations"][0], json.loads(match(source_id="s2"))["citations"][0]]})
    sources = [{"id": "s1", "content": "证据"}, {"id": "s2", "content": "其他证据"}]
    assert [item["source_id"] for item in validate_reference_matches(raw, "回答", sources)] == ["s1", "s2"]
    assert validate_reference_matches('{"citations":[]}', "回答", sources) == []


def test_normalized_search_and_fetch_provide_citable_content():
    """新的摘要字段与独立正文工具都进入真实引用来源。"""
    sources = collect_reference_sources(
        [
            tool(
                "web_search",
                {
                    "results": [
                        {"url": "https://example.org/search", "title": "Search", "relevant_content": "相关片段", "raw_content": "正文"}
                    ]
                },
            ),
            tool("web_fetch", {"url": "https://example.org/page", "title": "Fetch", "content": "# 网页正文"}, message_id=2),
        ],
        set(),
    )
    assert [source["content"] for source in sources] == ["相关片段", "# 网页正文"]


def test_reference_input_keeps_original_lines_for_text_images_and_complete_tables():
    """排除内容不发送给标注模型，表格分隔行和过滤后的原始行号保留。"""
    answer = (
        '# 标题\n\n- 正文 `code` 与 $x$。\n\n  ```widget\n  {"type":"Card","children":[]}\n  ```\n\n'
        "![图片](https://example.org/a.png)\n\n| 项目 | 结果 |\n| --- | --- |\n| A | B |\n\n$$\nx^2\n$$\n\n后续"
    )
    lines = reference_answer_lines(answer)
    assert [line["line"] for line in lines] == [3, 9, 11, 12, 13, 19]
    assert [line["text"] for line in lines] == [
        "- 正文 `code` 与 $x$。",
        "![图片](https://example.org/a.png)",
        "| 项目 | 结果 |",
        "| --- | --- |",
        "| A | B |",
        "后续",
    ]
    citations = validate_reference_matches(match(answer_start_line=11, answer_end_line=13), answer, [{"id": "s1", "content": "证据"}])
    assert (citations[0]["answer_start_line"], citations[0]["answer_end_line"]) == (11, 13)


def test_task_list_text_remains_citable():
    """任务列表中的文字属于正文，不因前端生成 checkbox 而丢失标注。"""
    answer = "- [x] 文字结论\n- [ ] 后续结论"
    assert [line["line"] for line in reference_answer_lines(answer)] == [1, 2]
    assert validate_reference_matches(match(answer_end_line=2), answer, [{"id": "s1", "content": "证据"}])[0]["answer_end_line"] == 2


@pytest.mark.parametrize(
    "excluded",
    [
        "```python\nprint(1)\n```",
        '```widget\n{"type":"Card","children":[{"type":"Text","value":"文字"}]}\n```',
        "$$\nx^2\n$$",
        "$$\nx^2",
        "$$x^2$$ $$y^2$$",
        "    code",
        "<div>HTML 预览</div>",
        "仅有 <span>HTML</span>",
        "$x$",
        "`code`",
        "---\nname: metadata\n---",
    ],
)
def test_excluded_answer_types_cannot_be_cited_or_included_in_cross_block_ranges(excluded):
    """合法行号不能绕过内容白名单，包括跨排除内容的范围。"""
    assert reference_answer_lines(excluded) == []
    answer = f"正文\n\n{excluded}\n\n后续"
    sources = [{"id": "s1", "content": "证据"}]
    for start, end in [(3, 3), (1, len(answer.splitlines()))]:
        with pytest.raises(ValueError, match="只能标注"):
            validate_reference_matches(match(answer_start_line=start, answer_end_line=end), answer, sources)


async def test_annotation_timeout_explains_model_configuration_and_keeps_answer_unmodified(monkeypatch):
    """超时保留失败语义与原回答，并给出管理员调整模型的方向。"""
    from yuxi.modules.agents.services import references

    message = SimpleNamespace(content="正文", extra_metadata={"keep": "original"})
    repository = SimpleNamespace(try_lock=AsyncMock(return_value=True))
    monkeypatch.setattr(references, "require_thread", AsyncMock())
    monkeypatch.setattr(references, "TurnReferenceRepository", lambda _db: repository)
    monkeypatch.setattr(
        references,
        "_load_reference_input",
        AsyncMock(return_value=(message, SimpleNamespace(id="run-a"), [{"id": "s1", "content": "证据"}])),
    )
    monkeypatch.setattr(
        references,
        "system_options",
        SimpleNamespace(get=AsyncMock(return_value={"reference_model": "provider:refs", "fast_model": "provider:fast"})),
    )
    monkeypatch.setattr(
        references, "load_chat_model", lambda *_args, **_kwargs: SimpleNamespace(ainvoke=AsyncMock(side_effect=TimeoutError))
    )

    with pytest.raises(OperationTimeoutError, match="设置中选择关闭思考或思考强度较低的来源标注模型") as error:
        await references.annotate_turn_references(
            db=SimpleNamespace(), scope=SimpleNamespace(uid="user-a"), thread_id="thread-a", turn_id="turn-a"
        )

    assert error.value.code == "references_timeout"
    assert message.content == "正文"
    assert message.extra_metadata == {"keep": "original"}


@pytest.mark.asyncio
async def test_mcp_adapter_recorded_output_preserves_reference_identity_and_span():
    """真实 adapter 和 recorder 转换后保留结构化证据，不能把文本块误当 chunk。"""
    from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
    from mcp import types

    from yuxi.modules.agents.services.message_recorder import tool_event_output

    payload = {
        "kb_id": "kb-a",
        "results": [{"id": "chunk-a", "file_id": "file-a", "content": "独立证据", "metadata": {"start_line": 10, "end_line": 12}}],
    }

    class Session:
        """仅提供固定 MCP 协议结果。"""

        async def call_tool(self, *args, **kwargs):
            """结构化结果与展示文本刻意不同，保证引用读取权威字段。"""
            return types.CallToolResult(content=[types.TextContent(type="text", text="展示摘要")], structuredContent=payload)

    adapter = convert_mcp_tool_to_langchain_tool(Session(), types.Tool(name="query_kb", inputSchema={"type": "object", "properties": {}}))
    result = await adapter.ainvoke({"type": "tool_call", "id": "call-a", "name": "query_kb", "args": {}})
    message = tool("query_kb", {})
    message.extra_metadata["output"] = tool_event_output({"output": result})
    sources = collect_reference_sources([message], {"kb-a"})
    assert [(item["file_id"], item["content"], item["start_line"], item["end_line"]) for item in sources] == [
        ("file-a", "独立证据", 10, 12)
    ]
    assert collect_reference_sources([message], set()) == []


def test_saved_source_identity_is_preserved_while_current_capabilities_are_refreshed():
    """旧引用保持原 ID，仅当前同消息同内容的来源可以重新投影。"""
    from yuxi.modules.agents.services.references import _answer_hash, _visible_references

    saved = {
        "answer_hash": _answer_hash("答案"),
        "sources": [{"id": "s-old", "tool_message_id": 1, "content_hash": "same", "supports_documents": False}],
        "citations": [{"source_id": "s-old", "answer_start_line": 1, "answer_end_line": 1}],
    }
    current = [{"id": "s-new", "tool_message_id": 1, "content_hash": "same", "supports_documents": False, "supports_document_read": True}]
    result = _visible_references(saved, current, "答案")
    assert result["sources"][0]["id"] == "s-old"
    assert result["sources"][0]["supports_document_read"] is True
    assert result["citations"][0]["source_id"] == "s-old"
    assert "supports_document_read" not in saved["sources"][0]
    assert _visible_references(saved, [], "答案")["sources"] == []
    assert _visible_references(saved, [{**current[0], "content_hash": "changed"}], "答案")["citations"] == []


@pytest.mark.parametrize(
    "other_metadata",
    [
        {"file_id": "file-b", "start_line": 100, "end_line": 100},
        {"file_id": "file-a", "start_line": 100, "end_line": 100},
        {"file_id": "file-a", "start_line": 10, "end_line": 10, "chunk_id": "chunk-b"},
    ],
)
def test_saved_sources_with_identical_text_keep_distinct_file_and_window(other_metadata):
    """同工具同正文的不同文件、窗口与分块不得互换引用身份。"""
    from yuxi.modules.agents.services.references import _answer_hash, _visible_references

    message = tool(
        "query_kb",
        [
            {"content": "相同证据", "metadata": {"file_id": "file-a", "start_line": 10, "end_line": 10, "chunk_id": "chunk-a"}},
            {"content": "相同证据", "metadata": other_metadata},
        ],
    )
    original = collect_reference_sources([message], {"kb-a"})
    assert len(original) == 2
    saved = {
        "answer_hash": _answer_hash("答案"),
        "sources": original,
        "citations": [{"source_id": source["id"], "answer_start_line": 1, "answer_end_line": 1} for source in original],
    }
    current = [{**source, "supports_document_read": True} for source in reversed(original)]
    result = _visible_references(saved, current, "答案")
    assert [(source["id"], source["file_id"], source["chunk_id"], source["start_line"]) for source in result["sources"]] == [
        (source["id"], source["file_id"], source["chunk_id"], source["start_line"]) for source in original
    ]
    assert all(source["supports_document_read"] for source in result["sources"])
    filtered = _visible_references(saved, current[:1], "答案")
    assert [source["id"] for source in filtered["sources"]] == [original[1]["id"]]
    assert [citation["source_id"] for citation in filtered["citations"]] == [original[1]["id"]]
