"""MinIO 对象存储客户端与公开 URL 规范化。"""

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from typing import Any
from urllib.parse import quote, urlsplit

from urllib3 import BaseHTTPResponse

from minio import Minio
from minio.commonconfig import ENABLED, Filter, Tag, Tags
from minio.error import S3Error
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule
from yuxi.infrastructure.observability.logging import logger
from yuxi.shared.files import detect_media_type

_DEFAULT_PUBLIC_PORT = 9000
_DEFAULT_PUBLIC_ENDPOINT = f"localhost:{_DEFAULT_PUBLIC_PORT}"


class StorageError(Exception):
    """存储相关异常基类。"""


class ObjectSizeLimitError(StorageError):
    """对象字节数超过调用方指定的读取上限。"""


@dataclass(frozen=True)
class UploadResult:
    """一次对象上传的访问地址与对象身份。"""

    url: str
    bucket_name: str
    object_name: str


def normalize_public_minio_url(value: str | None) -> str | None:
    """把历史直连 MinIO 的公开 URL 改写为同源代理路径。"""
    if not value or value.startswith("/minio/public/"):
        return value
    try:
        parsed = urlsplit(value)
        if parsed.port != _DEFAULT_PUBLIC_PORT or not parsed.path.startswith("/public/"):
            return value
    except ValueError:
        return value
    normalized = f"{_public_base_url()}{parsed.path}"
    if parsed.query:
        normalized = f"{normalized}?{parsed.query}"
    if parsed.fragment:
        normalized = f"{normalized}#{parsed.fragment}"
    return normalized


class MinIOClient:
    """按部署环境创建 MinIO 客户端并封装对象读写。"""

    PUBLIC_READ_BUCKETS = {"public"}

    # 知识库相关的 bucket 名称；images 使用私有 bucket，图片统一经后端鉴权代理访问
    KB_BUCKETS = {
        "documents": "knowledgebases",
        "parsed": "knowledgebases",
        "images": "kb-images",
    }

    def __init__(self):
        """初始化 MinIO 客户端配置，连接在首次使用时建立。"""
        self.endpoint: str = os.getenv("MINIO_URI") or "http://minio:9000"
        self.access_key: str = os.getenv("MINIO_ACCESS_KEY") or "minioadmin"
        self.secret_key: str = os.getenv("MINIO_SECRET_KEY") or "minioadmin"
        self.public_base_url: str = _public_base_url()
        self._client: Minio | None = None

        # 设置公开访问端点
        if os.getenv("RUNNING_IN_DOCKER"):
            host_ip = (os.getenv("HOST_IP") or "").strip()
            if not host_ip:
                host_ip = "localhost"
            host_ip = _strip_scheme(host_ip).rstrip("/")
            self.public_endpoint = f"{host_ip}:{_DEFAULT_PUBLIC_PORT}"
            logger.debug(f"Docker MinIOClient public_endpoint: {self.public_endpoint}")
        else:
            self.public_endpoint = _DEFAULT_PUBLIC_ENDPOINT
            logger.debug(f"Default_client: {self.public_endpoint}")

    @property
    def client(self) -> Minio:
        """获取 MinIO 客户端实例"""
        if self._client is None:
            self._client = Minio(
                endpoint=_strip_scheme(self.endpoint),
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=False,
            )
        return self._client

    def ensure_bucket_exists(self, bucket_name: str) -> bool:
        """确保存储桶存在，并为公开桶配置只读策略。"""
        try:
            created = False
            if not self.client.bucket_exists(bucket_name=bucket_name):
                try:
                    self.client.make_bucket(bucket_name=bucket_name)
                except S3Error as e:
                    # 其他上传可能已创建同一桶；只有明确归当前账号所有才可继续。
                    if e.code != "BucketAlreadyOwnedByYou":
                        raise
                else:
                    created = True
                    logger.info(f"存储桶 '{bucket_name}' 已创建")

            self._ensure_public_read_access(bucket_name)

            if created and bucket_name in self.PUBLIC_READ_BUCKETS:
                logger.info(f"存储桶 '{bucket_name}' 已配置为公开可读")

            return True
        except S3Error as e:
            logger.error(f"存储桶 '{bucket_name}' 错误: {e}")
            raise StorageError(f"Error with bucket '{bucket_name}': {e}") from e

    def upload_file(
        self,
        bucket_name: str,
        object_name: str,
        data: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
        tags: dict[str, str] | None = None,
    ) -> UploadResult:
        """上传字节到指定对象并返回访问地址。"""
        try:
            self.ensure_bucket_exists(bucket_name=bucket_name)

            resolved_content_type = content_type or detect_media_type(object_name)
            data_stream = BytesIO(data)
            object_tags = Tags.new_object_tags()
            object_tags.update(tags or {})
            self.client.put_object(
                bucket_name=bucket_name,
                object_name=object_name,
                data=data_stream,
                length=len(data),
                content_type=resolved_content_type,
                metadata=metadata,
                tags=object_tags if tags else None,
            )

            if bucket_name in self.PUBLIC_READ_BUCKETS:
                url = f"{self.public_base_url}/{bucket_name}/{quote(object_name, safe='/')}"
            else:
                url = f"http://{self.public_endpoint}/{bucket_name}/{quote(object_name, safe='/')}"

            return UploadResult(url, bucket_name, object_name)

        except S3Error as e:
            error_msg = f"上传文件 '{object_name}' 失败: {e}"
            logger.error(error_msg)
            raise StorageError(error_msg) from e

    async def aupload_file(
        self,
        bucket_name: str,
        object_name: str,
        data: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
        tags: dict[str, str] | None = None,
    ) -> UploadResult:
        """在线程内上传字节及可选对象元数据。"""
        return await asyncio.to_thread(
            self.upload_file,
            bucket_name=bucket_name,
            object_name=object_name,
            data=data,
            content_type=content_type,
            metadata=metadata,
            tags=tags,
        )

    async def ensure_tag_expiration(self, bucket_name: str, *, rule_id: str, tag: str, days: int) -> None:
        """按对象标签配置过期规则，只替换调用方拥有的规则并保留其他策略。"""

        def configure() -> None:
            """读取并合并 S3 生命周期，配置失败时阻止暂存写入。"""
            self.ensure_bucket_exists(bucket_name)
            rule = Rule(ENABLED, rule_filter=Filter(tag=Tag(tag, "true")), rule_id=rule_id, expiration=Expiration(days=days))
            config = self.client.get_bucket_lifecycle(bucket_name)
            rules = list(config.rules) if config is not None else []
            if rule in rules:
                return
            rules = [existing for existing in rules if existing.rule_id != rule_id]
            self.client.set_bucket_lifecycle(bucket_name, LifecycleConfig([*rules, rule]))

        await asyncio.to_thread(configure)

    async def object_metadata(self, bucket_name: str, object_name: str) -> dict[str, str]:
        """回读对象来源元数据，不从物理键猜测逻辑文件名。"""
        stat = await asyncio.to_thread(self.client.stat_object, bucket_name, object_name)
        return {key.lower(): value for key, value in stat.metadata.items()}

    def download_file(self, bucket_name: str, object_name: str, *, max_bytes: int | None = None) -> bytes:
        """读取对象；指定上限时最多读取上限加一字节以判断超限。"""
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("object read limit must be non-negative")
        response = None
        try:
            response = self.client.get_object(bucket_name=bucket_name, object_name=object_name)
            data = response.read() if max_bytes is None else response.read(max_bytes + 1)
            if max_bytes is not None and len(data) > max_bytes:
                raise ObjectSizeLimitError("object exceeds read limit")
            logger.info(f"成功下载 '{object_name}' 从存储桶 '{bucket_name}'")
            return data

        except S3Error as e:
            if e.code == "NoSuchKey":
                raise StorageError(f"对象 '{object_name}' 在存储桶 '{bucket_name}' 中不存在") from e
            raise StorageError(f"下载文件失败: {e}") from e
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    async def adownload_response(self, bucket_name: str, object_name: str) -> BaseHTTPResponse:
        """在线程内打开对象响应，调用方负责读取并归还连接。"""
        try:
            return await asyncio.to_thread(
                self.client.get_object,
                bucket_name=bucket_name,
                object_name=object_name,
            )

        except S3Error as e:
            if e.code == "NoSuchKey":
                raise StorageError(f"对象 '{object_name}' 在存储桶 '{bucket_name}' 中不存在") from e
            raise StorageError(f"下载文件失败: {e}") from e

    async def adownload_file(self, bucket_name: str, object_name: str, *, max_bytes: int | None = None) -> bytes:
        """在线程内读取并关闭对象响应，支持调用方指定字节上限。"""
        return await asyncio.to_thread(self.download_file, bucket_name, object_name, max_bytes=max_bytes)

    def get_presigned_url(self, bucket_name: str, object_name: str, days: int = 7) -> str:
        """生成短期预签名 GET 地址，供内网调用方直接访问。"""
        return self.client.get_presigned_url(
            method="GET",
            bucket_name=bucket_name,
            object_name=object_name,
            expires=timedelta(days=days),
        )

    def delete_file(self, bucket_name: str, object_name: str) -> bool:
        """删除对象；对象不存在时返回 False。"""
        try:
            self.client.remove_object(bucket_name=bucket_name, object_name=object_name)
            logger.info(f"成功删除 '{object_name}' 从存储桶 '{bucket_name}'")
            return True

        except S3Error as e:
            if e.code == "NoSuchKey":
                logger.warning(f"要删除的对象 '{object_name}' 不存在")
                return False
            raise StorageError(f"删除文件失败: {e}") from e

    async def adelete_file(self, bucket_name: str, object_name: str) -> bool:
        """在线程内删除对象。"""
        return await asyncio.to_thread(
            self.delete_file,
            bucket_name=bucket_name,
            object_name=object_name,
        )

    async def adelete_objects_by_prefix(self, bucket_name: str, prefix: str) -> int:
        """删除指定前缀下的全部对象并返回数量，缺失桶视为已清理。"""
        deleted_count = 0

        def _delete_objects():
            nonlocal deleted_count
            try:
                objects = self.client.list_objects(bucket_name, prefix=prefix, recursive=True)
                for obj in objects:
                    self.client.remove_object(bucket_name, obj.object_name)
                    deleted_count += 1
            except S3Error as e:
                if e.code == "NoSuchBucket":
                    logger.warning(f"待清理的存储桶 '{bucket_name}' 不存在")
                    return
                raise StorageError(f"删除对象前缀失败: {bucket_name}/{prefix}: {e}") from e

        await asyncio.to_thread(_delete_objects)
        return deleted_count

    async def alist_object_metadata(self, bucket_name: str, prefix: str) -> list[dict[str, Any]]:
        """在线程池中列出过期清理所需的对象元数据。"""

        def list_metadata() -> list[dict[str, Any]]:
            try:
                return [
                    {
                        "object_name": str(item.object_name),
                        "last_modified": item.last_modified,
                    }
                    for item in self.client.list_objects(bucket_name, prefix=prefix, recursive=True)
                ]
            except S3Error as exc:
                raise StorageError(f"列出对象前缀失败: {bucket_name}/{prefix}: {exc}") from exc

        return await asyncio.to_thread(list_metadata)

    def stat_file(self, bucket_name: str, object_name: str) -> int | None:
        """返回对象字节数，对象不存在时返回 None。"""
        try:
            stat = self.client.stat_object(bucket_name=bucket_name, object_name=object_name)
            return stat.size
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            raise StorageError(f"获取文件信息失败: {e}") from e

    async def astat_file(self, bucket_name: str, object_name: str) -> int | None:
        """在线程内读取对象字节数。"""
        return await asyncio.to_thread(self.stat_file, bucket_name, object_name)

    def _ensure_public_read_access(self, bucket_name: str) -> None:
        """设置存储桶策略，允许公开读取对象"""
        if bucket_name not in self.PUBLIC_READ_BUCKETS:
            return

        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
                },
            ],
        }

        try:
            self.client.set_bucket_policy(bucket_name=bucket_name, policy=json.dumps(policy))
        except S3Error as e:
            logger.warning(f"设置存储桶 '{bucket_name}' 公共读取策略失败: {e}")
            raise StorageError(f"无法设置存储桶公共访问策略: {e}") from e


def _public_base_url() -> str:
    """返回公开对象访问的同源代理前缀。"""
    return (os.getenv("MINIO_PUBLIC_URL") or "/minio").rstrip("/")


def _strip_scheme(endpoint: str) -> str:
    """移除端点中的 URL scheme，MinIO SDK 只接受 host:port。"""
    return endpoint.split("://", 1)[-1]


# 全局客户端实例
_default_client = None


def get_minio_client() -> MinIOClient:
    """获取进程级 MinIO 客户端实例。"""
    global _default_client
    if _default_client is None:
        _default_client = MinIOClient()
    return _default_client


async def aupload_file_to_minio(bucket_name: str, file_name: str, data: bytes) -> str:
    """异步上传字节并返回对象访问 URL，MIME 类型按对象名推断。"""
    client = get_minio_client()
    upload_result = await client.aupload_file(bucket_name, file_name, data)
    return upload_result.url
