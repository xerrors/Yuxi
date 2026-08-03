"""
MinIO 存储客户端
简化的 MinIO 对象存储操作
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlparse

from urllib3 import BaseHTTPResponse

from minio import Minio
from minio.error import S3Error
from src.utils import logger


class StorageError(Exception):
    """存储相关异常基类"""

    pass


class StorageUploadError(StorageError):
    """存储相关异常基类"""


class UploadResult:
    """简化的上传结果"""

    def __init__(self, url: str, bucket_name: str, object_name: str):
        self.url = url
        self.bucket_name = bucket_name
        self.object_name = object_name


class MinIOClient:
    """
    简化的 MinIO 客户端类
    """

    PUBLIC_READ_BUCKETS = {"generated-images", "avatar", "kb-images"}

    # 知识库相关的 bucket 名称
    KB_BUCKETS = {
        "documents": "kb-documents",
        "parsed": "kb-parsed",
        "images": "kb-images",
    }

    @staticmethod
    def get_ref_bucket_name(db_id: str) -> str:
        """获取 ref bucket 名称（截断32位）"""
        return f"ref-{db_id[:32].replace('_', '-')}"

    @staticmethod
    def get_ref_bucket_name_full(db_id: str) -> str:
        """获取 ref bucket 名称（不截断）"""
        return f"ref-{db_id.replace('_', '-')}"

    def __init__(self):
        """初始化 MinIO 客户端"""
        self.endpoint = os.getenv("MINIO_URI") or "http://milvus-minio:9000"
        self.access_key = os.getenv("MINIO_ACCESS_KEY") or "minioadmin"
        self.secret_key = os.getenv("MINIO_SECRET_KEY") or "minioadmin"
        self._client = None

        # Public URLs may be served through an application reverse proxy rather
        # than by exposing MinIO's port directly.
        public_uri = (os.getenv("MINIO_PUBLIC_URI") or "").strip().rstrip("/")
        if public_uri:
            parsed_public_uri = urlparse(public_uri)
            if parsed_public_uri.scheme not in {"http", "https"} or not parsed_public_uri.hostname:
                raise StorageError("MINIO_PUBLIC_URI 必须是有效的 http/https URL")
            self.public_base_url = public_uri
        elif os.getenv("RUNNING_IN_DOCKER"):
            host_ip = (os.getenv("HOST_IP") or "localhost").strip()
            if "://" in host_ip:
                host_ip = host_ip.split("://", 1)[-1]
            self.public_base_url = f"http://{host_ip.rstrip('/')}:9000"
        else:
            self.public_base_url = "http://localhost:9000"
        logger.debug(f"MinIOClient public_base_url: {self.public_base_url}")

    @property
    def client(self) -> Minio:
        """获取 MinIO 客户端实例"""
        if self._client is None:
            endpoint = self.endpoint
            if "://" in endpoint:
                endpoint = endpoint.split("://")[-1]

            self._client = Minio(
                endpoint=endpoint, access_key=self.access_key, secret_key=self.secret_key, secure=False
            )
        return self._client

    def ensure_bucket_exists(self, bucket_name: str) -> bool:
        """确保存储桶存在"""
        try:
            created = False
            if not self.client.bucket_exists(bucket_name=bucket_name):
                self.client.make_bucket(bucket_name=bucket_name)
                created = True
                logger.info(f"存储桶 '{bucket_name}' 已创建")

            self._ensure_public_read_access(bucket_name)

            if created and bucket_name in self.PUBLIC_READ_BUCKETS:
                logger.info(f"存储桶 '{bucket_name}' 已配置为公开可读")

            return True
        except S3Error as e:
            logger.error(f"存储桶 '{bucket_name}' 错误: {e}")
            raise StorageError(f"Error with bucket '{bucket_name}': {e}")
        except StorageError:
            raise

    def upload_file(
        self, bucket_name: str, object_name: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> UploadResult:
        """上传文件到 MinIO"""
        try:
            self.ensure_bucket_exists(bucket_name=bucket_name)

            data_stream = BytesIO(data)
            result = self.client.put_object(
                bucket_name=bucket_name,
                object_name=object_name,
                data=data_stream,
                length=len(data),
                content_type=content_type,
            )

            assert result is not None
            url = f"{self.public_base_url}/{bucket_name}/{object_name}"

            return UploadResult(url, bucket_name, object_name)

        except S3Error as e:
            error_msg = f"上传文件 '{object_name}' 失败: {e}"
            logger.error(error_msg)
            raise StorageError(error_msg)

    async def aupload_file(
        self, bucket_name: str, object_name: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> UploadResult:
        result = await asyncio.to_thread(
            self.upload_file, bucket_name=bucket_name, object_name=object_name, data=data, content_type=content_type
        )
        return result

    def upload_file_from_path(self, bucket_name: str, object_name: str, file_path: str) -> UploadResult:
        """从文件路径上传文件"""
        try:
            with open(file_path, "rb") as file_data:
                data = file_data.read()

            # 猜测内容类型
            content_type = self._guess_content_type(object_name)

            return self.upload_file(bucket_name, object_name, data, content_type)

        except FileNotFoundError:
            raise StorageError(f"文件 '{file_path}' 不存在")
        except Exception as e:
            raise StorageError(f"从路径上传文件失败: {e}")

    def _guess_content_type(self, object_name: str) -> str:
        """根据文件名猜测 MIME 类型"""
        ext = object_name.split(".")[-1].lower()
        content_types = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "pdf": "application/pdf",
            "txt": "text/plain",
            "json": "application/json",
            "html": "text/html",
            "css": "text/css",
            "js": "application/javascript",
        }
        return content_types.get(ext, "application/octet-stream")

    def download_file(self, bucket_name: str, object_name: str) -> bytes:
        """下载文件"""
        try:
            response = self.client.get_object(bucket_name=bucket_name, object_name=object_name)
            data = response.read()
            response.close()
            logger.info(f"成功下载 '{object_name}' 从存储桶 '{bucket_name}'")
            return data

        except S3Error as e:
            if "NoSuchKey" in str(e):
                raise StorageError(f"对象 '{object_name}' 在存储桶 '{bucket_name}' 中不存在")
            raise StorageError(f"下载文件失败: {e}")

    async def adownload_response(self, bucket_name: str, object_name: str) -> BaseHTTPResponse:
        """异步下载文件"""
        try:
            response = await asyncio.to_thread(
                self.client.get_object,
                bucket_name=bucket_name,
                object_name=object_name,
            )
            return response

        except S3Error as e:
            if "NoSuchKey" in str(e):
                raise StorageError(f"对象 '{object_name}' 在存储桶 '{bucket_name}' 中不存在")
            raise StorageError(f"下载文件失败: {e}")

    async def adownload_file(self, bucket_name: str, object_name: str) -> bytes:
        """异步下载文件"""
        try:
            response = await asyncio.to_thread(self.client.get_object, bucket_name=bucket_name, object_name=object_name)
            data = await asyncio.to_thread(response.read)
            response.close()
            logger.info(f"成功下载 '{object_name}' 从存储桶 '{bucket_name}'")
            return data

        except S3Error as e:
            if "NoSuchKey" in str(e):
                raise StorageError(f"对象 '{object_name}' 在存储桶 '{bucket_name}' 中不存在")
            raise StorageError(f"下载文件失败: {e}")

    def get_presigned_url(self, bucket_name: str, object_name: str, days=7) -> str:
        """将minio放在内网访问，外部通过返回代理链接访问"""
        res_url = self.client.get_presigned_url(
            method="GET", bucket_name=bucket_name, object_name=object_name, expires=timedelta(days=days)
        )
        return res_url

    def delete_file(self, bucket_name: str, object_name: str) -> bool:
        """删除文件"""
        try:
            self.client.remove_object(bucket_name=bucket_name, object_name=object_name)
            logger.info(f"成功删除 '{object_name}' 从存储桶 '{bucket_name}'")
            return True

        except S3Error as e:
            if "NoSuchKey" in str(e):
                logger.warning(f"要删除的对象 '{object_name}' 不存在")
                return False
            raise StorageError(f"删除文件失败: {e}")

    async def adelete_file(self, bucket_name: str, object_name: str) -> bool:
        """删除文件"""
        result = await asyncio.to_thread(
            self.delete_file,
            bucket_name=bucket_name,
            object_name=object_name,
        )
        return result

    async def adelete_objects_by_prefix(self, bucket_name: str, prefix: str) -> int:
        """
        按前缀删除对象

        Args:
            bucket_name: bucket 名称
            prefix: 对象前缀

        Returns:
            删除的对象数量
        """
        deleted_count = 0

        def _delete_objects():
            nonlocal deleted_count
            try:
                objects = self.client.list_objects(bucket_name, prefix=prefix, recursive=True)
                for obj in objects:
                    try:
                        self.client.remove_object(bucket_name, obj.object_name)
                        deleted_count += 1
                    except S3Error as e:
                        logger.warning(f"Failed to delete {bucket_name}/{obj.object_name}: {e}")
            except S3Error as e:
                logger.warning(f"Failed to list objects in {bucket_name}/{prefix}: {e}")

        await asyncio.to_thread(_delete_objects)
        return deleted_count

    async def adelete_bucket(self, bucket_name: str) -> bool:
        """
        删除 bucket（先删除所有对象，再删除 bucket）

        Args:
            bucket_name: bucket 名称

        Returns:
            是否成功
        """
        try:
            # 先删除所有对象
            await self.adelete_objects_by_prefix(bucket_name, "")
            # 再删除 bucket
            await asyncio.to_thread(self.client.remove_bucket, bucket_name)
            logger.info(f"成功删除 bucket: {bucket_name}")
            return True
        except S3Error as e:
            if "NoSuchBucket" in str(e):
                logger.warning(f"bucket 不存在: {bucket_name}")
                return False
            raise StorageError(f"删除 bucket 失败: {e}")

    def file_exists(self, bucket_name: str, object_name: str) -> bool:
        """检查文件是否存在"""
        try:
            self.client.stat_object(bucket_name=bucket_name, object_name=object_name)
            return True
        except S3Error as e:
            if "NoSuchKey" in str(e):
                return False
            raise StorageError(f"检查文件存在性失败: {e}")

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
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{bucket_name}"],
                },
            ],
        }

        try:
            self.client.set_bucket_policy(bucket_name=bucket_name, policy=json.dumps(policy))
        except S3Error as e:
            logger.warning(f"设置存储桶 '{bucket_name}' 公共读取策略失败: {e}")
            raise StorageError(f"无法设置存储桶公共访问策略: {e}")

    @asynccontextmanager
    async def temp_file_from_url(
        self,
        url: str,
        allowed_extensions: list[str] | None = None,
    ):
        """
        异步上下文管理器：从 MinIO URL 下载文件到临时文件，使用后自动清理

        Args:
            url: MinIO 文件 URL
            allowed_extensions: 允许的文件扩展名列表（可选）

        Yields:
            str: 临时文件路径

        Raises:
            StorageError: 如果 URL 无效或下载失败
        """
        import tempfile

        # 验证 URL
        if not url or not isinstance(url, str):
            raise StorageError("URL 不能为空")

        url = url.strip()

        if not url.startswith(("http://", "https://")):
            raise StorageError("无效的 MinIO URL，只允许 http/https")

        parsed = urlparse(url)

        # URL must belong to the private MinIO endpoint or the explicitly
        # configured public reverse-proxy endpoint.
        endpoint_host = self.endpoint.split("://")[-1].split(":")[0]
        public_uri = urlparse(self.public_base_url)
        allowed_hosts = {endpoint_host, public_uri.hostname or ""}
        if parsed.hostname not in allowed_hosts:
            raise StorageError(f"不允许的外部 URL: {parsed.hostname}")

        # 检查路径遍历
        if ".." in url or "\\" in url:
            raise StorageError("URL 包含路径遍历字符")

        # 验证扩展名
        if allowed_extensions and not any(url.endswith(ext) for ext in allowed_extensions):
            raise StorageError(f"文件扩展名不符合要求，允许: {', '.join(allowed_extensions)}")

        # Parse bucket and object name, removing the configured public prefix.
        object_path = parsed.path
        public_prefix = public_uri.path.rstrip("/")
        if parsed.hostname == public_uri.hostname and public_prefix:
            if object_path != public_prefix and not object_path.startswith(f"{public_prefix}/"):
                raise StorageError("URL 不属于配置的 MinIO 公开路径")
            object_path = object_path[len(public_prefix) :]
        path_parts = object_path.lstrip("/").split("/", 1)
        if len(path_parts) != 2:
            raise StorageError("无法解析 MinIO URL")

        bucket_name, object_name = path_parts

        # 下载文件
        file_data = await self.adownload_file(bucket_name, object_name)
        logger.info(f"成功从 MinIO 下载文件: {object_name} ({len(file_data)} bytes)")

        # 创建临时文件
        if allowed_extensions:
            suffix = next((ext for ext in allowed_extensions if url.endswith(ext)), ".tmp")
        else:
            suffix = f".{object_name.split('.')[-1]}"

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as temp_file:
                temp_file.write(file_data)
                temp_path = temp_file.name

            logger.info(f"文件已下载到临时路径: {temp_path}")
            yield temp_path

        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                    logger.info(f"已删除临时文件: {temp_path}")
                except Exception as e:
                    logger.warning(f"删除临时文件失败: {e}")


# 全局客户端实例
_default_client = None


def get_minio_client() -> MinIOClient:
    """获取 MinIO 客户端实例"""
    global _default_client
    if _default_client is None:
        _default_client = MinIOClient()
    return _default_client


async def aupload_file_to_minio(bucket_name: str, file_name: str, data: bytes, file_extension: str) -> str:
    """
    通过字节上传文件到 MinIO的异步接口，根据输入的file_extension确定文件格式，并返回资源url

    Args:
        bucket_name: bucket_name
        file_name : filename
        data: 文件字节流
        file_extension: 输入的拓展名
    Returns:
        str: 文件访问 URL
    """
    client = get_minio_client()
    # 根据扩展名猜测 content_type
    content_type = client._guess_content_type(file_extension)
    # 上传文件
    upload_result = await client.aupload_file(bucket_name, file_name, data, content_type)
    return upload_result.url
