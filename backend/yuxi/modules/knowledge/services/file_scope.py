"""精确文件条件与可信读取上限的交集。"""

from yuxi.shared.errors import ResourceNotFoundError


def intersect_file_ids(file_ids: list[str] | None, allowed_file_ids: list[str] | None) -> list[str] | None:
    """保留未限制与明确空集的区别，不把空集放宽为整库。"""
    if file_ids is None:
        return allowed_file_ids
    if allowed_file_ids is None:
        return list(dict.fromkeys(file_ids))
    allowed = set(allowed_file_ids)
    return list(dict.fromkeys(file_id for file_id in file_ids if file_id in allowed))


def require_file_in_scope(file_id: str, allowed_file_ids: list[str] | None) -> None:
    """在对象读取前拒绝可信范围外文件，不披露其是否存在。"""
    if allowed_file_ids is not None and file_id not in allowed_file_ids:
        raise ResourceNotFoundError("文件不存在或不可见")
