"""飞书连接器的远端协议、空间边界及实时阅读测试。"""

import json
from dataclasses import replace

import httpx
import pytest

from yuxi.modules.knowledge.implementations.feishu import FeishuKB
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig
from yuxi.shared.errors import UpstreamError


@pytest.fixture
def config():
    """提供虚构的连接配置。"""
    return KnowledgeBaseConfig(
        kb_id="kb-feishu", kb_type="feishu", additional_params={"feishu_user_token": "test-secret", "feishu_space_id": "123"}
    )


@pytest.fixture
def connector(tmp_path):
    """创建不拥有本地文档的执行器。"""
    return FeishuKB(str(tmp_path))


def install_transport(monkeypatch, handler):
    """在 HTTP wire 边界替换远端，保留真实请求序列化与响应解析。"""
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "yuxi.modules.knowledge.implementations.feishu.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )


def success(data):
    """生成飞书协议响应。"""
    return httpx.Response(200, json={"code": 0, "data": data})


@pytest.mark.asyncio
async def test_search_filters_types_paginates_and_returns_readable_node_ids(connector, config, monkeypatch):
    """非文档不占返回名额，分页候选保留可阅读的节点 ID。"""

    def handler(request):
        """按测试场景返回远端协议结果。"""
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert json.loads(request.content) == {"query": "设计", "space_id": "123"}
        assert request.url.path == "/open-apis/wiki/v2/nodes/search"
        if request.url.params.get("page_token") is None:
            return success({"items": [{"space_id": "123", "obj_type": 2}], "has_more": True, "page_token": "page2"})
        assert request.url.params["page_token"] == "page2"
        return success({"items": [{"space_id": "123", "obj_type": 8, "node_id": "Wiki123", "title": "设计规范"}], "has_more": False})

    install_transport(monkeypatch, handler)
    results = await connector.aquery("设计", config.kb_id, config=config)
    output = connector.build_search_output(config.kb_id, results)
    assert output["results"][0]["file_id"] == "Wiki123"
    assert output["results"][0]["content"] == "设计规范"
    assert output["results"][0]["metadata"]["result_type"] == "document"
    assert "score" not in output["results"][0]["metadata"]


@pytest.mark.asyncio
async def test_read_resolves_node_and_returns_fresh_window(connector, config, monkeypatch):
    """每次读取重新鉴权并获取正文，行窗口跟随远端更新。"""
    contents = iter(["首行\n旧内容\n尾行", "首行\n新内容\n尾行"])

    def handler(request):
        """按测试场景返回远端协议结果。"""
        if request.url.path.endswith("get_node"):
            assert request.url.params["token"] == "Wiki123"
            return success({"node": {"space_id": "123", "obj_type": "docx", "obj_token": "Doc123"}})
        assert request.url.path == "/open-apis/docx/v1/documents/Doc123/raw_content"
        return success({"content": next(contents)})

    install_transport(monkeypatch, handler)
    first = await connector.open_document(config.kb_id, "Wiki123", config=config, offset=1, limit=1)
    second = await connector.open_document(config.kb_id, "Wiki123", config=config, offset=1, limit=1)
    assert first["content"] == "     2\t旧内容"
    assert second["content"] == "     2\t新内容"
    assert second["total_lines"] == 3
    assert second["next_offset"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node,error",
    [
        ({"space_id": "456", "obj_type": "docx", "obj_token": "Outside"}, "不属于"),
        ({"space_id": "123", "obj_type": "sheet", "obj_token": "Sheet"}, "仅支持"),
        ({"space_id": "123", "obj_type": "docx", "obj_token": "Target", "node_type": "shortcut"}, "快捷方式"),
        ({"space_id": "123", "obj_type": "docx", "obj_token": "../outside"}, "ID 无效"),
    ],
)
async def test_read_rejects_outside_or_unsupported_nodes_before_reading(connector, config, monkeypatch, node, error):
    """伪造 file_id 不能利用共享凭证读取其他空间或资源。"""

    def handler(request):
        """按测试场景返回远端协议结果。"""
        assert request.url.path.endswith("get_node"), "越界正文不应被请求"
        return success({"node": node})

    install_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match=error):
        await connector.open_document(config.kb_id, "Wiki123", config=config)


@pytest.mark.asyncio
async def test_search_rejects_cross_space_results(connector, config, monkeypatch):
    """远端返回空间不符时拒绝暴露候选内容。"""
    install_transport(monkeypatch, lambda _: success({"items": [{"space_id": "456", "obj_type": 8, "title": "outside"}]}))
    with pytest.raises(ValueError, match="超出"):
        await connector.aquery("test", config.kb_id, config=config)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"code": 99991663, "msg": "test-secret"}),
        httpx.Response(403, text="test-secret"),
        httpx.Response(429, text="test-secret"),
        httpx.Response(200, json={"data": {}}),
        httpx.Response(200, text="not json"),
    ],
)
async def test_remote_failure_is_not_empty_success_or_secret_leak(connector, config, monkeypatch, response):
    """无权限、过期和无效协议必须失败，错误不包含远端敏感正文。"""
    install_transport(monkeypatch, lambda _: response)
    with pytest.raises(UpstreamError) as error:
        await connector.aquery("test", config.kb_id, config=config)
    assert "test-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_search_rejects_broken_pagination(connector, config, monkeypatch):
    """重复游标不能令检索无限循环。"""
    install_transport(monkeypatch, lambda _: success({"items": [], "has_more": True, "page_token": "same"}))
    with pytest.raises(ValueError, match="分页"):
        await connector.aquery("test", config.kb_id, config=config)


@pytest.mark.asyncio
async def test_connections_do_not_share_tokens(connector, config, monkeypatch):
    """同类型共享执行器在每次调用使用对应知识库凭证。"""

    def handler(request):
        """按测试场景返回远端协议结果。"""
        connection = json.loads(request.content)
        assert request.headers["Authorization"] == {"123": "Bearer test-secret", "456": "Bearer other-secret"}[connection["space_id"]]
        return success({"items": [], "has_more": False})

    install_transport(monkeypatch, handler)
    await connector.aquery("test", config.kb_id, config=config)
    other = replace(config, kb_id="other", additional_params={"feishu_user_token": "other-secret", "feishu_space_id": "456"})
    await connector.aquery("test", other.kb_id, config=other)


@pytest.mark.asyncio
async def test_readonly_rejects_parsing_and_invalid_ids(connector, config):
    """在线阅读不开放文档写操作，路径输入在请求前拒绝。"""
    with pytest.raises(ValueError, match="只读"):
        await connector.parse_file(config.kb_id, "file", additional_params=config.additional_params)
    with pytest.raises(ValueError, match="ID 无效"):
        await connector.open_document(config.kb_id, "../outside", config=config)


@pytest.mark.parametrize(
    "params",
    [{}, {"feishu_user_token": "token", "feishu_space_id": "bad"}, {"feishu_user_token": "Bearer token", "feishu_space_id": "123"}],
)
def test_invalid_connection_rejected(params):
    """配置边界拒绝缺失凭据与非标识输入。"""
    with pytest.raises(ValueError):
        FeishuKB.normalize_additional_params(params)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "a" * 51])
async def test_invalid_query_rejected_before_remote_request(connector, config, monkeypatch, query):
    """超出飞书搜索契约的关键词不发起远端请求。"""

    def handler(_request):
        """拒绝不应发生的远端请求。"""
        pytest.fail("非法查询不得请求远端")

    install_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="关键词长度"):
        await connector.aquery(query, config.kb_id, config=config)


@pytest.mark.asyncio
@pytest.mark.parametrize("route_name", ["query_test", "query_knowledge_base"])
async def test_remote_domain_error_reaches_http_error_adapter(monkeypatch, route_name):
    """Web 查询保留脱敏领域错误，避免失败被投影为成功或通用错误。"""
    import json
    from types import SimpleNamespace

    from yuxi.api.errors import domain_error_handler
    from yuxi.api.routers.knowledge import management

    async def fail(*args, **kwargs):
        """模拟已脱敏的上游失败。"""
        raise UpstreamError("飞书用户 token 已过期", code="feishu_request_failed")

    monkeypatch.setattr(management.knowledge_base_manager, "retrieve", fail)
    with pytest.raises(UpstreamError) as error:
        await getattr(management, route_name)("kb-feishu", query="test", meta={}, current_user=SimpleNamespace(uid="reader"))
    response = await domain_error_handler(None, error.value)
    assert response.status_code == 502
    assert json.loads(response.body) == {"detail": {"code": "feishu_request_failed", "message": "飞书用户 token 已过期"}}
