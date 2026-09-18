"""KnowledgeBase.rename_file 的行为断言。

重命名只改展示名。这里守的是「哪些请求该被拒绝」以及「哪些字段会被写」——
写错字段（比如顺手改了 path，而 path 对文件来说是存储路径）会破坏下载与解析链路。
"""

import types
from unittest.mock import AsyncMock

import pytest

from yuxi.knowledge.base import KnowledgeBase


class FakeKnowledgeBase(KnowledgeBase):
    """最小可实例化的 executor，用于验证基类里的通用流程。"""

    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, kb_id: str, embedding_model_spec: str | None):
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        pass

    async def index_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        return {}

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict]:
        return []

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        return {"options": []}

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        pass

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        return {}


class FakeFileRepository:
    """记录写入的假仓库。"""

    def __init__(self, *, update_result=None):
        self.update_result = update_result
        self.update_calls = []

    async def update_fields(self, **kwargs):
        self.update_calls.append(kwargs)
        return self.update_result


def make_meta(**overrides):
    meta = {
        "file_id": "file_1",
        "kb_id": "kb_1",
        "filename": "a.pdf",
        "original_filename": None,
        "is_folder": False,
        "status": "indexed",
        "path": "http://minio/upload/a.pdf",
    }
    meta.update(overrides)
    return meta


def make_record(meta):
    """伪造 ORM 记录：_file_record_to_meta 会读取全部这些属性。"""
    return types.SimpleNamespace(
        file_id=meta["file_id"],
        kb_id=meta["kb_id"],
        parent_id=meta.get("parent_id"),
        filename=meta.get("filename"),
        original_filename=meta.get("original_filename"),
        file_type=meta.get("file_type"),
        path=meta.get("path"),
        minio_url=meta.get("minio_url"),
        markdown_file=meta.get("markdown_file"),
        status=meta.get("status"),
        content_hash=None,
        file_size=None,
        chunk_count=0,
        token_count=0,
        content_type=None,
        processing_params=None,
        is_folder=meta.get("is_folder", False),
        error_message=None,
        created_by=None,
        updated_by=None,
        created_at=None,
        updated_at=None,
    )


@pytest.fixture
def kb(tmp_path):
    return FakeKnowledgeBase(str(tmp_path))


def patch_meta(monkeypatch, kb, meta):
    monkeypatch.setattr(kb, "_load_file_meta", AsyncMock(return_value=meta))


def patch_repo(monkeypatch, repo):
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda *a, **kw: repo,
    )


@pytest.mark.asyncio
async def test_重命名写入新名并保留上传原名(monkeypatch, kb):
    meta = make_meta(filename="MinerU_markdown_2068337809883488256.md")
    patch_meta(monkeypatch, kb, meta)
    repo = FakeFileRepository(
        update_result=make_record({**meta, "filename": "涡轮叶片气膜冷却技术分析.md"})
    )
    patch_repo(monkeypatch, repo)

    result = await kb.rename_file("kb_1", "file_1", "涡轮叶片气膜冷却技术分析.md", operator_id="u1")

    assert len(repo.update_calls) == 1
    data = repo.update_calls[0]["data"]
    assert data["filename"] == "涡轮叶片气膜冷却技术分析.md"
    assert data["original_filename"] == "MinerU_markdown_2068337809883488256.md"
    assert data["updated_by"] == "u1"
    # path 对文件而言是存储路径，改名绝不能动它，否则下载与解析链路会断
    assert "path" not in data
    assert result["filename"] == "涡轮叶片气膜冷却技术分析.md"


@pytest.mark.asyncio
async def test_已有原名时不被覆盖(monkeypatch, kb):
    meta = make_meta(original_filename="原始上传名.pdf", filename="改过一次.pdf")
    patch_meta(monkeypatch, kb, meta)
    repo = FakeFileRepository(update_result=make_record({**meta, "filename": "再改一次.pdf"}))
    patch_repo(monkeypatch, repo)

    await kb.rename_file("kb_1", "file_1", "再改一次.pdf")

    assert "original_filename" not in repo.update_calls[0]["data"]


@pytest.mark.asyncio
async def test_新旧同名时不产生写入(monkeypatch, kb):
    meta = make_meta(filename="a.pdf")
    patch_meta(monkeypatch, kb, meta)
    repo = FakeFileRepository()
    patch_repo(monkeypatch, repo)

    result = await kb.rename_file("kb_1", "file_1", "  a.pdf  ")

    assert repo.update_calls == []
    assert result["filename"] == "a.pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current_name", "new_name"),
    [
        # "..pdf" 这类名字是上传可达的：收录判定用 pathlib（suffix=".pdf"）会放行，
        # 而重命名守卫用 splitext（ext=""）。只守一种判据就会让「扩展名已锁死」失守：
        # 从 "..pdf" 改成 "report" 时 splitext 侧看着没变，pathlib 侧却从 .pdf 变成无后缀。
        ("..pdf", "report"),
        ("..pdf", "x.pdf"),
        ("a.pdf", "..pdf"),
        ("x.", "x"),
        ("x.", "a.pdf"),
    ],
)
async def test_两种后缀判据任一变化都被拒绝(monkeypatch, kb, current_name, new_name):
    patch_meta(monkeypatch, kb, make_meta(filename=current_name))
    repo = FakeFileRepository()
    patch_repo(monkeypatch, repo)

    with pytest.raises(ValueError, match="扩展名"):
        await kb.rename_file("kb_1", "file_1", new_name)

    assert repo.update_calls == []


@pytest.mark.asyncio
async def test_与已有文件同名时允许改名(monkeypatch, kb):
    """同名不拦：上传链路本就允许同名（rename_folder 也不查），

    这里设卡会让「改回原名」在已有同名的库里被永久拒绝。
    """
    meta = make_meta(filename="a-重命名验证.pdf")
    patch_meta(monkeypatch, kb, meta)
    repo = FakeFileRepository(update_result=make_record({**meta, "filename": "a.pdf"}))
    patch_repo(monkeypatch, repo)

    result = await kb.rename_file("kb_1", "file_1", "a.pdf")

    assert repo.update_calls[0]["data"]["filename"] == "a.pdf"
    assert result["filename"] == "a.pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "reason"),
    [
        ("b.txt", "扩展名"),
        ("b", "扩展名"),
        ("", "不能为空"),
        ("   ", "不能为空"),
        ("dir/b.pdf", "路径分隔符"),
        ("dir\\b.pdf", "路径分隔符"),
        # splitext 把它们都判成「无后缀」，光靠扩展名比较拦不住
        (".", "点组成"),
        ("..", "点组成"),
        ("...", "点组成"),
        # varchar(512) 写不下；上传链路截断到 500，这里按同一上限显式拒绝
        ("a" * 501, "500 个字符"),
    ],
)
async def test_非法文件名被拒绝且不写库(monkeypatch, kb, filename, reason):
    patch_meta(monkeypatch, kb, make_meta())
    repo = FakeFileRepository()
    patch_repo(monkeypatch, repo)

    with pytest.raises(ValueError, match=reason):
        await kb.rename_file("kb_1", "file_1", filename)

    assert repo.update_calls == []


@pytest.mark.asyncio
async def test_刚好到长度上限的名字可用(monkeypatch, kb):
    """边界值本身必须能过：496 + ".pdf" 正好 500 字符。"""
    meta = make_meta(filename="a.pdf")
    patch_meta(monkeypatch, kb, meta)
    boundary = "a" * 496 + ".pdf"
    assert len(boundary) == 500
    repo = FakeFileRepository(update_result=make_record({**meta, "filename": boundary}))
    patch_repo(monkeypatch, repo)

    result = await kb.rename_file("kb_1", "file_1", boundary)

    assert repo.update_calls[0]["data"]["filename"] == boundary
    assert result["filename"] == boundary


@pytest.mark.asyncio
async def test_文件夹走专属接口(monkeypatch, kb):
    patch_meta(monkeypatch, kb, make_meta(is_folder=True))
    repo = FakeFileRepository()
    patch_repo(monkeypatch, repo)

    with pytest.raises(ValueError, match="文件夹"):
        await kb.rename_file("kb_1", "file_1", "b.pdf")

    assert repo.update_calls == []


@pytest.mark.asyncio
async def test_扩展名大小写不同视为合法(monkeypatch, kb):
    """PDF 与 pdf 是同一个扩展名，不应因为大小写被拒。"""
    meta = make_meta(filename="a.PDF")
    patch_meta(monkeypatch, kb, meta)
    repo = FakeFileRepository(update_result=make_record({**meta, "filename": "b.pdf"}))
    patch_repo(monkeypatch, repo)

    await kb.rename_file("kb_1", "file_1", "b.pdf")

    assert repo.update_calls[0]["data"]["filename"] == "b.pdf"
