from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from yuxi.storage.minio.client import get_minio_client

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def test_admin_ocr_config_drives_real_tmp_attachment_parse(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
    tmp_path: Path,
):
    """验证管理员配置能驱动真实临时附件 OCR，并回收测试创建的资源。"""

    configs_response = await e2e_client.get("/api/system/config", headers=e2e_headers)
    assert configs_response.status_code == 200, configs_response.text
    previous_default = configs_response.json()["default_ocr_engine"]
    image_path = tmp_path / "ocr-config-center.png"
    _build_ocr_image(image_path)
    thread_id = None
    uploaded = None
    parsed = None
    attachment = None

    try:
        update_response = await e2e_client.post(
            "/api/system/config/update",
            json={"default_ocr_engine": "rapid_ocr"},
            headers=e2e_headers,
        )
        assert update_response.status_code == 200, update_response.text

        options_response = await e2e_client.get("/api/system/ocr/options", headers=e2e_headers)
        assert options_response.status_code == 200, options_response.text
        assert options_response.json()["default_engine"] == "rapid_ocr"

        thread_response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": e2e_agent_context["agent_slug"],
                "title": f"ocr-config-e2e-{uuid.uuid4().hex[:8]}",
                "metadata": {},
            },
            headers=e2e_headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_payload = thread_response.json()
        thread_id = str(thread_payload.get("thread_id") or thread_payload["id"])

        with image_path.open("rb") as image_file:
            upload_response = await e2e_client.post(
                "/api/chat/attachments/tmp",
                files={"file": (image_path.name, image_file, "image/png")},
                headers=e2e_headers,
            )
        assert upload_response.status_code == 200, upload_response.text
        uploaded = upload_response.json()
        assert "rapid_ocr" in uploaded["parse_methods"]

        parse_response = await e2e_client.post(
            "/api/chat/attachments/tmp/parse",
            json={
                "object_name": uploaded["object_name"],
                "file_name": uploaded["file_name"],
                "bucket_name": uploaded["bucket_name"],
                "parse_method": None,
            },
            headers=e2e_headers,
        )
        assert parse_response.status_code == 200, parse_response.text
        parsed = parse_response.json()
        assert parsed["parse_method"] == "rapid_ocr"

        confirm_response = await e2e_client.post(
            f"/api/chat/thread/{thread_id}/attachments/confirm",
            json={
                "attachments": [
                    {
                        "file_name": uploaded["file_name"],
                        "file_type": uploaded["file_type"],
                        "bucket_name": uploaded["bucket_name"],
                        "object_name": uploaded["object_name"],
                        "parsed_object_name": parsed["parsed_object_name"],
                    }
                ]
            },
            headers=e2e_headers,
        )
        assert confirm_response.status_code == 200, confirm_response.text
        attachment = confirm_response.json()["attachments"][0]

        file_response = await e2e_client.get(
            "/api/viewer/filesystem/file",
            params={"thread_id": thread_id, "path": attachment["path"]},
            headers=e2e_headers,
        )
        assert file_response.status_code == 200, file_response.text
        recognized = file_response.text.upper()
        assert "OCR" in recognized
        assert "CONFIG" in recognized
        assert "CENTER" in recognized
    finally:
        try:
            restore_default = await e2e_client.post(
                "/api/system/config/update",
                json={"default_ocr_engine": previous_default},
                headers=e2e_headers,
            )
            assert restore_default.status_code == 200, restore_default.text
        finally:
            await _cleanup_created_resources(
                e2e_client=e2e_client,
                e2e_headers=e2e_headers,
                thread_id=thread_id,
                attachment=attachment,
                uploaded=uploaded,
                parsed=parsed,
            )


def _build_ocr_image(path: Path) -> None:
    """生成包含稳定英文文本的真实 OCR 测试图片。"""

    image = Image.new("RGB", (1400, 260), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 72)
    draw.text((60, 80), "OCR CONFIG CENTER E2E", fill="black", font=font)
    image.save(path)


async def _cleanup_created_resources(
    *,
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    thread_id: str | None,
    attachment: dict | None,
    uploaded: dict | None,
    parsed: dict | None,
) -> None:
    """删除 E2E 创建的正式附件、临时 MinIO 对象和对话线程。"""

    if thread_id and attachment:
        response = await e2e_client.delete(
            f"/api/chat/thread/{thread_id}/attachments/{attachment['file_id']}",
            headers=e2e_headers,
        )
        assert response.status_code == 200, response.text

    if uploaded:
        minio_client = get_minio_client()
        object_names = [uploaded["object_name"]]
        if parsed:
            object_names.append(parsed["parsed_object_name"])
        # 确认接口会复制 tmp 内容但不会删除源对象，测试必须显式回收两份临时数据。
        for object_name in object_names:
            assert await minio_client.adelete_file(uploaded["bucket_name"], object_name)

    if thread_id:
        response = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
        assert response.status_code == 200, response.text
