"""设备工具截图落盘后经 artifact 预览通道回看的真实 HTTP 契约。"""

import base64
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from test.integration.api.helpers import create_default_agent_thread, delete_created_threads, get_thread_workdir_path
from yuxi.modules.agents.runtime.base import json_safe
from yuxi.modules.agents.runtime.middlewares.tool_media import create_tool_media_middleware
from yuxi.modules.agents.services.message_recorder import RunMessageRecorder
from yuxi.modules.agents.tool_results import normalize_tool_result

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4AAAAABJRU5ErkJggg==")


@pytest.mark.self_managed_resources
async def test_persisted_tool_image_projects_to_authorized_preview_url(test_client, standard_user, admin_headers):
    """落盘截图进入公开投影与预览通道，公开内容不携带 base64，越权拒绝。"""
    headers = standard_user["headers"]
    uid = str(standard_user["user"]["uid"])
    thread_id = await create_default_agent_thread(test_client, headers, title_prefix="tool-media")
    try:
        workdir = await get_thread_workdir_path(thread_id)
        middleware = create_tool_media_middleware(SimpleNamespace(uid=uid, workdir_relative_path=workdir))
        assert middleware is not None

        message = ToolMessage(
            content=[
                {"type": "text", "text": '{"status":"executed"}'},
                {"type": "image", "base64": base64.b64encode(_PNG).decode(), "mime_type": "image/png"},
            ],
            name="browser_open",
            tool_call_id="call-1",
        )
        request = ToolCallRequest(
            tool_call={"id": "call-1", "name": "browser_open", "args": {}},
            tool=None,
            state=None,
            runtime=SimpleNamespace(context=None, config={}, stream_writer=lambda _data: None),
        )

        async def handler(_request):
            return message

        persisted = await middleware.awrap_tool_call(request, handler)
        recorder = RunMessageRecorder(run_id="integration-run", thread_id=thread_id, worker_id="integration-worker")
        result = normalize_tool_result(json_safe(persisted), artifact_url_factory=recorder._artifact_preview_url)

        # 模型在结果末尾拿到可引用的沙盒路径。
        assert persisted.content[-1]["text"].startswith("[Tool result images saved to sandbox]")
        # 公开投影引用预览地址而不是内嵌 base64。
        [text_block, image_block] = result.public_content
        assert text_block == {"type": "input_text", "text": '{"status":"executed"}'}
        url = image_block["image_url"]
        assert url.startswith(f"/api/v1/agents/sessions/{thread_id}/artifacts/")
        assert "?preview=true" in url
        assert base64.b64encode(_PNG).decode() not in str(result.public_content)

        response = await test_client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("image/png")
        assert response.content == _PNG
        anonymous = await test_client.get(url)
        assert anonymous.status_code == 401
        cross_user = await test_client.get(url, headers=admin_headers)
        assert cross_user.status_code == 404
    finally:
        await delete_created_threads(thread_id)
