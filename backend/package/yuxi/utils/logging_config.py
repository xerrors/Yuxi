import logging
import os
import sys

from loguru import logger as loguru_logger

from yuxi.utils.datetime_utils import shanghai_now

SAVE_DIR = os.getenv("SAVE_DIR") or "saves"
DATETIME = shanghai_now().strftime("%Y-%m-%d")
LOG_FILE = f"{SAVE_DIR}/logs/yuxi-{DATETIME}.log"


class LoguruHandler(logging.Handler):
    """将 Python logging 桥接到 loguru 的 handler"""

    def emit(self, record: logging.LogRecord):
        level_map = {
            logging.DEBUG: "DEBUG",
            logging.INFO: "INFO",
            logging.WARNING: "WARNING",
            logging.ERROR: "ERROR",
            logging.CRITICAL: "CRITICAL",
        }
        level = level_map.get(record.levelno, "DEBUG")
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        loguru_logger.opt(depth=1, exception=record.exc_info).log(level, msg)


def _setup_logging_bridge():
    """配置 logging 到 loguru 的桥接，捕获第三方库日志（如 LightRAG）"""
    loguru_handler = LoguruHandler()
    loguru_handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    loguru_handler.setFormatter(formatter)

    # 桥接 LightRAG 日志
    lightrag_logger = logging.getLogger("lightrag")
    lightrag_logger.addHandler(loguru_handler)
    lightrag_logger.setLevel(logging.DEBUG)
    lightrag_logger.propagate = False  # 避免重复

    # 桥接 MCP 服务层日志，便于在 agent 运行时直接观察 MCP 选择、加载和失败冷却。
    mcp_logger = logging.getLogger("yuxi.mcp")
    mcp_logger.addHandler(loguru_handler)
    mcp_logger.setLevel(logging.INFO)
    mcp_logger.propagate = False

    # 桥接其他常见第三方库（降低级别减少噪音）
    for lib in ["httpx", "openai", "neo4j", "urllib3"]:
        lib_logger = logging.getLogger(lib)
        lib_logger.addHandler(loguru_handler)
        lib_logger.setLevel(logging.WARNING)
        lib_logger.propagate = False


def setup_logger(name, level="DEBUG", console=True):
    """使用 loguru 设置日志记录器"""
    os.makedirs(f"{SAVE_DIR}/logs", exist_ok=True)

    # 移除默认的 handler
    loguru_logger.remove()

    # 添加文件日志（无颜色）
    loguru_logger.add(
        LOG_FILE,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} - {level} - {file}:{line} - {message}",
        encoding="utf-8",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
    )

    # 添加控制台日志（有颜色）
    if console:
        loguru_logger.add(
            sys.stderr,
            level=level,
            format=(
                "<green>{time:MM-DD HH:mm:ss}</green> "
                "<level>{level}</level> "
                "<cyan>{file}:{line}</cyan>: "
                "<level>{message}</level>"
            ),
            colorize=True,
            enqueue=True,
        )

    return loguru_logger


# 设置根日志记录器
logger = setup_logger("Yuxi")

# 初始化 logging 桥接
_setup_logging_bridge()

__all__ = ["logger"]
