"""临时文件响应在发送失败或取消时也回收磁盘内容。"""

import asyncio

import pytest

from yuxi.api.responses.files import render_file_result
from yuxi.shared.files import PreparedFile


@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
async def test_prepared_file_is_removed_when_response_body_send_fails(tmp_path, failure):
    """发送已经读出的文件时失败，响应保留原异常并回收本地文件。"""
    path = tmp_path / "prepared.txt"
    path.write_bytes(b"contents")
    response = render_file_result(PreparedFile(str(path), "text/plain"))

    async def receive():
        """文件响应无需读取请求体。"""
        raise AssertionError("file response does not consume request body")

    async def send(message):
        """在读取文件字节后模拟连接发送中断。"""
        if message["type"] == "http.response.body":
            assert message["body"] == b"contents"
            raise failure("send interrupted")

    with pytest.raises(failure, match="send interrupted"):
        await response({"type": "http", "method": "GET", "headers": []}, receive, send)
    assert not path.exists()
