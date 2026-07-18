"""应用配置模块。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomli
import tomli_w
from pydantic import BaseModel, Field, PrivateAttr

from yuxi.config import cache as runtime_cache
from yuxi.knowledge.parser.registry import PROCESSOR_TYPES
from yuxi.utils.logging_config import logger

READONLY_CONFIG_FIELDS = frozenset({"save_dir"})
DEFAULT_OCR_ENGINE = "rapid_ocr"


def _get_available_ocr_engines() -> set[str]:
    return {"disable", *PROCESSOR_TYPES}


def _normalize_default_ocr_engine(value: Any) -> str:
    engine = str(value or "").strip() or DEFAULT_OCR_ENGINE
    if engine not in _get_available_ocr_engines():
        raise ValueError(f"不支持的默认 OCR 引擎: {engine}")
    return engine


class Config(BaseModel):
    """应用配置类。

    `save_dir` 只在启动时决定配置文件位置，运行时不可修改。管理员保存配置时先写
    `base.toml`，再把可运行时同步的字段写入 Redis 快照（`yuxi:runtime_config`）。
    其他进程通过 `start_runtime_sync()` 启动的后台线程周期性拉取该快照刷新内存值。
    """

    save_dir: str = Field(default="saves", description="保存目录", exclude=True)
    enable_content_guard: bool = Field(default=False, description="是否启用内容审查")
    enable_content_guard_llm: bool = Field(default=False, description="是否启用LLM内容审查")
    default_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="默认对话模型",
    )
    fast_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="快速响应模型",
    )
    embed_model: str = Field(
        default="siliconflow-cn:Pro/BAAI/bge-m3",
        description="默认 Embedding 模型",
    )
    reranker: str = Field(
        default="siliconflow-cn:Pro/BAAI/bge-reranker-v2-m3",
        description="默认 Re-Ranker 模型",
    )
    content_guard_llm_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="内容审查LLM模型",
    )
    default_ocr_engine: str = Field(default=DEFAULT_OCR_ENGINE, description="默认 OCR 解析引擎")

    _config_file: Path | None = PrivateAttr(default=None)
    _runtime_sync_thread: Any = PrivateAttr(default=None)

    model_config = {"arbitrary_types_allowed": True, "extra": "allow"}

    def __init__(self, **data):
        super().__init__(**data)
        self._setup_paths()
        self._load_user_config()

    def _setup_paths(self) -> None:
        self._config_file = Path(self.save_dir) / "config" / "base.toml"
        self._config_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_user_config(self) -> None:
        if not self._config_file or not self._config_file.exists():
            logger.info(f"Config file not found, using defaults: {self._config_file}")
            return

        logger.info(f"Loading config from {self._config_file}")
        try:
            with open(self._config_file, "rb") as f:
                user_config = tomli.load(f)

            for key, value in user_config.items():
                if key in READONLY_CONFIG_FIELDS:
                    logger.warning(f"Readonly config key ignored: {key}")
                elif key in type(self).model_fields:
                    try:
                        setattr(self, key, self._normalize_config_value(key, value))
                    except ValueError as exc:
                        logger.warning(f"Invalid config key ignored: {key} ({exc})")
                else:
                    logger.warning(f"Unknown config key: {key}")

        except Exception as e:
            logger.error(f"Failed to load config from {self._config_file}: {e}")

    def start_runtime_sync(self, interval: float = runtime_cache.RUNTIME_CONFIG_SYNC_INTERVAL_SECONDS) -> None:
        """启动后台线程周期性从 Redis 同步运行时配置。多次调用仅启动一次。"""
        self._runtime_sync_thread = runtime_cache.start_runtime_sync(
            self,
            self._runtime_sync_thread,
            interval=interval,
        )

    def refresh(self) -> None:
        """从 Redis 快照刷新公开配置字段到内存；Redis 不可用或无快照时保持当前值。"""
        runtime_cache.refresh_runtime_config(self)

    def save(self) -> None:
        if not self._config_file:
            logger.warning("Config file path not set")
            return

        logger.info(f"Saving config to {self._config_file}")
        user_modified = {}
        for field_name, field_info in type(self).model_fields.items():
            if field_info.exclude:
                continue
            current_value = getattr(self, field_name)
            if current_value != field_info.default:
                user_modified[field_name] = current_value

        try:
            with open(self._config_file, "wb") as f:
                tomli_w.dump(user_modified, f)
            logger.info(f"Config saved to {self._config_file}")
            runtime_cache.save_runtime_config(self)
        except Exception as e:
            logger.error(f"Failed to save config to {self._config_file}: {e}")

    def dump_config(self) -> dict[str, Any]:
        config_dict = self.model_dump()
        fields_info = {}
        for field_name, field_info in Config.model_fields.items():
            if field_info.exclude:
                continue
            fields_info[field_name] = {
                "des": field_info.description,
                "default": field_info.default,
                "type": field_info.annotation.__name__
                if hasattr(field_info.annotation, "__name__")
                else str(field_info.annotation),
                "exclude": field_info.exclude if hasattr(field_info, "exclude") else False,
            }
        config_dict["_config_items"] = fields_info
        return config_dict

    def update(self, other: dict[str, Any]) -> None:
        for key, value in other.items():
            if self.can_update(key):
                self.set_value(key, value)
            elif key in READONLY_CONFIG_FIELDS:
                logger.warning(f"Readonly config key ignored: {key}")
            else:
                logger.warning(f"Unknown config key: {key}")

    def can_update(self, key: object) -> bool:
        return isinstance(key, str) and key in type(self).model_fields and key not in READONLY_CONFIG_FIELDS

    def set_value(self, key: str, value: Any) -> None:
        if not self.can_update(key):
            raise ValueError(f"配置项不可修改: {key}")
        setattr(self, key, self._normalize_config_value(key, value))

    def _normalize_config_value(self, key: str, value: Any) -> Any:
        if key == "default_ocr_engine":
            return _normalize_default_ocr_engine(value)
        return value


config = Config()
