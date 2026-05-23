import uuid
from pathlib import Path

import aiofiles
from fastapi import UploadFile

from yuxi.storage.minio import aupload_file_to_minio


async def write_upload_to_buffer(
    upload: UploadFile,
    buffer,
    *,
    max_size_bytes: int,
    too_large_message: str,
    chunk_size: int = 1024 * 1024,
) -> int:
    await upload.seek(0)
    written = 0

    while chunk := await upload.read(chunk_size):
        written += len(chunk)
        if written > max_size_bytes:
            raise ValueError(too_large_message)
        await buffer.write(chunk)

    return written


async def read_upload_with_limit(
    upload: UploadFile,
    *,
    max_size_bytes: int,
    too_large_message: str,
    chunk_size: int = 1024 * 1024,
) -> bytes:
    await upload.seek(0)
    written = 0
    chunks: list[bytes] = []

    while chunk := await upload.read(chunk_size):
        written += len(chunk)
        if written > max_size_bytes:
            raise ValueError(too_large_message)
        chunks.append(chunk)

    return b"".join(chunks)


async def write_upload_to_path(
    upload: UploadFile,
    dest: Path,
    *,
    max_size_bytes: int,
    too_large_message: str,
    mode: str = "wb",
    chunk_size: int = 1024 * 1024,
) -> int:
    async with aiofiles.open(dest, mode) as buffer:
        return await write_upload_to_buffer(
            upload,
            buffer,
            max_size_bytes=max_size_bytes,
            too_large_message=too_large_message,
            chunk_size=chunk_size,
        )


async def upload_image_to_minio(
    upload: UploadFile,
    *,
    object_prefix: str,
    max_size_bytes: int,
    too_large_message: str,
) -> str:
    if not upload.content_type or not upload.content_type.startswith("image/"):
        raise ValueError("只能上传图片文件")

    file_content = await read_upload_with_limit(
        upload,
        max_size_bytes=max_size_bytes,
        too_large_message=too_large_message,
    )
    file_extension = upload.filename.rsplit(".", 1)[-1].lower() if upload.filename and "." in upload.filename else "jpg"
    object_name = f"{object_prefix.strip('/')}/{uuid.uuid4()}.{file_extension}"
    return await aupload_file_to_minio("public", object_name, file_content)
