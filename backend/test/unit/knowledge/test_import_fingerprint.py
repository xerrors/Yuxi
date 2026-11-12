"""相同内容指纹只复用解析器一致的文件。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.knowledge.repositories.files import first_matching_fingerprint
from yuxi.modules.knowledge.utils.kb_utils import content_parser_id

pytestmark = pytest.mark.unit


def _record(parser, file_id):
    return SimpleNamespace(file_id=file_id, processing_params={"ocr_engine": parser} if parser else {})


def test_fingerprint_match_requires_the_same_parser():
    """不同解析器是另一次处理请求，不能短路成已有文件。"""

    records = [_record("", "plain"), _record("rapidocr", "ocr")]

    assert content_parser_id({}) == ""
    assert content_parser_id({"ocr_engine": "rapidocr"}) == "rapidocr"
    assert first_matching_fingerprint(records, "").file_id == "plain"
    assert first_matching_fingerprint(records, "rapidocr").file_id == "ocr"
    assert first_matching_fingerprint(records, "paddle") is None
