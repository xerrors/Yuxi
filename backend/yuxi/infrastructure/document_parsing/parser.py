"""本地文档到完整 Markdown 目录的唯一转换入口。"""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import threading
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup
from docling.backend.msexcel_backend import MsExcelDocumentBackend
from docling.backend.mspowerpoint_backend import MsPowerpointDocumentBackend
from docling.backend.msword_backend import MsWordDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument
from docling_core.types.doc import DoclingDocument
from markdownify import markdownify as md_convert
from pypdf import PdfReader

from yuxi.infrastructure.document_parsing import IMAGE_FILE_EXTENSIONS, ParseOptions, ParseResult
from yuxi.infrastructure.document_parsing.artifacts import (
    ensure_no_symlink_ancestors,
    extract_markdown_archive,
    local_image_links,
    replace_image_links,
    resource_path,
    save_resource,
)
from yuxi.infrastructure.document_parsing.pdf_utils import validate_pdf_page_tree_loadable
from yuxi.infrastructure.document_parsing.text_encoding import decode_user_text
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.observability.logging import logger


class WordDocumentBackend(MsWordDocumentBackend):
    """将无引用的 VML 图形元数据与真正的图片资源分开处理。"""

    def _handle_vml_pictures(self, vml_imagedatas, doc: DoclingDocument):
        """仅排除空或只有标题的节点，未知属性及无效引用仍严格处理。"""
        image_nodes = []
        for node in vml_imagedatas:
            metadata_only = all(key == "{urn:schemas-microsoft-com:office:office}title" for key in node.attrib)
            if metadata_only and len(node) == 0 and not (node.text or "").strip():
                continue
            image_nodes.append(node)
        omitted = len(vml_imagedatas) - len(image_nodes)
        if omitted:
            logger.info(f"DOCX 识别到 {omitted} 个无图片引用的 VML 图形元数据节点")
        return super()._handle_vml_pictures(image_nodes, doc)


_OFFICE_BACKENDS = {
    ".docx": (InputFormat.DOCX, WordDocumentBackend),
    ".pptx": (InputFormat.PPTX, MsPowerpointDocumentBackend),
    ".xlsx": (InputFormat.XLSX, MsExcelDocumentBackend),
}
_docling_office_lock = threading.Lock()


async def parse(source: str | Path, output_dir: str | Path, options: ParseOptions | None = None) -> ParseResult:
    """将本地文档转换为独占目录；调用方负责目录的持久化与清理。"""
    source = Path(source)
    output_dir = Path(output_dir)
    options = options or ParseOptions()
    if not source.is_file():
        raise ValueError(f"文件不存在: {source}")
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        return await await_io(asyncio.to_thread(_convert_document, source, output_dir, options))
    except BaseException:
        await await_io(asyncio.to_thread(shutil.rmtree, output_dir))
        raise


def _convert_document(source: Path, output_dir: Path, options: ParseOptions) -> ParseResult:
    """在同一 I/O 任务中生成并验证完整产物。"""
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        validate_pdf_page_tree_loadable(source)
        markdown = _parse_ocr(source, output_dir, options)
    elif suffix in IMAGE_FILE_EXTENSIONS:
        markdown = _parse_ocr(source, output_dir, options)
    elif suffix in (".txt", ".md"):
        markdown = decode_user_text(source.read_bytes(), source.name)
        if suffix == ".md":
            markdown = _copy_markdown_resources(source, output_dir, markdown)
    elif suffix in (".docx", ".pptx", ".xlsx"):
        markdown = _convert_with_docling(source, output_dir)
    elif suffix == ".xls":
        markdown = _convert_legacy_excel_to_markdown(source)
    elif suffix == ".csv":
        markdown = _convert_csv_to_markdown(source)
    elif suffix in (".html", ".htm"):
        html_text_only = options.params.get("html_text_only", False)
        if not isinstance(html_text_only, bool):
            raise ValueError("html_text_only 必须是布尔值")
        content = decode_user_text(source.read_bytes(), source.name)
        if html_text_only:
            document = BeautifulSoup(content, "html.parser")
            for image in document.find_all("img"):
                image.replace_with(image.get("alt", ""))
            for video in document.find_all("video"):
                video.attrs.pop("poster", None)
            content = str(document)
        markdown = md_convert(content, heading_style="ATX")
        if not html_text_only:
            markdown = _copy_markdown_resources(source, output_dir, markdown)
    elif suffix == ".json":
        content = decode_user_text(source.read_bytes(), source.name)
        markdown = f"```json\n{json.dumps(json.loads(content), ensure_ascii=False, indent=2)}\n```"
    elif suffix == ".zip":
        markdown = extract_markdown_archive(str(source), output_dir)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")
    # PostgreSQL 文本不接受 NUL；单字符替换保留正文位置与其余提取内容。
    markdown = markdown.replace("\x00", "\ufffd")
    # 只有资源完整且路径有效时才发布 Markdown 入口。
    for link in local_image_links(markdown):
        relative = resource_path(unquote(link))
        if not (output_dir / relative).is_file():
            raise ValueError(f"解析产物缺少图片: {link}")
    markdown_path = output_dir / "document.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    resources = tuple(
        sorted(path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*") if path.is_file() and path != markdown_path)
    )
    return ParseResult(output_dir, markdown_path, resources)


def _parse_ocr(source: Path, output_dir: Path, options: ParseOptions) -> str:
    """按显式引擎解析 PDF 或图片，禁用 OCR 时只读取 PDF 文本。"""
    # 具体引擎按需导入，避免 API 启动时加载 OCR 依赖。
    from yuxi.infrastructure.document_parsing.engines import get_engine, get_engine_spec

    if options.ocr_engine == "disable":
        if source.suffix.lower() != ".pdf":
            raise ValueError("图像文件必须启用 OCR 才能提取文本")
        with source.open("rb") as stream:
            return "\n\n".join((page.extract_text(extraction_mode="plain") or "").strip() for page in PdfReader(stream).pages)
    spec = get_engine_spec(options.ocr_engine)
    if source.suffix.lower() not in spec.supported_extensions:
        raise ValueError(f"OCR 引擎 {options.ocr_engine} 不支持文件类型 {source.suffix}")
    return get_engine(options.ocr_engine, **options.processor_kwargs).process_file(str(source), output_dir, options.params)


def _copy_markdown_resources(source: Path, output_dir: Path, markdown: str) -> str:
    """复制源文档旁的图片，拒绝符号链接和越界。"""
    mapping = {}
    copied = set()
    for link in local_image_links(markdown):
        relative = resource_path(unquote(link))
        resource = source.parent / relative
        ensure_no_symlink_ancestors(resource, source.parent, error_message=f"源资源包含符号链接: {link}")
        target = f"images/{relative}"
        if target not in copied:
            save_resource(output_dir, target, resource.read_bytes())
            copied.add(target)
        mapping[link] = target
    return replace_image_links(markdown, mapping)


def _convert_with_docling(file_path: Path, output_dir: Path) -> str:
    """转换 Office 文档并保存图片，资源提取失败时拒绝整个结果。"""
    with _docling_office_lock:
        doc = _convert_office_document(file_path)
    markdown = doc.export_to_markdown()
    for index, picture in enumerate(doc.pictures):
        image = getattr(picture, "image", None)
        if image is None:
            raise ValueError(f"Office 图片缺少图像数据: {index}")
        image_data = image.pil_image
        if image_data is None:
            raise ValueError(f"Office 图片缺少图像数据: {index}")
        buffer = io.BytesIO()
        image_data.save(buffer, format="PNG")
        relative = save_resource(output_dir, f"images/figure-{index + 1:03d}.png", buffer.getvalue())
        markdown = re.sub(r"<!--\s*image\s*-->", f"![image]({relative})", markdown, count=1)
    return markdown


def _convert_office_document(file_path: Path) -> DoclingDocument:
    """用 Docling Slim 的格式 backend 转换一个 Office 文件。"""
    try:
        input_format, backend_type = _OFFICE_BACKENDS[file_path.suffix.lower()]
    except KeyError as exc:
        raise ValueError(f"Docling 不支持该 Office 格式: {file_path.suffix}") from exc

    input_document = InputDocument(file_path, input_format, backend_type)
    # Docling Slim 仅通过 InputDocument 持有已初始化的格式 backend。
    backend = getattr(input_document, "_backend", None)
    if backend is None:
        raise RuntimeError(f"Docling 无法读取 Office 文件: {file_path.name}")

    try:
        if not input_document.valid:
            raise RuntimeError(f"Docling 无法读取 Office 文件: {file_path.name}")
        return backend.convert()
    finally:
        backend.unload()


def _convert_csv_to_markdown(file_path: Path) -> str:
    """把 CSV 的每一行渲染为独立 Markdown 表格，列类型由 pandas 推断。"""
    import io

    import pandas as pd

    dataframe = pd.read_csv(io.StringIO(decode_user_text(file_path.read_bytes(), file_path.name)))
    tables: list[str] = []
    for i in range(len(dataframe)):
        row_dataframe = dataframe.iloc[[i]]
        tables.append(row_dataframe.to_markdown(index=False))
    return "\n\n".join(tables)


def _convert_legacy_excel_to_markdown(file_path: Path) -> str:
    """按真实文件头解析 .xls，兼容常见的"伪 xls"。

    WPS 与各类网页系统导出的 `.xls` 实际可能是 xlsx 改名、XML Spreadsheet 2003 或
    网页表格另存，而 xlrd 只认旧版 BIFF 二进制；扩展名直读会让这些文件原样抛出底层
    英文异常并落为解析失败。嗅探失败时给出可执行的中文错误。
    """
    with file_path.open("rb") as handle:
        head = handle.read(8)

    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _convert_xls_to_markdown(file_path)
    if head.startswith(b"PK\x03\x04"):
        return _convert_xlsx_workbook_to_markdown(file_path)
    if head[:2] in (b"\xff\xfe", b"\xfe\xff") or head.lstrip(b" \t\r\n\xef\xbb\xbf").startswith(b"<"):
        raw = file_path.read_bytes()
        text = _decode_legacy_markup(raw, file_path.name)
        if "schemas-microsoft-com:office:spreadsheet" in text:
            return _convert_spreadsheetml_to_markdown(text, file_path.name)
        return _convert_html_tables_to_markdown(text, file_path.name)
    raise ValueError(f"{file_path.name} 不是可识别的 Excel 工作簿，请在 WPS/Excel 中另存为 .xlsx 后重新上传")


def _decode_legacy_markup(raw: bytes, filename: str) -> str:
    """解码 markup 类 .xls（SpreadsheetML/HTML）的声明编码；不依赖其它解码模块。"""
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    logger.warning(f"{filename} 声明的编码无法解码，按 UTF-8 替换兜底")
    return raw.decode("utf-8", errors="replace")


def _render_sheet_map_to_markdown(sheet_map: dict, filename: str) -> str:
    """把工作表字典渲染为 Markdown；无任何非空表时显式失败，不产出空文档。"""
    blocks: list[str] = []
    for sheet_name, dataframe in sheet_map.items():
        if dataframe.empty:
            continue
        if len(sheet_map) > 1:
            blocks.append(f"## {sheet_name}")
        blocks.append(dataframe.to_markdown(index=False, headers=[""] * len(dataframe.columns), disable_numparse=True))
    if not blocks:
        raise ValueError(f"{filename} 未解析出任何表格内容，工作簿可能为空")
    return "\n\n".join(blocks)


def _convert_xls_to_markdown(file_path: Path) -> str:
    """使用 pandas + xlrd 解析旧版 BIFF .xls 文件并转为 Markdown（Docling 不支持该格式）。"""
    import pandas as pd

    # 文档提取不假定首行是表头，也不把文本编号和 NA 等字面值转换成分析数据。
    sheet_map = pd.read_excel(file_path, engine="xlrd", sheet_name=None, header=None, dtype=object, keep_default_na=False)
    return _render_sheet_map_to_markdown(sheet_map, file_path.name)


def _convert_xlsx_workbook_to_markdown(file_path: Path) -> str:
    """解析 zip 容器（以 .xls 命名的 xlsx）：openpyxl 读取，渲染口径与 BIFF 路径一致。"""
    import pandas as pd

    sheet_map = pd.read_excel(file_path, engine="openpyxl", sheet_name=None, header=None, dtype=object, keep_default_na=False)
    return _render_sheet_map_to_markdown(sheet_map, file_path.name)


def _convert_spreadsheetml_to_markdown(text: str, filename: str) -> str:
    """解析 XML Spreadsheet 2003（WPS/Excel 早期另存格式）为 Markdown 网格。"""
    import xml.etree.ElementTree as ET

    import pandas as pd

    ns = "urn:schemas-microsoft-com:office:spreadsheet"
    sheet_map: dict[str, pd.DataFrame] = {}
    for worksheet in ET.fromstring(text).findall(f"{{{ns}}}Worksheet"):
        sheet_name = worksheet.get(f"{{{ns}}}Name", "")
        rows: list[list[str]] = []
        width = 0
        for row in worksheet.findall(f".//{{{ns}}}Row"):
            cells: list[str] = []
            for cell in row.findall(f"{{{ns}}}Cell"):
                index = cell.get(f"{{{ns}}}Index")
                if index is not None:
                    cells.extend([""] * (int(index) - len(cells) - 1))
                data = cell.find(f"{{{ns}}}Data")
                cells.append((data.text or "") if data is not None else "")
                cells.extend([""] * int(cell.get(f"{{{ns}}}MergeAcross") or 0))
            rows.append(cells)
            width = max(width, len(cells))
        grid = [row + [""] * (width - len(row)) for row in rows]
        sheet_map[sheet_name] = pd.DataFrame(grid, columns=range(width)) if grid else pd.DataFrame()
    return _render_sheet_map_to_markdown(sheet_map, filename)


def _convert_html_tables_to_markdown(text: str, filename: str) -> str:
    """解析网页另存的 .xls（HTML 表格）：read_html 保留 <th> 表头。"""
    import io

    import pandas as pd

    tables = [table for table in pd.read_html(io.StringIO(text)) if not table.empty]
    if not tables:
        raise ValueError(f"{filename} 未解析出任何表格内容，页面可能不含数据表")
    if len(tables) == 1:
        return tables[0].to_markdown(index=False, disable_numparse=True)
    return "\n\n".join(f"## 表 {i + 1}\n\n{table.to_markdown(index=False, disable_numparse=True)}" for i, table in enumerate(tables))
