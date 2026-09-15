"""已接受任务的文档限额快照；解析阶段不重新读取管理员配置。"""

from pathlib import Path


def validate_document_limits(value: dict | None) -> dict | None:
    """校验持久任务边界的整数快照，不将布尔值或字符串解释为限额。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid document limits snapshot")
    for key in ("max_file_bytes", "max_ocr_pages", "version"):
        if type(value.get(key)) is not int or value[key] < (0 if key == "version" else 1):
            raise ValueError(f"Invalid document limits snapshot: {key}")
    return {key: value[key] for key in ("max_file_bytes", "max_ocr_pages", "version")}


def validate_local_document(file_path: str | Path, limits: dict | None) -> None:
    """在解析或推理前按入队快照检查字节数及PDF页数。"""
    if limits is None:
        return
    path = Path(file_path)
    if path.stat().st_size > limits["max_file_bytes"]:
        raise ValueError(f"Document exceeds accepted limit of {limits['max_file_bytes']} bytes")
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        from yuxi.knowledge.utils.pdf_utils import validate_pdf_page_tree_loadable

        validate_pdf_page_tree_loadable(path)

        with path.open("rb") as stream:
            pages = len(PdfReader(stream).pages)
        if pages > limits["max_ocr_pages"]:
            raise ValueError(f"PDF exceeds accepted OCR limit of {limits['max_ocr_pages']} pages")
