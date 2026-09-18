from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from yuxi.knowledge.utils import mindmap_utils as mm


def make_kb(**overrides):
    data = {
        "kb_id": "kb_1",
        "name": "知识库",
        "mindmap": {"content": "知识库", "children": [{"content": "tracked.pdf", "children": []}]},
        "mindmap_file_ids": {"tracked": "tracked.pdf"},
        "mindmap_metadata": {},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_file(file_id: str, filename: str, *, kb_id: str = "kb_1"):
    return SimpleNamespace(
        file_id=file_id,
        kb_id=kb_id,
        filename=filename,
        file_type="pdf",
        status="indexed",
        is_folder=False,
        created_at=None,
    )


class FakeKnowledgeBaseRepository:
    def __init__(self, kb):
        self.kb = kb
        self.updates = []

    async def get_by_kb_id(self, kb_id):
        return self.kb if kb_id == self.kb.kb_id else None

    async def update(self, kb_id, data):
        """保存并返回记录，与 repository 契约一致。"""
        self.updates.append((kb_id, data))
        for key, value in data.items():
            setattr(self.kb, key, value)
        return self.kb


@pytest.mark.asyncio
async def test_get_mindmap_diff_keeps_tracked_file_outside_first_page(monkeypatch):
    kb_repo = FakeKnowledgeBaseRepository(make_kb())

    class FakeFileRepository:
        async def search_files(self, **kwargs):
            assert kwargs["limit"] == mm.MINDMAP_FILE_PAGE_SIZE
            return [make_file("new", "new.pdf")], 100

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["tracked"]
            return [make_file("tracked", "tracked.pdf")]

    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        FakeFileRepository,
    )

    result = await mm.get_mindmap_diff("kb_1")

    assert result["removed_file_ids"] == []
    assert result["unchanged_count"] == 1
    assert result["added_files"] == [{"file_id": "new", "filename": "new.pdf", "type": "pdf"}]
    assert result["current_files_truncated"] is True


@pytest.mark.asyncio
async def test_generate_database_mindmap_loads_selected_file_ids_directly(monkeypatch):
    kb_repo = FakeKnowledgeBaseRepository(make_kb(mindmap=None, mindmap_file_ids=None))

    class FakeFileRepository:
        async def list_documents(self, **kwargs):
            raise AssertionError("selected file generation should query by file id")

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["outside-page"]
            return [make_file("outside-page", "outside.pdf")]

    class FakeModel:
        async def call(self, messages, stream):
            assert "outside.pdf" in messages[1]["content"]
            return SimpleNamespace(content='{"content":"知识库","children":[{"content":"outside.pdf","children":[]}]}')

    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        FakeFileRepository,
    )
    monkeypatch.setattr(mm, "select_model", lambda model_spec: FakeModel())

    async def get_system_options(_option, _db=None):
        return {"default_model": "test-provider:test-model"}

    monkeypatch.setattr(type(mm.system_options), "get", get_system_options)

    result = await mm.generate_database_mindmap("kb_1", file_ids=["outside-page"])

    assert result["file_count"] == 1
    assert result["original_file_count"] == 1
    assert kb_repo.updates[0][1]["mindmap_file_ids"] == {"outside-page": "outside.pdf"}


@pytest.mark.asyncio
async def test_generate_database_mindmap_includes_nested_files_when_root_is_empty(monkeypatch):
    kb_repo = FakeKnowledgeBaseRepository(make_kb(mindmap=None, mindmap_file_ids=None))

    class FakeFileRepository:
        async def search_files(self, **kwargs):
            assert kwargs == {
                "kb_id": "kb_1",
                "offset": 0,
                "limit": mm.MINDMAP_GENERATION_FILE_LIMIT,
                "files_only": True,
            }
            return [make_file("nested", "nested.pdf")], 1

    class FakeModel:
        async def call(self, messages, stream):
            assert "nested.pdf" in messages[1]["content"]
            return SimpleNamespace(content='{"content":"知识库","children":[{"content":"nested.pdf","children":[]}]}')

    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        FakeFileRepository,
    )
    monkeypatch.setattr(mm, "select_model", lambda model_spec: FakeModel())

    async def get_system_options(_option, _db=None):
        return {"default_model": "test-provider:test-model"}

    monkeypatch.setattr(type(mm.system_options), "get", get_system_options)

    result = await mm.generate_database_mindmap("kb_1")

    assert result["file_count"] == 1
    assert result["original_file_count"] == 1
    assert kb_repo.updates[0][1]["mindmap_file_ids"] == {"nested": "nested.pdf"}


@pytest.mark.asyncio
async def test_generate_database_mindmap_rejects_missing_selected_files(monkeypatch):
    kb_repo = FakeKnowledgeBaseRepository(make_kb(mindmap=None, mindmap_file_ids=None))

    class FakeFileRepository:
        async def list_by_file_ids(self, file_ids):
            return []

    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        FakeFileRepository,
    )

    with pytest.raises(HTTPException, match="选择的文件不存在"):
        await mm.generate_database_mindmap("kb_1", file_ids=["missing"])


@pytest.mark.parametrize("incremental", [False, True])
@pytest.mark.parametrize("outcome", ["saved", "missing", "error"])
@pytest.mark.asyncio
async def test_mindmap_success_requires_saved_state(monkeypatch, incremental, outcome):
    """全量和增量生成均以保存结果为成功依据，失败保留原状态。"""
    kb = make_kb()
    repository = FakeKnowledgeBaseRepository(kb)
    error = RuntimeError("database unavailable")

    async def update(_kb_id, data):
        """模拟持久化结果并允许成功后回读。"""
        if outcome == "error":
            raise error
        if outcome == "missing":
            return None
        for key, value in data.items():
            setattr(kb, key, value)
        return kb

    monkeypatch.setattr(repository, "update", update)
    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: repository)
    monkeypatch.setattr(mm, "_load_mindmap_current_files", AsyncMock(return_value=({}, 0)))
    files = {"new": {"filename": "new.pdf", "file_type": "pdf"}}
    file_repository = SimpleNamespace(search_files=AsyncMock(return_value=([make_file("new", "new.pdf")], 1)))
    monkeypatch.setattr("yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository", lambda: file_repository)
    monkeypatch.setattr(
        mm, "system_options", SimpleNamespace(get=AsyncMock(return_value={"default_model": "test:model"}))
    )
    generated = '{"content":"知识库","children":[{"content":"new.pdf","children":[]}]}'
    model = SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(content=generated)))
    monkeypatch.setattr(mm, "select_model", lambda **_: model)
    operation = mm.update_mindmap_incremental if incremental else mm.generate_database_mindmap

    if outcome == "saved":
        result = await operation("kb_1")
        stored = await repository.get_by_kb_id("kb_1")
        assert result["mindmap"] == stored.mindmap
        assert stored.mindmap_file_ids == ({} if incremental else {"new": files["new"]["filename"]})
    else:
        with pytest.raises(HTTPException if outcome == "missing" else RuntimeError) as caught:
            await operation("kb_1")
        assert kb.mindmap_file_ids == {"tracked": "tracked.pdf"}
        if outcome == "missing":
            assert caught.value.status_code == 404
        else:
            assert caught.value is error


def test_rename_files_in_mindmap_renames_only_the_leaf_and_keeps_structure():
    """重命名是纯树手术：只替换叶子文件名，分类结构原样保留。"""
    mindmap = {
        "content": "知识库",
        "children": [
            {
                "content": "分类A",
                "children": [
                    {"content": "old.pdf", "children": []},
                    {"content": "keep.pdf", "children": []},
                ],
            }
        ],
    }

    renamed = mm.rename_files_in_mindmap(mindmap, {"old.pdf": "new.pdf"})

    assert renamed["content"] == "知识库"
    assert renamed["children"][0]["content"] == "分类A"
    assert [leaf["content"] for leaf in renamed["children"][0]["children"]] == ["new.pdf", "keep.pdf"]
    # 深拷贝：不能改动传入的原始结构
    assert mindmap["children"][0]["children"][0]["content"] == "old.pdf"


def test_rename_files_in_mindmap_without_matching_leaf_is_noop():
    mindmap = {"content": "知识库", "children": [{"content": "keep.pdf", "children": []}]}

    assert mm.rename_files_in_mindmap(mindmap, {"missing.pdf": "new.pdf"}) == mindmap


@pytest.mark.asyncio
async def test_rename_file_in_mindmap_updates_tree_and_tracking(monkeypatch):
    """叶子名与追踪表必须一起更新，否则界面会继续显示旧名或再次报「已变更」。"""
    kb = make_kb(
        mindmap={
            "content": "知识库",
            "children": [{"content": "分类A", "children": [{"content": "old.pdf", "children": []}]}],
        },
        mindmap_file_ids={"f1": "old.pdf"},
    )
    kb_repo = FakeKnowledgeBaseRepository(kb)
    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)

    assert await mm.rename_file_in_mindmap("kb_1", "f1", "new.pdf") is True

    assert kb.mindmap["children"][0]["children"][0]["content"] == "new.pdf"
    assert kb.mindmap_file_ids == {"f1": "new.pdf"}


@pytest.mark.asyncio
async def test_rename_file_in_mindmap_skips_when_not_tracked_or_unchanged(monkeypatch):
    """文件不在导图里、或新名与旧名相同，都不应产生写入。"""
    kb_repo = FakeKnowledgeBaseRepository(make_kb())
    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)

    await mm.rename_file_in_mindmap("kb_1", "not_tracked", "new.pdf")
    await mm.rename_file_in_mindmap("kb_1", "tracked", "tracked.pdf")

    assert kb_repo.updates == []


@pytest.mark.asyncio
async def test_rename_file_in_mindmap_skips_ambiguous_same_name_leaves(monkeypatch):
    """同名文件会让按名字替换的树手术改错叶子，且几乎没有修复手段，故 fail-closed。

    库里允许同名（上传只提示不阻断），此时「把 dup.pdf 改成 new.pdf」无法判断该改哪个
    叶子；若两个叶子一起改，另一个名字还叫 dup.pdf 的文件就被写上了错名。更麻烦的是
    mindmap_file_ids 只记 id 集合，detect_mindmap_changes 永远报「无变更」，界面不会提示，
    而正确名的叶子已经不存在，改回来的路也断了。宁可不更新。
    """
    kb = make_kb(
        mindmap={
            "content": "知识库",
            "children": [
                {
                    "content": "分类A",
                    "children": [
                        {"content": "dup.pdf", "children": []},
                        {"content": "dup.pdf", "children": []},
                    ],
                }
            ],
        },
        mindmap_file_ids={"f1": "dup.pdf", "f2": "dup.pdf"},
    )
    kb_repo = FakeKnowledgeBaseRepository(kb)
    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)

    assert await mm.rename_file_in_mindmap("kb_1", "f1", "new.pdf") is False

    assert kb_repo.updates == []
    assert [leaf["content"] for leaf in kb.mindmap["children"][0]["children"]] == ["dup.pdf", "dup.pdf"]
    assert kb.mindmap_file_ids == {"f1": "dup.pdf", "f2": "dup.pdf"}


@pytest.mark.asyncio
async def test_rename_file_in_mindmap_renames_the_only_leaf_even_when_old_name_is_shared(monkeypatch):
    """旧名同时挂在多个被跟踪文件上、但树里只有一个同名叶子时，照常改名。

    这是库里真实存在的状态（13 组「原稿 + zh-CN 译文」同名文件共用一个叶子）：那个叶子
    本来就不区分两个同名文件，改它不会覆盖任何别的文件的独立叶子；若一并跳过，导图同步
    在真实数据上几乎全部失效，而用户只看到「重命名成功」。
    """
    kb = make_kb(
        mindmap={
            "content": "知识库",
            "children": [{"content": "分类A", "children": [{"content": "dup.pdf", "children": []}]}],
        },
        mindmap_file_ids={"f1": "dup.pdf", "f2": "dup.pdf"},
    )
    kb_repo = FakeKnowledgeBaseRepository(kb)
    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)

    synced = await mm.rename_file_in_mindmap("kb_1", "f1", "new.pdf")

    assert synced is True
    assert kb.mindmap["children"][0]["children"][0]["content"] == "new.pdf"
    assert kb.mindmap_file_ids == {"f1": "new.pdf", "f2": "dup.pdf"}


@pytest.mark.asyncio
async def test_rename_file_in_mindmap_reports_skipped_sync(monkeypatch):
    """跳过同步必须返回 False，调用方才有机会告诉用户导图其实是旧的。"""
    kb = make_kb(
        mindmap={
            "content": "知识库",
            "children": [
                {
                    "content": "分类A",
                    "children": [
                        {"content": "dup.pdf", "children": []},
                        {"content": "dup.pdf", "children": []},
                    ],
                }
            ],
        },
        mindmap_file_ids={"f1": "dup.pdf"},
    )
    kb_repo = FakeKnowledgeBaseRepository(kb)
    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)

    assert await mm.rename_file_in_mindmap("kb_1", "f1", "new.pdf") is False


@pytest.mark.asyncio
async def test_rename_file_in_mindmap_load_failure_is_not_raised(monkeypatch):
    """读取失败不能冒成 500：重命名已经落库生效，报错会让用户以为没改成功。"""
    kb = make_kb()
    kb_repo = FakeKnowledgeBaseRepository(kb)

    async def failing_get(kb_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(kb_repo, "get_by_kb_id", failing_get)
    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)

    assert await mm.rename_file_in_mindmap("kb_1", "tracked", "new.pdf") is False

    assert kb_repo.updates == []
