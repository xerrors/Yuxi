"""沙盒配置常量

定义沙盒系统的配置项，包括镜像、路径、超时等。
"""

from __future__ import annotations

import os

# 沙盒容器配置
DEFAULT_SANDBOX_IMAGE = "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"
DEFAULT_SANDBOX_PORT = 8080
DEFAULT_CONTAINER_PREFIX = "yuxi-sandbox"

# 空闲超时配置（秒）
DEFAULT_IDLE_TIMEOUT = 600  # 10 分钟
IDLE_CHECK_INTERVAL = 60  # 1 分钟检查一次

# 最大并发沙盒数
DEFAULT_MAX_REPLICAS = 3

# 虚拟路径前缀（容器内）
VIRTUAL_PATH_PREFIX = "/mnt"

# 容器内用户数据路径
USER_DATA_PATH = f"{VIRTUAL_PATH_PREFIX}/user-data"
SKILLS_PATH = f"{VIRTUAL_PATH_PREFIX}/skills"

# 子目录
WORKSPACE_DIR = "workspace"
OUTPUTS_DIR = "outputs"
UPLOADS_DIR = "uploads"
LARGE_TOOL_RESULTS_DIR = "large_tool_results"
THREADS_DIR = "threads"


def get_sandbox_image() -> str:
    """获取沙盒镜像，默认使用配置值或环境变量"""
    return os.getenv("YUXI_SANDBOX_IMAGE", DEFAULT_SANDBOX_IMAGE)


def get_sandbox_base_port() -> int:
    """获取沙盒基础端口"""
    return int(os.getenv("YUXI_SANDBOX_BASE_PORT", str(DEFAULT_SANDBOX_PORT)))


def get_container_prefix() -> str:
    """获取容器名前缀"""
    return os.getenv("YUXI_SANDBOX_CONTAINER_PREFIX", DEFAULT_CONTAINER_PREFIX)


def get_idle_timeout() -> int:
    """获取空闲超时时间（秒）"""
    return int(os.getenv("YUXI_SANDBOX_IDLE_TIMEOUT", str(DEFAULT_IDLE_TIMEOUT)))


def get_max_replicas() -> int:
    """获取最大并发沙盒数"""
    return int(os.getenv("YUXI_SANDBOX_MAX_REPLICAS", str(DEFAULT_MAX_REPLICAS)))


def get_sandbox_host() -> str:
    """获取沙盒主机地址（宿主机侧访问容器）"""
    return os.getenv("YUXI_SANDBOX_HOST", "localhost")


def get_sandbox_provisioner_url() -> str:
    return os.getenv("YUXI_SANDBOX_PROVISIONER_URL", "").strip()
