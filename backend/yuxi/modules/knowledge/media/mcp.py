"""原生媒体 MCP 协议装配；字节类型与模型观察保持分开。"""

import base64
import json
from urllib.parse import quote

from mcp import types

from yuxi.modules.knowledge.media.schemas import InspectMediaInput, ListMediaInput, ReadMediaInput, SearchMediaInput

MEDIA_TOOLS = {
    "list_kb_media": (ListMediaInput, "发现当前授权文件的原生图片、录音和视频片段，返回media2修订、asset_id、时间码；目录不表示已读原件。"),
    "query_kb_media": (
        SearchMediaInput,
        "检索原始图像像素、视频采样帧或有界原始音频候选。modality必须明确；音频为native_audio_scoring，检查coverage/next_offset，不能宣称全库音频向量检索。",
    ),
    "read_kb_media": (
        ReadMediaInput,
        "读取指定当前media2修订的原图、原始音频片段或视频片段字节；asset_id=original读取有界原件。只有能消费对应模态的模型才可直接核对；其他情况使用inspect_kb_media。",
    ),
    "inspect_kb_media": (
        InspectMediaInput,
        "每次重新读取已授权原件/片段，调用原生多模态模型核对question。返回model_observation而非原文；视频必须选有时间范围的片段，原件视频仅供下载；片段画面与音轨明确分别输入，保留原件hash与时间码。独立检查须另一次调用且保留其operation_id。",
    ),
}


def media_read_result(result: dict) -> types.CallToolResult:
    """标准 MCP 原生内容块携带实际字节，视频使用可解码嵌入资源。"""
    public = {key: value for key, value in result.items() if key not in {"media_bytes", "understanding_model"}}
    data = base64.b64encode(result["media_bytes"]).decode("ascii")
    mime = result["mime_type"]
    if mime.startswith("image/"):
        content = types.ImageContent(type="image", data=data, mimeType=mime)
    elif mime.startswith("audio/"):
        content = types.AudioContent(type="audio", data=data, mimeType=mime)
    else:
        identity = "/".join(quote(result[key], safe="") for key in ("kb_id", "file_id", "revision", "asset_id"))
        content = types.EmbeddedResource(
            type="resource", resource=types.BlobResourceContents(uri=f"yuxi-media:///{identity}", mimeType=mime, blob=data)
        )
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(public, ensure_ascii=False)), content], structuredContent=public
    )
