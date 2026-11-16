"""工具结果公开投影的图片块单元测试。

背景：normalize_tool_result 此前把含 image 块的混合内容整体 JSON 字符串化，
前端无法按图片渲染，1.9MB base64 也整段进入 public_items。落盘机制上线后，
投影应优先输出已保存 artifact 的预览地址，退回 data URL，最后才退回 JSON 文本。
"""

import base64

import pytest

from yuxi.modules.agents.services.message_recorder import RunMessageRecorder
from yuxi.modules.agents.tool_results import MEDIA_NOTE_PREFIX, normalize_tool_result

pytestmark = [pytest.mark.unit]

_PNG_B64 = base64.b64encode(b"fake-png-bytes").decode()
_SAVED = [{"index": 1, "path": "/home/user/workspace/projects/p1/outputs/tool_media/browser_open-abc123.png", "mime_type": "image/png"}]


def _image_output() -> dict:
    return {
        "content": [
            {"type": "text", "text": '{"status":"executed"}'},
            {"type": "image", "base64": _PNG_B64, "mime_type": "image/png"},
            {"type": "text", "text": f"{MEDIA_NOTE_PREFIX}\n/home/user/workspace/projects/p1/outputs/tool_media/browser_open-abc123.png"},
        ],
        "additional_kwargs": {"yuxi_saved_media": _SAVED},
        "status": "success",
    }


def test_saved_media_projects_to_preview_url():
    """已落盘图片投影为 artifact 预览地址，模型注记不进入公开内容。"""
    result = normalize_tool_result(_image_output(), artifact_url_factory=lambda path: f"/api/v1/agents/sessions/s1/artifacts{path}?preview=true")
    assert result.public_content == [
        {"type": "input_text", "text": '{"status":"executed"}'},
        {
            "type": "input_image",
            "image_url": f"/api/v1/agents/sessions/s1/artifacts{_SAVED[0]['path']}?preview=true",
        },
    ]
    assert _PNG_B64 not in str(result.public_content)
    assert MEDIA_NOTE_PREFIX not in str(result.public_content)


def test_without_factory_falls_back_to_data_url():
    """未提供地址构造器时退回内嵌 data URL，前端仍可渲染。"""
    result = normalize_tool_result(_image_output())
    assert result.public_content[1] == {"type": "input_image", "image_url": f"data:image/png;base64,{_PNG_B64}"}


def test_note_lookalike_business_text_is_not_filtered():
    """仅前缀相同但路径不一致的回显文本按业务文本保留，不因前缀被丢弃。"""
    lookalike = [{"type": "text", "text": f"{MEDIA_NOTE_PREFIX}\n/not/a/saved/path.png"}]
    result = normalize_tool_result(
        {"content": lookalike, "additional_kwargs": {"yuxi_saved_media": _SAVED}, "status": "success"},
        artifact_url_factory=lambda path: path,
    )
    assert result.public_content == [{"type": "input_text", "text": lookalike[0]["text"]}]


def test_index_mismatch_falls_back_to_data_url():
    """保存记录与块位置不对应时不猜测引用，退回 data URL。"""
    output = _image_output()
    output["additional_kwargs"]["yuxi_saved_media"] = [{"index": 0, "path": "/wrong/index.png", "mime_type": "image/png"}]
    result = normalize_tool_result(output, artifact_url_factory=lambda path: path)
    assert result.public_content[1]["image_url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "image", "base64": "", "mime_type": "image/png"}],
        [{"type": "image", "mime_type": "image/png"}],
        [{"type": "text", "text": "ok"}, {"type": "video", "url": "https://example.com/v.mp4"}],
        [{"type": "text", "text": "ok"}, "raw string part"],
    ],
)
def test_unprojectable_content_keeps_json_text_fallback(content):
    """空数据、未知块与非 dict 块维持整体 JSON 文本的既有行为。"""
    result = normalize_tool_result({"content": content, "status": "success"}, artifact_url_factory=lambda path: path)
    assert isinstance(result.public_content, str)
    assert result.public_content.startswith("[")


def test_text_only_and_input_protocol_lists_unchanged():
    """纯文本与已是 input 协议的列表维持原投影。"""
    text_list = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    assert normalize_tool_result({"content": text_list}).public_content == [
        {"type": "input_text", "text": "a"},
        {"type": "input_text", "text": "b"},
    ]
    input_list = [{"type": "input_text", "text": "a"}, {"type": "input_image", "image_url": "https://example.com/x.png"}]
    assert normalize_tool_result({"content": input_list}).public_content is input_list


def test_recorder_builds_thread_scoped_preview_url():
    """预览地址绑定当前线程并逐段编码，与前端 getThreadArtifactUrl 同构。"""
    recorder = RunMessageRecorder(run_id="r1", thread_id="sess-1", worker_id="w1")
    url = recorder._artifact_preview_url("/home/user/workspace/projects/p1/outputs/tool_media/browser_open-abc.png")
    assert url == "/api/v1/agents/sessions/sess-1/artifacts/home/user/workspace/projects/p1/outputs/tool_media/browser_open-abc.png?preview=true"
