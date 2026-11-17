"""ToolMediaPersistMiddleware 单元测试。

背景：设备工具返回的截图是 MCP image 内容块，此前只活在对话上下文里——
模型无法在答案中引用、用户无法回看（公开投影把混合内容整体字符串化）。
中间件在工具结果进入状态前把 image 块写入 Workdir outputs/tool_media/，
模型拿到路径注记，公开投影与历史回看共享同一份文件事实。

测试在真实宿主用户目录上执行（YUXI_USER_DATA_DIR 指向 tmp_path），从磁盘回读核验。
"""

import asyncio
import base64
import hashlib
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from yuxi.modules.agents.runtime.middlewares import ToolErrorGuardMiddleware
from yuxi.modules.agents.runtime.middlewares.tool_media import SAVED_MEDIA_KEY, ToolMediaPersistMiddleware, create_tool_media_middleware
from yuxi.modules.workspace.paths import ensure_user_workspace, user_workspace_dir
from yuxi.modules.workspace.runtime_paths import WORKSPACE_PATH

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_MEDIA_DIR = f"{WORKSPACE_PATH}/projects/p1/outputs/tool_media"
_PNG_B64 = base64.b64encode(b"fake-png-bytes").decode()
_JPG_B64 = base64.b64encode(b"fake-jpg-bytes").decode()


@pytest.fixture
def middleware(tmp_path, monkeypatch):
    """真实用户目录承载写入，回读磁盘证明而不是 mock 调用次数。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "users"))
    ensure_user_workspace("media-user")
    return ToolMediaPersistMiddleware(uid="media-user", media_dir=_MEDIA_DIR)


def _request(call_id: str = "call-1", name: str = "computer_observe") -> ToolCallRequest:
    events = []
    return ToolCallRequest(
        tool_call={"id": call_id, "name": name, "args": {}},
        tool=None,
        state=None,
        runtime=SimpleNamespace(context=None, config={}, stream_writer=events.append, emitted=events),
    )


def _async_result(value):
    """返回携带固定结果的异步 handler。"""

    async def handler(_request):
        return value

    return handler


def _image_message() -> ToolMessage:
    return ToolMessage(
        content=[
            {"type": "text", "text": '{"status":"executed"}'},
            {"type": "image", "base64": _PNG_B64, "mime_type": "image/png"},
        ],
        name="computer_observe",
        tool_call_id="call-1",
    )


async def _persist(middleware, request, message):
    """按生产装配顺序执行：Guard 外层、媒体落盘内层。"""

    async def handler(_request):
        return message

    async def media_handler(request):
        return await middleware.awrap_tool_call(request, handler)

    return await ToolErrorGuardMiddleware().awrap_tool_call(request, media_handler)


async def test_persists_image_block_and_annotates_path(middleware):
    """image 块按内容寻址落盘，路径注记与投影元数据随结果发布。"""
    request = _request()
    result = await _persist(middleware, request, _image_message())

    assert isinstance(result, ToolMessage)
    digest = hashlib.sha256(b"fake-png-bytes").hexdigest()[:16]
    path = f"{_MEDIA_DIR}/computer_observe-{digest}.png"
    assert (user_workspace_dir("media-user") / "projects/p1/outputs/tool_media" / f"computer_observe-{digest}.png").read_bytes() == b"fake-png-bytes"
    # image 块保留供模型视觉使用，末尾追加路径注记。
    assert result.content[0] == {"type": "text", "text": '{"status":"executed"}'}
    assert result.content[1] == {"type": "image", "base64": _PNG_B64, "mime_type": "image/png"}
    assert result.content[-1] == {"type": "text", "text": f"[Tool result images saved to sandbox]\n{path}"}
    assert result.additional_kwargs[SAVED_MEDIA_KEY] == [{"index": 1, "path": path, "mime_type": "image/png"}]
    # Guard 发布的审计/公开投影 payload 携带同一份保存事实。
    [event] = [item["data"] for item in request.runtime.emitted]
    assert event["event"] == "tool-result"
    assert event["output"].additional_kwargs[SAVED_MEDIA_KEY][0]["path"] == path


async def test_sync_wrap_persists_image_block(middleware):
    """同步包装入口同样落盘并注记。"""
    message = _image_message()
    result = middleware.wrap_tool_call(_request(), lambda _request: message)
    assert SAVED_MEDIA_KEY in result.additional_kwargs
    assert result.content[-1]["text"].startswith("[Tool result images saved to sandbox]")


async def test_rebuilds_command_result_in_place(middleware):
    """Command 结果只替换本次调用的 ToolMessage，其他消息保持原样。"""
    other = ToolMessage(content="other", tool_call_id="other-call")
    command = Command(update={"messages": [other, _image_message()]})

    result = await middleware.awrap_tool_call(_request(), _async_result(command))

    assert isinstance(result, Command)
    updated = result.update["messages"][1]
    assert SAVED_MEDIA_KEY in updated.additional_kwargs
    assert result.update["messages"][0] is other


async def test_multiple_images_map_to_content_indexes(middleware):
    """多个 image 块逐个落盘，index 与 content 位置一一对应。"""
    message = ToolMessage(
        content=[
            {"type": "image", "base64": _PNG_B64, "mime_type": "image/png"},
            {"type": "text", "text": "between"},
            {"type": "image", "base64": _JPG_B64, "mime_type": "image/jpeg"},
        ],
        name="browser_open",
        tool_call_id="call-1",
    )
    result = await middleware.awrap_tool_call(_request(name="browser_open"), _async_result(message))
    records = result.additional_kwargs[SAVED_MEDIA_KEY]
    assert [record["index"] for record in records] == [0, 2]
    assert records[0]["path"].endswith(".png")
    assert records[1]["path"].endswith(".jpg")


async def test_nested_source_image_shape_is_persisted(middleware):
    """Anthropic 风格 source 嵌套结构的 image 块同样落盘。"""
    message = ToolMessage(
        content=[{"type": "image", "source": {"type": "base64", "data": _PNG_B64, "media_type": "image/png"}}],
        name="computer_observe",
        tool_call_id="call-1",
    )
    result = await middleware.awrap_tool_call(_request(), _async_result(message))
    assert SAVED_MEDIA_KEY in result.additional_kwargs


@pytest.mark.parametrize(
    "content",
    [
        "plain string result",
        [{"type": "text", "text": "text only"}],
        [{"type": "image", "base64": "", "mime_type": "image/png"}],
        [{"type": "image", "base64": _PNG_B64, "mime_type": "image/bmp"}],
        [{"type": "video", "url": "https://example.com/v.mp4"}],
    ],
)
async def test_unsupported_results_stay_untouched(middleware, content):
    """纯文本、空数据、非白名单类型与未知块的结果原样返回。"""
    message = ToolMessage(content=content, name="any", tool_call_id="call-1")
    result = await middleware.awrap_tool_call(_request(), _async_result(message))
    assert result is message


async def test_error_status_and_foreign_call_id_stay_untouched(middleware):
    """失败结果与不属于本次调用的消息不落盘。"""
    error = ToolMessage(content=[{"type": "image", "base64": _PNG_B64, "mime_type": "image/png"}], tool_call_id="call-1", status="error")
    assert await middleware.awrap_tool_call(_request(), _async_result(error)) is error
    foreign = ToolMessage(content=[{"type": "image", "base64": _PNG_B64, "mime_type": "image/png"}], tool_call_id="other")
    assert await middleware.awrap_tool_call(_request(), _async_result(foreign)) is foreign


async def test_write_failure_degrades_to_original_result(middleware, monkeypatch):
    """写盘失败只降级不阻塞工具链路，且不泄漏异常细节。"""
    message = _image_message()

    def broken_write(*_args, **_kwargs):
        raise OSError("disk path: /secret/endpoint")

    monkeypatch.setattr(middleware._files, "write", broken_write)
    result = await asyncio.to_thread(middleware.wrap_tool_call, _request(), lambda _request: message)
    assert result is message
    # 异步路径同样降级：抛错被隔离为告警，结果保持原样。
    async_result = await middleware.awrap_tool_call(_request(), _async_result(message))
    assert async_result is message


def test_factory_requires_uid_and_workdir(tmp_path, monkeypatch):
    """没有用户或 Workdir 时不装配；两者都在时与沙盒 backend 无关。"""
    assert create_tool_media_middleware(SimpleNamespace(uid="u", workdir_relative_path="")) is None
    assert create_tool_media_middleware(SimpleNamespace(uid="", workdir_relative_path="projects/p1")) is None
    middleware = create_tool_media_middleware(SimpleNamespace(uid="u", workdir_relative_path="projects/p1"))
    assert middleware is not None
    assert middleware._media_dir == _MEDIA_DIR


async def test_graph_mounts_media_inside_guard_when_backend_is_absent(monkeypatch):
    """checkpoint 清理图传入 backend=None 时，只要有用户和 Workdir 就仍装配落盘。"""
    from yuxi.modules.agents.runtime.agent_backends.chatbot import graph as chatbot_graph

    monkeypatch.setattr(chatbot_graph, "create_agent_filesystem_middleware", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(chatbot_graph, "create_summary_middleware_from_context", lambda *_args, **_kwargs: object())

    async def no_memory(_context):
        return None

    monkeypatch.setattr(chatbot_graph, "create_memory_middleware", no_memory)
    context = SimpleNamespace(
        uid="media-user",
        workdir_relative_path="projects/p1",
        tool_approval_mode="default",
        tool_token_limit=8,
    )
    middlewares = await chatbot_graph._build_middlewares(context, None)
    assert isinstance(middlewares[0], ToolErrorGuardMiddleware)
    assert isinstance(middlewares[1], ToolMediaPersistMiddleware)
