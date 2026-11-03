"""将 HTML 表格转换为保留合并关系的独立检索行。"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag


def html_table_to_key_value(html: str) -> list[str]:
    """按单元格身份继承纵向类别、去除横向重复，不猜测跨字段内容归属。"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    # 嵌套表格不能按同一套列坐标铺平，由调用方保留原 HTML。
    if table is None or table.find("table") is not None:
        return []

    rows = table.find_all("tr")
    if not rows:
        return []

    grid: list[list[Tag | None]] = [[] for _ in rows]
    for r_idx, row in enumerate(rows):
        cells = row.find_all(["td", "th"])
        c_idx = 0
        for cell in cells:
            while c_idx < len(grid[r_idx]) and grid[r_idx][c_idx] is not None:
                c_idx += 1

            rowspan = int(cell.get("rowspan", 1))
            colspan = int(cell.get("colspan", 1))
            if rowspan < 1 or colspan < 1:
                raise ValueError("表格单元格跨度必须为正数")

            for target_r in range(r_idx, min(r_idx + rowspan, len(rows))):
                for c in range(colspan):
                    target_c = c_idx + c
                    while len(grid[target_r]) <= target_c:
                        grid[target_r].append(None)
                    # 同一合并格共享 Tag；相同文字的独立单元格仍有不同身份。
                    grid[target_r][target_c] = cell
            c_idx += colspan

    # 只相信显式 thead；表体中的 th 也可能是数据，不能据此猜测多层列名。
    header_rows = [index for index, row in enumerate(rows) if row.find_parent("thead") is not None] or [0]

    column_keys = []
    column_labels = []
    for col in range(max(map(len, grid))):
        headers = {}
        for row_index in header_rows:
            row = grid[row_index]
            cell = row[col] if col < len(row) else None
            if cell is not None and cell.get_text(strip=True):
                headers[id(cell)] = cell.get_text(" ", strip=True)
        column_keys.append(tuple(headers))
        column_labels.append(" / ".join(headers.values()))

    kv_lines = []
    caption = table.find("caption", recursive=False)
    for row_index, row_values in enumerate(grid):
        if row_index in header_rows:
            continue
        cells_by_id: dict[int, tuple[Tag, list[int]]] = {}
        for col, cell in enumerate(row_values):
            if cell is not None:
                cells_by_id.setdefault(id(cell), (cell, []))[1].append(col)

        fields: dict[tuple[int, ...] | int, tuple[str, list[str]]] = {}
        for cell_id, (cell, columns) in cells_by_id.items():
            keys = {column_keys[col] for col in columns}
            key = next(iter(keys)) if len(keys) == 1 else ()
            label = column_labels[columns[0]] if key else ""
            # 跨逻辑字段的说明不属于其中任一字段；空表头也不能吞掉内容。
            field_key = key if label else cell_id
            fields.setdefault(field_key, (label, []))[1].append(cell.get_text(" ", strip=True))

        row_parts = [caption.get_text(" ", strip=True)] if caption is not None else []
        for label, values in fields.values():
            value = " / ".join(values)
            row_parts.append(f"{label}：{value}" if label else value)
        if row_parts:
            kv_lines.append("；".join(row_parts) + "；")

    return kv_lines
