"""应用配置模块。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import tomli
import tomli_w
from pydantic import BaseModel, Field, PrivateAttr

from yuxi.utils.logging_config import logger


class Config(BaseModel):
    """应用配置类。"""

    save_dir: str = Field(default="saves", description="保存目录")
    enable_reranker: bool = Field(default=True, description="是否开启重排序")
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
        default="yuxi-embedding:bge-m3",
        description="默认 Embedding 模型",
    )
    reranker: str = Field(
        default="yuxi-reranker:bge-reranker-v2-m3",
        description="默认 Re-Ranker 模型",
    )
    content_guard_llm_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="内容审查LLM模型",
    )

    default_agent_id: str = Field(default="ChatbotAgent", description="默认智能体ID")

    sandbox_provider: str = Field(default="provisioner", description="沙箱提供者")
    sandbox_provisioner_url: str = Field(default="http://sandbox-provisioner:8002", description="沙箱服务地址")
    sandbox_virtual_path_prefix: str = Field(default="/home/gem/user-data", description="沙箱用户目录前缀")
    sandbox_exec_timeout_seconds: int = Field(default=180, description="沙箱执行超时时间（秒）")
    sandbox_max_output_bytes: int = Field(default=262144, description="沙箱最大输出字节数")
    sandbox_keepalive_interval_seconds: int = Field(default=30, description="沙箱保活间隔")

    _config_file: Path | None = PrivateAttr(default=None)
    _user_modified_fields: set[str] = PrivateAttr(default_factory=set)

    model_config = {"arbitrary_types_allowed": True, "extra": "allow"}

    def __init__(self, **data):
        super().__init__(**data)
        self._setup_paths()
        self._load_user_config()
        self._handle_environment()

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

            self._user_modified_fields = set(user_config.keys())

            for key, value in user_config.items():
                if hasattr(self, key):
                    setattr(self, key, value)
                else:
                    logger.warning(f"Unknown config key: {key}")

        except Exception as e:
            logger.error(f"Failed to load config from {self._config_file}: {e}")

    def _handle_environment(self) -> None:
        self.sandbox_provider = (os.getenv("SANDBOX_PROVIDER") or self.sandbox_provider or "provisioner").strip()
        self.sandbox_provisioner_url = (
            os.getenv("SANDBOX_PROVISIONER_URL") or self.sandbox_provisioner_url or "http://sandbox-provisioner:8002"
        ).strip()
        self.sandbox_virtual_path_prefix = (
            os.getenv("SANDBOX_VIRTUAL_PATH_PREFIX") or self.sandbox_virtual_path_prefix or "/home/gem/user-data"
        ).strip()
        self.sandbox_exec_timeout_seconds = int(
            os.getenv("SANDBOX_EXEC_TIMEOUT_SECONDS") or self.sandbox_exec_timeout_seconds or 180
        )
        self.sandbox_max_output_bytes = int(
            os.getenv("SANDBOX_MAX_OUTPUT_BYTES") or self.sandbox_max_output_bytes or 262144
        )
        self.sandbox_keepalive_interval_seconds = int(
            os.getenv("SANDBOX_KEEPALIVE_INTERVAL_SECONDS") or self.sandbox_keepalive_interval_seconds or 30
        )

        if self.sandbox_provider.lower() != "provisioner":
            raise ValueError("Only sandbox_provider=provisioner is supported.")
        if not self.sandbox_provisioner_url:
            raise ValueError("SANDBOX_PROVISIONER_URL is required when sandbox provider is provisioner.")
        if not self.sandbox_virtual_path_prefix.startswith("/"):
            self.sandbox_virtual_path_prefix = f"/{self.sandbox_virtual_path_prefix}"

    def save(self) -> None:
        if not self._config_file:
            logger.warning("Config file path not set")
            return

        logger.info(f"Saving config to {self._config_file}")
        default_config = Config.model_construct()
        user_modified = {}
        for field_name, field_info in self.model_fields.items():
            if field_info.exclude:
                continue
            current_value = getattr(self, field_name)
            default_value = getattr(default_config, field_name)
            if current_value != default_value:
                user_modified[field_name] = current_value

        try:
            with open(self._config_file, "wb") as f:
                tomli_w.dump(user_modified, f)
            logger.info(f"Config saved to {self._config_file}")
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
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                logger.warning(f"Unknown config key: {key}")


config = Config()
