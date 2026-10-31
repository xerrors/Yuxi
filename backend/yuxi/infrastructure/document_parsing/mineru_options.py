"""MinerU 已持久化的 3.x 选项到 4.x 档位的迁移政策。"""

from typing import Any


def migrate_mineru_options(values: dict[str, Any]) -> dict[str, Any]:
    """保留显式新档位，否则按旧计算强度迁移；不承诺识别质量等价。"""
    migrated = dict(values)
    if not {"backend", "effort"}.intersection(migrated):
        return migrated
    backend = migrated.pop("backend", "hybrid-engine")
    effort = migrated.pop("effort", "medium")
    if "tier" not in migrated:
        if backend not in {"hybrid-engine", "vlm-engine"} or effort not in {"medium", "high"}:
            raise ValueError("MinerU 旧 backend/effort 配置无法迁移，请显式选择 tier")
        migrated["tier"] = "advanced" if backend == "hybrid-engine" and effort == "high" else "standard"
    return migrated
