"""把中立 Workspace preview 结果装配为 HTTP 响应。"""

import os
from contextlib import suppress
from urllib.parse import quote

from fastapi.responses import FileResponse, Response
from starlette.types import Receive, Scope, Send

from yuxi.shared.files import PreparedFile, PreviewResult


def render_file_result(result: PreparedFile | PreviewResult | dict) -> Response | dict:
    """把已准备好的业务文件结果装配为 HTTP 响应。"""
    if isinstance(result, PreparedFile):
        headers = None
        if result.explicit_disposition and result.filename:
            headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(result.filename)}"}
        try:
            return _TemporaryFileResponse(
                result.path,
                media_type=result.media_type,
                filename=result.filename if not result.explicit_disposition else None,
                headers=headers,
                content_disposition_type="attachment",
            )
        except Exception:
            os.unlink(result.path)
            raise
    if isinstance(result, PreviewResult):
        return _preview_response(result)
    return result


class _TemporaryFileResponse(FileResponse):
    """让响应在成功、范围拒绝和中断时都归还临时文件。"""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """沿用 FileResponse 的协议，退出时回收文件。"""
        try:
            await super().__call__(scope, receive, send)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(self.path)


def _preview_response(result: PreviewResult) -> dict | Response:
    if not isinstance(result.content, bytes):
        return result.payload()
    filename = quote(result.filename or "preview")
    disposition = "attachment" if result.preview_type == "office" else "inline"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
        "X-Yuxi-Preview-Type": result.preview_type,
        "X-Yuxi-Preview-Filename": filename,
    }
    if result.preview_type == "office":
        headers["X-Content-Type-Options"] = "nosniff"
    return Response(result.content, media_type=result.media_type or "application/octet-stream", headers=headers)
