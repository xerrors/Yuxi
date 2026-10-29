"""`.xls` 按文件头嗅探分派与伪 xls 兼容测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import pytest

import yuxi.infrastructure.document_parsing.parser as parser_unified
from yuxi.modules.documents.service import parse

PARSER_FIXTURES = Path(__file__).parents[2] / "data"


def test_biff_xls_still_parses() -> None:
    markdown = parser_unified._convert_legacy_excel_to_markdown(PARSER_FIXTURES / "测试旧表格.xls")
    assert "Docling Slim" in markdown
    assert "53" in markdown


def test_renamed_xlsx_falls_back_to_openpyxl(tmp_path: Path) -> None:
    renamed = tmp_path / "renamed.xls"
    renamed.write_bytes((PARSER_FIXTURES / "测试表格.xlsx").read_bytes())
    with pytest.raises(Exception):
        pd.read_excel(renamed, engine="xlrd", sheet_name=None, header=None)

    markdown = parser_unified._convert_legacy_excel_to_markdown(renamed)
    assert "个人所得税计算" in markdown


def test_spreadsheetml_parses_with_index_and_merge(tmp_path: Path) -> None:
    sample = """<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
  <Worksheet ss:Name="数据" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
    <Table>
      <Row><Cell ss:Index="2"><Data ss:Type="String">名称</Data></Cell><Cell><Data ss:Type="String">数量</Data></Cell></Row>
      <Row><Cell><Data ss:Type="String">设备甲</Data></Cell><Cell><Data ss:Type="Number">3</Data></Cell></Row>
    </Table>
  </Worksheet>
</Workbook>"""
    path = tmp_path / "spreadsheetml.xls"
    path.write_bytes(sample.encode("utf-8"))
    with pytest.raises(Exception):
        pd.read_excel(path, engine="xlrd", sheet_name=None, header=None)

    markdown = parser_unified._convert_legacy_excel_to_markdown(path)
    assert "名称" in markdown
    assert "设备甲" in markdown


def test_html_table_masquerading_as_xls(tmp_path: Path) -> None:
    path = tmp_path / "html.xls"
    path.write_bytes(b"<html><body><table><tr><th>name</th><th>qty</th></tr><tr><td>radar</td><td>2</td></tr></table></body></html>")
    with pytest.raises(Exception):
        pd.read_excel(path, engine="xlrd", sheet_name=None, header=None)

    markdown = parser_unified._convert_legacy_excel_to_markdown(path)
    assert "radar" in markdown
    assert "name" in markdown


def test_spreadsheetml_without_content_fails_readable(tmp_path: Path) -> None:
    path = tmp_path / "empty.xls"
    path.write_bytes(b'<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"></Workbook>')
    with pytest.raises(ValueError, match="未解析出任何表格内容"):
        parser_unified._convert_legacy_excel_to_markdown(path)


def test_unrecognizable_content_fails_readable(tmp_path: Path) -> None:
    path = tmp_path / "garbage.xls"
    path.write_bytes(b"\x00\x01\x02\x03not a workbook")
    with pytest.raises(ValueError, match="不是可识别的 Excel 工作簿"):
        parser_unified._convert_legacy_excel_to_markdown(path)


async def test_parse_routes_xls_through_header_sniffing() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        result = await parse(str(PARSER_FIXTURES / "测试旧表格.xls"), Path(temporary) / "parsed")
        markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Docling Slim" in markdown
