"""沙盒存储 - MinIO 附件复制

将用户上传的附件从 MinIO 同步到沙盒的 uploads/{thread_id}/ 目录。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from yuxi.agents.backends.sandbox_config import UPLOADS_DIR
from yuxi.storage.minio import get_minio_client
from yuxi.utils.logging_config import logger

if TYPE_CHECKING:
    from yuxi.agents.backends.sandbox_backend import YuxiSandboxBackend


# MinIO bucket for attachments
ATTACHMENTS_BUCKET = "attachments"


async def copy_thread_attachments(
    user_id: str,
    thread_id: str,
    sandbox_backend: YuxiSandboxBackend,
) -> bool:
    """将线程附件从 MinIO 复制到沙盒

    Args:
        user_id: 用户 ID
        thread_id: 线程 ID
        sandbox_backend: 沙盒后端

    Returns:
        是否成功
    """
    try:
        minio_client = get_minio_client()

        # 获取该线程在 MinIO 中的附件前缀
        prefix = f"attachments/{user_id}/{thread_id}/"

        # 列出所有附件
        attachments = []
        try:
            objects = minio_client.client.list_objects(ATTACHMENTS_BUCKET, prefix=prefix, recursive=True)
            for obj in objects:
                attachments.append(obj.object_name)
        except Exception as e:
            logger.warning(f"Failed to list MinIO attachments for thread {thread_id}: {e}")
            return False

        if not attachments:
            logger.info(f"No attachments found in MinIO for thread {thread_id}")
            return True

        # 并行复制附件到沙盒
        async def copy_single_attachment(object_name: str):
            try:
                # 从 MinIO 下载
                data = minio_client.download_file(ATTACHMENTS_BUCKET, object_name)

                # 计算目标路径
                relative_path = object_name[len(prefix) :]
                dest_path = f"/uploads/{relative_path}"

                # 上传到沙盒
                sandbox_backend.upload_files([(dest_path, data)])
                logger.debug(f"Copied attachment {object_name} to {dest_path}")
            except Exception as e:
                logger.warning(f"Failed to copy attachment {object_name}: {e}")

        await asyncio.gather(*[copy_single_attachment(obj) for obj in attachments])
        logger.info(f"Copied {len(attachments)} attachments to sandbox for thread {thread_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to copy thread attachments: {e}")
        return False


async def copy_attachment_to_sandbox(
    user_id: str,
    thread_id: str,
    file_name: str,
    file_content: bytes,
    sandbox_backend: YuxiSandboxBackend,
) -> bool:
    """将单个附件复制到沙盒

    Args:
        user_id: 用户 ID
        thread_id: 线程 ID
        file_name: 文件名
        file_content: 文件内容
        sandbox_backend: 沙盒后端

    Returns:
        是否成功
    """
    try:
        dest_path = f"/uploads/{file_name}"
        result = sandbox_backend.upload_files([(dest_path, file_content)])
        return result[0].error is None
    except Exception as e:
        logger.error(f"Failed to copy attachment to sandbox: {e}")
        return False


def get_sandbox_uploads_dir(user_id: str, thread_id: str, saves_dir: str) -> Path:
    """获取沙盒 uploads 目录的宿主机路径

    Args:
        user_id: 用户 ID
        thread_id: 线程 ID
        saves_dir: 保存根目录

    Returns:
        宿主机侧 uploads 目录路径
    """
    return Path(saves_dir) / "threads" / thread_id / "user-data" / UPLOADS_DIR
