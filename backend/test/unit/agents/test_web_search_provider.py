"""供应商协议、配置选择与网页工具边界。"""

import json
from typing import get_args
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from yuxi.modules.agents.tool_results import normalize_tool_result
from yuxi.modules.extensions.options import WEB_PROVIDER_OPTIONS
from yuxi.modules.extensions.tools.builtin.web_search import web_fetch, web_search
from yuxi.modules.extensions.tools.registry import get_all_tool_instances
from yuxi.modules.extensions.web import providers as web_providers, services
from yuxi.modules.extensions.web.schemas import FetchInput, SearchInput
from yuxi.shared.errors import InvalidInputError, OperationTimeoutError, UpstreamError


@pytest.fixture
async def configured(monkeypatch):
    """提供可独立选择的三家凭据，不依赖外部部署。"""
    configuration = {"search": "doubao", "fetch": "deepseek", "keys": {provider: f"{provider}-secret" for provider in WEB_PROVIDER_OPTIONS}}
    monkeypatch.setattr(services, "get_web_configuration", AsyncMock(return_value=configuration))
    return configuration


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tavily", "deepseek", "doubao"])
async def test_search_preserves_summary_body_score_and_filters(provider, configured, httpx_mock):
    wire = {
        "tavily": {
            "results": [{"title": "Title", "url": "https://example.com", "content": "Relevant", "raw_content": "Body", "score": 0.8}]
        },
        "deepseek": {
            "results": [
                {
                    "title": "Title",
                    "url": "https://example.com",
                    "snippet": "Short",
                    "summary": "Relevant",
                    "content": "Body",
                    "rerank_score": 2.4,
                }
            ]
        },
        "doubao": {
            "Result": {
                "WebResults": [
                    {
                        "Title": "Title",
                        "Url": "https://example.com",
                        "Snippet": "Short",
                        "Summary": "Relevant",
                        "Content": "Body",
                        "RankScore": 0.8,
                    }
                ]
            }
        },
    }
    httpx_mock.add_response(url=web_providers.endpoint(provider, "search"), json=wire[provider])
    result = await services.search_web(
        SearchInput(
            query="Python",
            count=3,
            sites=["python.org"],
            block_hosts=["example.org"],
            time_range="2026-01-01..2026-02-01",
            include_raw_content=True,
            content_format="markdown",
        ),
        provider=provider,
    )
    item = result["results"][0]
    assert result["provider"] == provider
    assert item["title"] == "Title"
    assert item["relevant_content"] == "Relevant"
    assert item["raw_content"] == "Body"
    assert item["score"] == (2.4 if provider == "deepseek" else 0.8)
    assert item["score_max"] == (3 if provider == "deepseek" else 1)
    assert result["response_time"] >= 0
    request = httpx_mock.get_request()
    assert request.headers["authorization"] == f"Bearer {provider}-secret"
    payload = json.loads(request.content)
    if provider == "deepseek":
        assert payload == {
            "query": "Python site:python.org -site:example.org after:2026-01-01 before:2026-02-01",
            "count": 3,
            "need_content": True,
        }
    elif provider == "tavily":
        assert payload["include_domains"] == ["python.org"]
        assert payload["exclude_domains"] == ["example.org"]
        assert payload["start_date"] == "2026-01-01"
        assert payload["include_raw_content"] == "markdown"
        assert payload["search_depth"] == "basic"
    else:
        assert payload["Filter"] == {"NeedUrl": True, "Sites": "python.org", "BlockHosts": "example.org"}
        assert payload["ContentFormats"] == "markdown"
        assert "NeedContent" not in payload["Filter"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tavily", "deepseek"])
async def test_fetch_returns_markdown_with_requested_url(provider, configured, httpx_mock):
    wire = (
        {"results": [{"url": "https://example.com", "raw_content": "# Body"}]}
        if provider == "tavily"
        else {"title": "Title", "content": "# Body"}
    )
    httpx_mock.add_response(url=web_providers.endpoint(provider, "fetch"), json=wire)
    result = await services.fetch_web(FetchInput(url="https://example.com"), provider=provider)
    assert result["url"] == "https://example.com"
    assert result["content"] == "# Body"
    assert result["provider"] == provider
    assert result["format"] == "markdown"
    payload = json.loads(httpx_mock.get_request().content)
    assert payload == (
        {"urls": ["https://example.com"], "format": "markdown", "extract_depth": "basic"}
        if provider == "tavily"
        else {"url": "https://example.com"}
    )


@pytest.mark.asyncio
async def test_empty_search_is_success_and_raw_body_is_opt_in(configured, httpx_mock):
    httpx_mock.add_response(json={"Result": {"WebResults": []}})
    assert (await services.search_web(SearchInput(query="none")))["results"] == []
    httpx_mock.add_response(json={"Result": {"WebResults": [{"Title": "T", "Url": "https://example.com", "Content": "Full"}]}})
    result = await services.search_web(SearchInput(query="none"))
    assert result["results"][0]["raw_content"] == ""
    assert result["results"][0]["relevant_content"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tavily", "deepseek", "doubao"])
async def test_registered_search_quota_failure_is_error_tool_message(configured, httpx_mock, provider):
    """额度失败通过真实注册工具保留错误结局，不能伪装为空列表。"""
    configured["search"] = provider
    httpx_mock.add_response(status_code=432, json={"error": "private-provider-response"})
    result = await web_search.ainvoke({"type": "tool_call", "id": "quota-failure", "name": "web_search", "args": {"query": "q"}})
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    normalized = normalize_tool_result({"content": result.content, "status": result.status})
    assert normalized.error == "供应商额度已用尽"
    assert normalized.public_content == "供应商额度已用尽"
    assert "private-provider-response" not in normalized.audit_content


@pytest.mark.asyncio
async def test_registered_search_empty_results_remain_success(configured, httpx_mock):
    """成功零结果与失败的 ToolMessage 和公开结局可区分。"""
    httpx_mock.add_response(json={"Result": {"WebResults": []}})
    result = await web_search.ainvoke({"type": "tool_call", "id": "empty-search", "name": "web_search", "args": {"query": "q"}})
    assert result.status == "success"
    normalized = normalize_tool_result({"content": result.content, "status": result.status})
    assert normalized.error is None
    assert json.loads(normalized.public_content)["results"] == []


@pytest.mark.asyncio
async def test_draft_key_does_not_read_or_save_configuration(monkeypatch, httpx_mock):
    configuration = AsyncMock(side_effect=AssertionError("draft must not read saved configuration"))
    monkeypatch.setattr(services, "get_web_configuration", configuration)
    httpx_mock.add_response(json={"results": []})
    await services.search_web(SearchInput(query="query"), provider="tavily", api_key="draft-secret")
    assert httpx_mock.get_request().headers["authorization"] == "Bearer draft-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 404, 429, 432, 500])
async def test_provider_error_is_sanitized_without_fallback(status, configured, httpx_mock):
    httpx_mock.add_response(status_code=status, json={"error": "deepseek-secret"})
    with pytest.raises(UpstreamError) as error:
        await services.fetch_web(FetchInput(url="https://example.com"))
    assert "secret" not in str(error.value)
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_timeout_and_malformed_success_fail_explicitly(configured, httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("deepseek-secret"))
    with pytest.raises(OperationTimeoutError, match="超时"):
        await services.fetch_web(FetchInput(url="https://example.com"))
    httpx_mock.add_response(json={"results": "invalid"})
    with pytest.raises(UpstreamError, match="无效结果"):
        await services.search_web(SearchInput(query="query"), provider="deepseek")
    httpx_mock.add_response(json={"results": [], "failed_results": [{"error": "no content"}]})
    with pytest.raises(UpstreamError, match="正文"):
        await services.fetch_web(FetchInput(url="https://example.com"), provider="tavily")


@pytest.mark.asyncio
async def test_missing_credentials_and_unsupported_fetch_do_not_send_requests(configured):
    configured["keys"]["doubao"] = ""
    with pytest.raises(InvalidInputError, match="未配置"):
        await services.search_web(SearchInput(query="query"))
    assert await services.available_web_tools() == {"web_fetch"}
    with pytest.raises(InvalidInputError, match="不支持"):
        await services.fetch_web(FetchInput(url="https://example.com"), provider="doubao", api_key="key")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,input_values",
    [
        ("tavily", {"query": "x", "count": 21}),
        ("doubao", {"query": "x" * 101}),
        ("deepseek", {"query": "x" * 120, "sites": ["example.com"]}),
        ("deepseek", {"query": "x", "include_raw_content": True, "content_format": "text"}),
    ],
)
async def test_provider_limits_are_not_silently_truncated(provider, input_values, configured):
    with pytest.raises(InvalidInputError):
        await services.search_web(SearchInput(**input_values), provider=provider)


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "https:///missing", "https://user:pass@example.com", "https://example.com\n", "https://example.com:bad"]
)
def test_fetch_rejects_non_web_urls_and_credentials(url):
    with pytest.raises(ValidationError):
        FetchInput(url=url)


@pytest.mark.parametrize(
    "values", [{"query": " "}, {"query": "q", "time_range": "Yesterday"}, {"query": "q", "sites": ["example.com OR evil"]}]
)
def test_search_input_rejects_invalid_filters(values):
    with pytest.raises(ValidationError):
        SearchInput(**values)


def test_tools_register_stably_without_credentials():
    tools = {tool.name: tool for tool in get_all_tool_instances()}
    assert tools["web_search"] is web_search
    assert tools["web_fetch"] is web_fetch
    assert "provider" not in web_search.args_schema.model_fields
    assert "api_key" not in web_fetch.args_schema.model_fields


def test_provider_catalog_drives_settings_options_and_choices():
    """新增或重命名供应商只需改目录，配置定义与默认选项必须同步派生。"""
    from yuxi.modules.extensions.web.schemas import FetchProvider, SearchProvider
    from yuxi.modules.system.options import system_options
    from yuxi.shared.web_providers import FETCH_PROVIDER_IDS, PROVIDERS_BY_ID, SEARCH_PROVIDER_IDS

    assert get_args(SearchProvider) == SEARCH_PROVIDER_IDS
    assert get_args(FetchProvider) == FETCH_PROVIDER_IDS
    fields = {field["key"]: field for field in system_options.fields}
    assert fields["web_search_provider"]["choices"] == ["", *SEARCH_PROVIDER_IDS]
    assert fields["web_fetch_provider"]["choices"] == ["", *FETCH_PROVIDER_IDS]
    for provider_id, option in WEB_PROVIDER_OPTIONS.items():
        metadata = option.params["provider"]
        assert metadata["id"] == provider_id
        assert metadata["supports_fetch"] == PROVIDERS_BY_ID[provider_id].supports_fetch
        assert metadata["environment"] == PROVIDERS_BY_ID[provider_id].environment
        assert option.fields[0]["environment"] == PROVIDERS_BY_ID[provider_id].environment


@pytest.mark.asyncio
async def test_defaults_honor_environment_and_do_not_replace_explicit_missing_key(monkeypatch):
    """自动选择遵循顺序，显式选择缺少凭据时保持不可用。"""
    from yuxi.modules.system.options import system_options
    from yuxi.modules.system.services.options import Option

    stored = {option.key: {} for option in WEB_PROVIDER_OPTIONS.values()}
    stored[system_options.key] = {}

    async def read(option, db=None):
        """解析隔离的存储输入与真实环境变量优先级。"""
        return option.resolve(stored[option.key])

    monkeypatch.setattr(Option, "get", read)
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    for name in ("TAVILY_API_KEY", "DEEPSEEK_API_KEY", "DOUBAO_SEARCH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-deepseek")
    monkeypatch.setenv("TAVILY_API_KEY", "env-tavily")
    assert (await services.get_web_configuration())["search"] == "tavily"
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", " TAVILY ")
    assert (await services.get_web_configuration())["search"] == "tavily"
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "doubao")
    assert await services.available_web_tools() == {"web_fetch"}
    stored["tavily_search_opts"] = {"api_key": "database-key"}
    stored[system_options.key] = {"web_search_provider": "tavily", "web_fetch_provider": "deepseek"}
    current = await services.get_web_configuration()
    assert current["keys"]["tavily"] == "database-key"
    assert (current["search"], current["fetch"]) == ("tavily", "deepseek")
    stored["tavily_search_opts"] = {"api_key": ""}
    assert (await services.get_web_configuration())["keys"]["tavily"] == "env-tavily"


@pytest.mark.asyncio
async def test_runtime_excludes_unconfigured_web_tools_and_binds_after_configuration(monkeypatch, configured):
    """稳定注册不代表可执行能力，新装配必须依据有效凭据。"""
    from types import SimpleNamespace
    from yuxi.modules.extensions.tools.runtime import resolve_configured_runtime_tools

    context = SimpleNamespace(tools=["web_search", "web_fetch"], mcps=[], skill_runtime_snapshot=None)
    configured["keys"] = {provider: "" for provider in WEB_PROVIDER_OPTIONS}
    assert await resolve_configured_runtime_tools(context) == []
    configured["keys"]["doubao"] = "new-doubao-key"
    configured["keys"]["deepseek"] = "new-deepseek-key"
    assert {tool.name for tool in await resolve_configured_runtime_tools(context)} >= {"web_search", "web_fetch"}


@pytest.mark.asyncio
async def test_existing_tool_reads_new_provider_on_next_call(configured, httpx_mock):
    """同一个注册实例不冻结启动时供应商。"""
    httpx_mock.add_response(json={"Result": {"WebResults": []}})
    first = await web_search.ainvoke({"query": "query"})
    configured["search"] = "tavily"
    httpx_mock.add_response(json={"results": []})
    second = await web_search.ainvoke({"query": "query"})
    assert (first["provider"], second["provider"]) == ("doubao", "tavily")


@pytest.mark.asyncio
async def test_deepseek_body_switch_works_without_provider_specific_arguments(configured, httpx_mock):
    """正文开关使用统一默认格式，不要求模型知道当前供应商。"""
    configured["search"] = "deepseek"
    httpx_mock.add_response(
        json={"results": [{"title": "Page", "url": "https://example.com", "summary": "Relevant", "content": "# Full body"}]}
    )
    result = await web_search.ainvoke({"query": "q", "include_raw_content": True})
    assert result["results"][0]["raw_content"] == "# Full body"
    assert json.loads(httpx_mock.get_request().content)["need_content"] is True


@pytest.mark.asyncio
async def test_provider_cannot_return_executable_result_url(configured, httpx_mock):
    """供应商结果属于外部输入，拒绝可执行链接。"""
    httpx_mock.add_response(json={"Result": {"WebResults": [{"Title": "Page", "Url": "javascript:alert(1)"}]}})
    with pytest.raises(UpstreamError, match="无效结果"):
        await services.search_web(SearchInput(query="q"))
