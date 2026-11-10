"""图谱清除接受的文件状态。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.knowledge.repositories.graphs import KnowledgeGraphRepository
from yuxi.shared.errors import InvalidOperationError, StateConflictError


@pytest.mark.parametrize("status", ["parsed", "indexed", "error_indexing", "done", "failed"])
def test_stable_and_legacy_failed_files_can_clear_graph(status):
    KnowledgeGraphRepository.require_clearable_file(SimpleNamespace(is_folder=False, status=status))


@pytest.mark.parametrize("status", ["uploaded", "parsing", "indexing", "processing", "waiting"])
def test_in_progress_files_cannot_clear_graph(status):
    with pytest.raises(StateConflictError, match="不能清除图谱"):
        KnowledgeGraphRepository.require_clearable_file(SimpleNamespace(is_folder=False, status=status))


def test_folder_cannot_clear_graph():
    with pytest.raises(InvalidOperationError, match="目录不能清除图谱"):
        KnowledgeGraphRepository.require_clearable_file(SimpleNamespace(is_folder=True, status="done"))
