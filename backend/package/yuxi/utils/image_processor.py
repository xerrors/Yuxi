"""
图片处理工具模块
支持图片的格式转换、压缩、缩略图生成等功能
"""

import base64
import io

from PIL import ExifTags, Image

from yuxi.utils import logger


class ImageProcessor:
    """图片处理类"""

    # 支持的图片格式
    SUPPORTED_FORMATS = {"JPEG", "PNG", "WebP", "GIF", "BMP"}

    # 最大文件大小（5MB）
    MAX_FILE_SIZE = 5 * 1024 * 1024

    # 缩略图尺寸
    THUMBNAIL_SIZE = (200, 200)

    def process_image(self, image_data: bytes, original_filename: str = "") -> dict:
        """
        处理上传的图片

        Args:
            image_data: 图片二进制数据
            original_filename: 原始文件名

        Returns:
            dict: 包含处理结果的字典
        """
        try:
            # 验证图片格式
            img_format, _ = self._validate_image_format(image_data)
            if img_format not in self.SUPPORTED_FORMATS:
                raise ValueError(f"不支持的图片格式: {img_format}")

            # 加载图片
            with Image.open(io.BytesIO(image_data)) as img:
                # 处理EXIF方向信息
                img = self._fix_image_orientation(img)

                # 生成缩略图
                thumbnail_data = self._generate_thumbnail(img)

                # 压缩主图片（如果需要）
                processed_data, final_format = self._compress_image(img, img_format)

                # 转换为 base64
                base64_data = base64.b64encode(processed_data).decode("utf-8")
                base64_thumbnail = base64.b64encode(thumbnail_data).decode("utf-8")

                # 获取图片信息
                width, height = img.size
                mime_type = f"image/{final_format.lower()}"

                return {
                    "success": True,
                    "image_content": base64_data,
                    "thumbnail_content": base64_thumbnail,
                    "width": width,
                    "height": height,
                    "format": final_format,
                    "mime_type": mime_type,
                    "size_bytes": len(processed_data),
                    "original_filename": original_filename,
                }

        except Exception as e:
            logger.error(f"图片处理失败: {str(e)}")
            return {"success": False, "error": str(e)}

    def _validate_image_format(self, image_data: bytes) -> tuple[str, str]:
        """验证图片格式并返回格式信息"""
        try:
            with Image.open(io.BytesIO(image_data)) as img:
                return img.format, img.mode
        except Exception as e:
            raise ValueError(f"无效的图片格式: {str(e)}")

    def _fix_image_orientation(self, img: Image.Image) -> Image.Image:
        """根据EXIF信息修正图片方向"""
        try:
            if hasattr(img, "_getexif"):
                exif = img._getexif()
                if exif is not None:
                    for tag, value in exif.items():
                        if tag in ExifTags.TAGS and ExifTags.TAGS[tag] == "Orientation":
                            if value == 3:
                                img = img.rotate(180, expand=True)
                            elif value == 6:
                                img = img.rotate(270, expand=True)
                            elif value == 8:
                                img = img.rotate(90, expand=True)
                            break
        except Exception as e:
            logger.warning(f"修正图片方向失败，使用原始方向: {str(e)}")

        return img

    def _generate_thumbnail(self, img: Image.Image) -> bytes:
        """生成缩略图"""
        try:
            thumbnail = self._convert_to_rgb_for_export(img)

            # 生成缩略图，保持宽高比
            thumbnail.thumbnail(self.THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

            # 转换为JPEG格式
            with io.BytesIO() as output:
                thumbnail.save(output, format="JPEG", quality=85, optimize=True)
                return output.getvalue()

        except Exception as e:
            logger.error(f"生成缩略图失败: {str(e)}")
            # 如果缩略图生成失败，返回一个1x1的透明图片
            with io.BytesIO() as output:
                empty_img = Image.new("RGB", (1, 1), color="white")
                empty_img.save(output, format="JPEG", quality=85)
                return output.getvalue()

    def _convert_to_rgb_for_export(self, img: Image.Image) -> Image.Image:
        """转换为 RGB，同时把透明像素按白底合成，避免隐藏颜色变成可见像素。"""
        if img.mode == "RGB":
            return img.copy()

        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if not has_alpha:
            return img.convert("RGB")

        rgba_img = img.convert("RGBA")
        background = Image.new("RGBA", rgba_img.size, (255, 255, 255, 255))
        background.alpha_composite(rgba_img)
        return background.convert("RGB")

    def _compress_image(self, img: Image.Image, original_format: str) -> tuple[bytes, str]:
        """
        压缩图片，如果超过大小限制

        Args:
            img: PIL Image对象
            original_format: 原始格式

        Returns:
            Tuple[bytes, str]: (压缩后的图片数据, 最终格式)
        """
        processed_img = self._convert_to_rgb_for_export(img)

        # 尝试保持原始格式，但优先使用JPEG（更好的压缩）
        target_format = "JPEG" if original_format != "PNG" else "PNG"

        # 初始质量设置
        quality = 85

        with io.BytesIO() as output:
            # 第一次保存以检查大小
            processed_img.save(output, format=target_format, quality=quality, optimize=True)
            compressed_data = output.getvalue()

            # 如果文件大小合适，直接返回
            if len(compressed_data) <= self.MAX_FILE_SIZE:
                return compressed_data, target_format

            # 如果文件太大，逐步降低质量
            while len(compressed_data) > self.MAX_FILE_SIZE and quality > 10:
                quality -= 10
                output.seek(0)
                output.truncate(0)
                processed_img.save(output, format=target_format, quality=quality, optimize=True)
                compressed_data = output.getvalue()

            # 如果质量降到最低仍然太大，尝试缩小尺寸
            if len(compressed_data) > self.MAX_FILE_SIZE:
                # 逐步缩小尺寸
                scale_factor = 0.9
                while len(compressed_data) > self.MAX_FILE_SIZE and scale_factor > 0.3:
                    new_width = int(processed_img.width * scale_factor)
                    new_height = int(processed_img.height * scale_factor)
                    resized_img = processed_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                    output.seek(0)
                    output.truncate(0)
                    resized_img.save(output, format=target_format, quality=85, optimize=True)
                    compressed_data = output.getvalue()

                    scale_factor -= 0.1

            return compressed_data, target_format


# 全局实例
image_processor = ImageProcessor()


def process_uploaded_image(image_data: bytes, filename: str = "") -> dict:
    """
    处理上传的图片（便捷函数）

    Args:
        image_data: 图片二进制数据
        filename: 文件名

    Returns:
        dict: 处理结果
    """
    return image_processor.process_image(image_data, filename)
