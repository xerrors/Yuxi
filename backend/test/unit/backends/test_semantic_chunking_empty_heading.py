from types import SimpleNamespace

from yuxi.knowledge.chunking.ragflow_like.parsers import semantic


def test_empty_heading_preserves_current_title_context():
    markdown_content = """
## Parent Section

### Child Section

Content before empty heading.

###

Content after empty heading.
"""

    chunks = semantic.chunk_markdown(
        markdown_content,
        parser_config={"chunk_token_num": 512},
        embed_fn=lambda texts: texts,
    )

    assert len(chunks) == 1
    assert chunks[0].splitlines()[0] == "### Parent Section|Child Section"
    assert "Content before empty heading." in chunks[0]
    assert "Content after empty heading." in chunks[0]


def test_truncated_heading_token_stream_is_ignored(monkeypatch):
    markdown_parser = semantic.MarkdownIt("commonmark")
    monkeypatch.setattr(markdown_parser, "parse", lambda _: [SimpleNamespace(type="heading_open")])
    monkeypatch.setattr(semantic, "MarkdownIt", lambda _: markdown_parser)

    assert semantic.chunk_markdown("#", embed_fn=lambda texts: texts) == []
