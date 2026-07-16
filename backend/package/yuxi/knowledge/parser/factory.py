"""
文档处理器工厂

提供统一的文档处理器创建和管理接口
"""

import asyncio
from importlib import import_module
from typing import Any

from yuxi.knowledge.parser.base import BaseDocumentProcessor
from yuxi.knowledge.parser.registry import PROCESSOR_TYPES
from yuxi.utils import logger

# 处理器实例缓存
_PROCESSOR_CACHE: dict[str, BaseDocumentProcessor] = {}


class DocumentProcessorFactory:
    """文档处理器工厂"""

    PROCESSOR_TYPES = PROCESSOR_TYPES

    @classmethod
    def _build_cache_key(cls, processor_type: str, kwargs: dict[str, Any]) -> str:
        if not kwargs:
            return processor_type

        kwargs_repr = "|".join(f"{key}={kwargs[key]!r}" for key in sorted(kwargs))
        return f"{processor_type}|{kwargs_repr}"

    @classmethod
    def _load_processor_class(cls, processor_type: str) -> type[BaseDocumentProcessor]:
        module_path, class_name = cls.PROCESSOR_TYPES[processor_type]
        module = import_module(module_path)
        processor_class = getattr(module, class_name)
        return processor_class

    @classmethod
    def get_processor(cls, processor_type: str, **kwargs) -> BaseDocumentProcessor:
        """
        获取文档处理器实例 (单例模式)

        Args:
            processor_type: 处理器类型
                - "rapid_ocr": RapidOCR 本地 OCR
                - "mineru_ocr": MinerU HTTP API 文档解析
                - "mineru_official": MinerU 官方云服务 API 文档解析
                - "pp_structure_v3_ocr": PP-Structure-V3 版面解析
                - "deepseek_ocr": DeepSeek-OCR SiliconFlow API
                - "paddleocr_vl_1_6": PaddleOCR-VL-1.6 云端 API 文档解析
                - "paddleocr_pp_ocrv6": PP-OCRv6 云端 API 文字识别
            **kwargs: 处理器初始化参数

        Returns:
            BaseDocumentProcessor: 处理器实例

        Raises:
            ValueError: 不支持的处理器类型
        """
        if processor_type not in cls.PROCESSOR_TYPES:
            raise ValueError(f"不支持的处理器类型: {processor_type}. 支持的类型: {list(cls.PROCESSOR_TYPES.keys())}")

        # 使用缓存避免重复创建
        cache_key = cls._build_cache_key(processor_type, kwargs)
        if cache_key not in _PROCESSOR_CACHE:
            processor_class = cls._load_processor_class(processor_type)
            _PROCESSOR_CACHE[cache_key] = processor_class(**kwargs)
            logger.debug(f"创建文档处理器: {processor_type}")

        return _PROCESSOR_CACHE[cache_key]

    @classmethod
    def process_file(cls, processor_type: str, file_path: str, params: dict | None = None) -> str:
        """
        使用指定处理器处理文件 (便捷方法)

        Args:
            processor_type: 处理器类型
            file_path: 文件路径
            params: 处理参数

        Returns:
            str: 提取的文本

        Raises:
            DocumentProcessorException: 处理失败
        """
        processor = cls.get_processor(processor_type)
        return processor.process_file(file_path, params)

    @classmethod
    def check_health(cls, processor_type: str) -> dict[str, Any]:
        """
        检查指定处理器的健康状态

        Args:
            processor_type: 处理器类型

        Returns:
            dict: 健康状态信息
        """
        try:
            processor = cls.get_processor(processor_type)
            return processor.check_health()
        except Exception as e:
            return {
                "status": "error",
                "message": f"健康检查失败: {str(e)}",
                "details": {"error": str(e)},
            }

    @classmethod
    def check_all_health(cls) -> dict[str, dict[str, Any]]:
        """
        检查所有处理器的健康状态

        Returns:
            dict: 各处理器的健康状态
        """
        health_status = {}
        for processor_type in cls.PROCESSOR_TYPES:
            health_status[processor_type] = cls.check_health(processor_type)
        return health_status

    @classmethod
    async def check_all_health_async(cls) -> dict[str, dict[str, Any]]:
        async def run_check(processor_type: str) -> tuple[str, dict[str, Any]]:
            return processor_type, await asyncio.to_thread(cls.check_health, processor_type)

        results = await asyncio.gather(*(run_check(processor_type) for processor_type in cls.PROCESSOR_TYPES))
        return {processor_type: health for processor_type, health in results}

    @classmethod
    def get_available_processors(cls) -> list[str]:
        """返回所有可用的处理器类型"""
        return list(cls.PROCESSOR_TYPES.keys())

    @classmethod
    def clear_cache(cls):
        """清除处理器缓存"""
        _PROCESSOR_CACHE.clear()
        logger.debug("文档处理器缓存已清除")
