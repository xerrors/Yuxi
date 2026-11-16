"""从当前 Markdown 提取图片位置，不保存第二份正文。"""

from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from yuxi.infrastructure.document_parsing.artifacts import resource_path


def document_images(content: str, *, kb_id: str, file_id: str) -> list[dict]:
    """从 Markdown 与内联 HTML 读取本文件图片及原文行号。"""
    images = []
    for token in MarkdownIt("commonmark").enable("table").parse(content):
        if token.type == "inline" and token.map:
            line = token.map[0] + 1
            for child in token.children or []:
                if child.type in {"softbreak", "hardbreak"}:
                    line += 1
                elif child.type == "image":
                    images.append(_image_position(str(child.attrGet("src") or ""), line, child.content, kb_id, file_id))
                elif child.type == "html_inline":
                    images.extend(_html_images(child.content, line, kb_id, file_id))
        elif token.type == "html_block" and token.map:
            images.extend(_html_images(token.content, token.map[0] + 1, kb_id, file_id))

    return images


def _image_position(reference: str, line: int, alt: str, kb_id: str, file_id: str) -> dict:
    """图片仅以本文件代理引用解析为可读取资源身份。"""
    parsed = urlsplit(reference)
    prefix = f"/api/knowledge/knowledge-bases/{kb_id}/images/"
    asset_id = None
    if not parsed.scheme and not parsed.netloc and not parsed.query and not parsed.fragment and parsed.path.startswith(prefix):
        candidate = unquote(parsed.path[len(prefix) :])
        try:
            candidate = resource_path(candidate)
        except ValueError:
            pass
        else:
            if candidate.startswith(f"kb-images/{file_id}/"):
                asset_id = candidate
    return {
        "asset_id": asset_id,
        "reference": reference,
        "line": line,
        "alt": alt,
        "readable": asset_id is not None,
        "reason": None if asset_id is not None else "not_document_asset",
    }


def _html_images(content: str, line: int, kb_id: str, file_id: str) -> list[dict]:
    """保留解析正文中 HTML 图片的真实行号。"""

    class ImageParser(HTMLParser):
        """只收集 img 属性，不解释或执行 HTML。"""

        def handle_starttag(self, tag, attrs):
            """把 HTML 中的资源引用投影为同一图片契约。"""
            if tag.lower() == "img":
                values = dict(attrs)
                images.append(
                    _image_position(values.get("src") or "", line + self.getpos()[0] - 1, values.get("alt") or "", kb_id, file_id)
                )

    images = []
    ImageParser().feed(content)
    return images
