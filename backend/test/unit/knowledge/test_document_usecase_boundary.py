"""元数据读取不依赖解析配置和外部检索执行器。"""

from unittest.mock import AsyncMock

import pytest

from yuxi.modules.knowledge.manager import KnowledgeBaseManager
from yuxi.modules.knowledge.models import KnowledgeFile
from yuxi.modules.knowledge.services import documents as knowledge_documents

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_file_metadata_read_does_not_load_execution_configuration(monkeypatch, tmp_path):
    """损坏的运行配置不能阻止读取已经持久化的文件事实。"""
    record = KnowledgeFile(file_id="file-a", kb_id="kb-a", filename="report.md", status="uploaded", is_folder=False)
    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.files.KnowledgeFileRepository.get_by_file_id",
        AsyncMock(return_value=record),
    )
    monkeypatch.setattr(KnowledgeBaseManager, "get_kb_config", AsyncMock(side_effect=ValueError("invalid execution configuration")))
    result = await knowledge_documents.get_file_basic_info("kb-a", "file-a")
    assert result["meta"]["filename"] == "report.md"
    assert result["meta"]["status"] == "uploaded"
    with pytest.raises(ValueError, match="not found"):
        await knowledge_documents.get_file_basic_info("kb-other", "file-a")


@pytest.mark.parametrize("kb_type", ["milvus", "dify", "notion", "feishu"])
async def test_query_configuration_needs_no_executor(kb_type, monkeypatch, tmp_path):
    """每种查询配置不构造执行器，模型选项只读取已装配的目录缓存。"""
    from yuxi.modules.knowledge.implementations.dify import DifyKB
    from yuxi.modules.knowledge.implementations.feishu import FeishuKB
    from yuxi.modules.knowledge.implementations.milvus import MilvusKB
    from yuxi.modules.knowledge.implementations.notion import NotionKB
    from yuxi.modules.models.providers.cache import model_cache

    monkeypatch.setattr(model_cache, "get_all_specs", lambda _kind: [])
    kb_class = {"milvus": MilvusKB, "dify": DifyKB, "notion": NotionKB, "feishu": FeishuKB}[kb_type]
    monkeypatch.setattr(kb_class, "__init__", lambda *_args, **_kwargs: pytest.fail("configuration constructed an executor"))
    config = kb_class.get_query_params_config()
    assert config["type"] == kb_type
    assert kb_class.get_default_query_params()["options"] == {
        option["key"]: option["default"] for option in config["options"] if "default" in option
    }


async def test_local_document_window_does_not_load_execution_configuration(monkeypatch, tmp_path):
    """本地正文阅读只依赖当前文件归属和对象内容。"""
    from types import SimpleNamespace

    from yuxi.modules.knowledge.services import documents

    manager = KnowledgeBaseManager(str(tmp_path))
    monkeypatch.setattr(manager, "get_kb_config", AsyncMock(side_effect=ValueError("invalid parser configuration")))
    monkeypatch.setattr(manager, "_get_or_create_kb_instance", AsyncMock(side_effect=RuntimeError("Milvus unavailable")))
    monkeypatch.setattr(knowledge_documents, "get_knowledge_base_document_support", AsyncMock(return_value=(SimpleNamespace(kb_type="milvus"), True)))
    record = KnowledgeFile(file_id="file", kb_id="kb", filename="source.md", status="parsed", markdown_file="minio://parsed/source.md")
    monkeypatch.setattr("yuxi.modules.knowledge.repositories.files.KnowledgeFileRepository.get_by_file_id", AsyncMock(return_value=record))
    monkeypatch.setattr(documents, "read_minio_bytes", AsyncMock(return_value=b"first\nsecond\nthird"))
    result = await manager.open_document("kb", "file", offset=1, limit=1)
    assert result["content"] == "     2\tsecond"
    assert result["kb_id"] == "kb" and result["file_id"] == "file"


async def test_management_services_load_types_without_runtime_or_parser():
    """冷导入服务拥有完整类型信息，不依赖其他测试先装配单例。"""
    import subprocess
    import sys

    script = """
import sys
from yuxi.modules.knowledge.services import bases, documents
from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
assert list(KnowledgeBaseFactory.get_available_types()) == ["milvus", "feishu", "dify", "notion"]
assert KnowledgeBaseFactory.supports_documents("milvus")
assert not KnowledgeBaseFactory.supports_documents("dify")
assert "yuxi.modules.knowledge.runtime" not in sys.modules
assert not any(name.startswith("yuxi.infrastructure.document_parsing") for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
