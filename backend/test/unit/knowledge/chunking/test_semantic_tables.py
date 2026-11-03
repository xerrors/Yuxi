"""合并表格转为检索文本时保留字段、条件与原文范围。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.knowledge.chunking.dispatcher import chunk_markdown
from yuxi.modules.knowledge.chunking.ragflow_like.parsers import semantic
from yuxi.modules.knowledge.chunking.ragflow_like.utils.table_utils import html_table_to_key_value


def test_merged_education_is_emitted_once_and_category_is_inherited():
    """横向合并不重复，纵向类别与子条件在每个数据行中齐全。"""
    html = """<table>
<tr><td>等级</td><td colspan="3">学历</td><td>资历</td><td>方式</td></tr>
<tr><td rowspan="3">助理级</td><td colspan="3">本科</td><td>见习满一年</td><td>确认</td></tr>
<tr><td rowspan="2">高技能人才</td><td colspan="2">技师班</td><td>工作满两年</td><td>评审</td></tr>
<tr><td colspan="2">高级工</td><td>工作满三年</td><td>评审</td></tr>
</table>"""

    assert html_table_to_key_value(html) == [
        "等级：助理级；学历：本科；资历：见习满一年；方式：确认；",
        "等级：助理级；学历：高技能人才 / 技师班；资历：工作满两年；方式：评审；",
        "等级：助理级；学历：高技能人才 / 高级工；资历：工作满三年；方式：评审；",
    ]


def test_cross_field_explanation_is_not_assigned_to_education_or_experience():
    """跨逻辑字段的说明只保留一次，并继承适用类别。"""
    html = """<table>
<tr><td>等级</td><td colspan="2">学历</td><td>资历</td><td>方式</td></tr>
<tr><td rowspan="2">中级</td><td colspan="2">硕士</td><td>工作满两年</td><td>评审</td></tr>
<tr><td colspan="4">申报说明：另有规定的按规定执行。</td></tr>
</table>"""

    assert html_table_to_key_value(html) == [
        "等级：中级；学历：硕士；资历：工作满两年；方式：评审；",
        "等级：中级；申报说明：另有规定的按规定执行。；",
    ]


def test_equal_text_in_independent_columns_is_not_deduplicated():
    """相同文字所在的独立字段都需要保留。"""
    html = "<table><tr><th>学历审核</th><th>资历审核</th></tr><tr><td>合格</td><td>合格</td></tr></table>"

    assert html_table_to_key_value(html) == ["学历审核：合格；资历审核：合格；"]


def test_explicit_multiple_header_rows_keep_their_column_paths():
    """显式多层表头不能被误当作数据或丢掉子字段名称。"""
    html = """<table><thead>
<tr><th rowspan="2">等级</th><th colspan="2">条件</th></tr>
<tr><th>学历</th><th>资历</th></tr></thead><tbody>
<tr><td>中级</td><td>本科</td><td>四年</td></tr></tbody></table>"""

    assert html_table_to_key_value(html) == ["等级：中级；条件 / 学历：本科；条件 / 资历：四年；"]


@pytest.mark.parametrize("explicit_thead", [False, True])
def test_body_header_cells_are_data_instead_of_extra_column_headers(explicit_thead):
    """表体中的 th 行不能被误当作多层表头而吞掉条件。"""
    header = "<tr><th>学历</th><th>资历</th></tr>"
    if explicit_thead:
        header = f"<thead>{header}</thead>"
    html = f'<table>{header}<tbody><tr><th scope="row">本科</th><th>四年</th></tr><tr><td>专科</td><td>六年</td></tr></tbody></table>'

    assert html_table_to_key_value(html) == ["学历：本科；资历：四年；", "学历：专科；资历：六年；"]


def test_blank_header_does_not_discard_its_cell():
    """无名称的列仍保留正文，不猜测业务字段名。"""
    html = "<table><tr><th>类别</th><th></th></tr><tr><td>甲类</td><td>补充要求</td></tr></table>"

    assert html_table_to_key_value(html) == ["类别：甲类；补充要求；"]


@pytest.mark.parametrize("token_limit", [1, 5000])
def test_semantic_keeps_rows_atomic_with_context_even_above_length_target(token_limit, monkeypatch):
    """小长度目标不能把行中的类别、学历和资历拆散。"""
    source = """# 资格条件

<table>
<tr><td>等级</td><td>学历</td><td>资历</td></tr>
<tr><td rowspan="2">助理级</td><td>本科</td><td>工作满一年。考察合格。</td></tr>
<tr><td>专科</td><td>三年</td></tr>
</table>"""

    def no_embedding(_):
        """表格行无需通过聚类改写条件。"""
        pytest.fail("完整表格行不能交给语义聚类拆散")

    monkeypatch.setattr("yuxi.modules.models.embed.select_embedding_model", lambda _: SimpleNamespace(encode=no_embedding))
    records = chunk_markdown(
        source,
        "file",
        "table.md",
        {
            "chunk_preset_id": "semantic",
            "chunk_parser_config": {"chunk_token_num": token_limit, "embed_model_id": "test-embedding"},
        },
    )

    assert [row["content"].splitlines()[-1] for row in records] == [
        "- 等级：助理级；学历：本科；资历：工作满一年。考察合格。；",
        "- 等级：助理级；学历：专科；资历：三年；",
    ]
    assert [(row["start_line"], row["end_line"]) for row in records] == [(3, 7), (3, 7)]


def test_semantic_keeps_explanation_as_an_independent_chunk():
    """较大预算也不能把完整资格行与跨字段说明揉成一块。"""
    source = """<table><tr><th>学历</th><th>资历</th></tr>
<tr><td>本科</td><td>四年</td></tr>
<tr><td colspan="2">说明：期限按规定计算。</td></tr></table>"""

    chunks = semantic.chunk_markdown(source, {"chunk_token_num": 5000}, embed_fn=lambda _: [])

    assert [chunk.splitlines()[-1] for chunk in chunks] == [
        "- 学历：本科；资历：四年；",
        "- 说明：期限按规定计算。；",
    ]


def test_semantic_preserves_all_tables_and_adjacent_text_in_one_html_block(monkeypatch):
    """一个 HTML token 内的多个表格、容器正文和贴表说明都不丢失。"""
    source = """<div>适用范围：甲类。
<table><tr><th>条件</th></tr><tr><td>本科</td></tr></table>
第一张表的说明。
<table><tr><th>条件</th></tr><tr><td>专科</td></tr></table>
第二张表的说明。</div>"""

    monkeypatch.setattr("yuxi.modules.models.embed.select_embedding_model", lambda _: SimpleNamespace(encode=lambda _: []))
    records = chunk_markdown(
        source,
        "file",
        "table.md",
        {"chunk_preset_id": "semantic", "chunk_parser_config": {"embed_model_id": "test-embedding"}},
    )
    content = "\n".join(record["content"] for record in records)

    for text in ["适用范围：甲类。", "条件：本科", "第一张表的说明。", "条件：专科", "第二张表的说明。"]:
        assert content.count(text) == 1
    assert all((row["start_line"], row["end_line"]) == (1, 5) for row in records)


@pytest.mark.parametrize(
    "table",
    [
        '<table><tr><th>条件</th></tr><tr><td colspan="invalid">本科</td></tr></table>',
        '<table><tr><th>条件</th></tr><tr><td colspan="0">本科</td></tr></table>',
        '<table><tr><th>条件</th></tr><tr><td rowspan="-1">本科</td></tr></table>',
        "<table><tr><th>条件</th></tr><tr><td>本科<table><tr><td>附加要求</td></tr></table></td></tr></table>",
    ],
)
def test_unsupported_table_preserves_original_html_and_surrounding_text(table):
    """无法可靠转换的结构保留原表，不制造残缺 KV 或吞掉其他正文。"""
    source = table + "\n贴表说明不能丢失。"

    chunks = semantic.chunk_markdown(source, embed_fn=lambda _: [])
    content = "\n".join(chunks)

    assert table in content
    assert "贴表说明不能丢失。" in content
    assert "Table KV" not in content


@pytest.mark.parametrize("span", ['colspan="0"', 'rowspan="-1"'])
def test_nonpositive_span_is_rejected_for_raw_html_preservation(span):
    """非正跨度必须显式拒绝转换，不能生成缺单元格的成功 KV。"""
    html = f"<table><tr><th>条件</th></tr><tr><td {span}>本科</td></tr></table>"

    with pytest.raises(ValueError, match="跨度必须为正数"):
        html_table_to_key_value(html)


def test_rowspan_past_table_end_does_not_invent_data_rows():
    """跨度超出真实行数时，不为不存在的原文创建检索行。"""
    html = '<table><tr><th>类别</th><th>条件</th></tr><tr><td rowspan="5">甲类</td><td>本科</td></tr></table>'

    assert html_table_to_key_value(html) == ["类别：甲类；条件：本科；"]


def test_table_caption_is_carried_with_each_row():
    """表格标题属于各行的共同上下文。"""
    html = "<table><caption>人员条件</caption><tr><th>学历</th></tr><tr><td>本科</td></tr><tr><td>专科</td></tr></table>"

    assert html_table_to_key_value(html) == ["人员条件；学历：本科；", "人员条件；学历：专科；"]
