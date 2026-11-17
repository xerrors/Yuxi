"""图谱请求用例保留裁决顺序、唯一登记与执行阶段边界。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.modules.knowledge.services import graph_build
from yuxi.shared.errors import InvalidOperationError, ResourceNotFoundError, StateConflictError

pytestmark = pytest.mark.asyncio


@pytest.fixture
def graph_boundary(monkeypatch):
    """只替代真实 I/O，保留提交用例和领域错误。"""
    jobs = SimpleNamespace(find_job_by_payload=AsyncMock(return_value=None))
    kb = SimpleNamespace(get_knowledge_base_info=AsyncMock(return_value=SimpleNamespace(name="知识库")))
    execution = SimpleNamespace(get_status=AsyncMock(return_value={"locked": True}), reset=AsyncMock(return_value={"status": "success"}))
    submit = AsyncMock(return_value=(SimpleNamespace(id="job"), True))
    monkeypatch.setattr(graph_build, "job_tracker", jobs)
    monkeypatch.setattr(graph_build, "knowledge_bases", kb)
    monkeypatch.setattr(graph_build, "KnowledgeGraphService", lambda: execution)
    monkeypatch.setattr(graph_build, "submit_knowledge_job", submit)
    return jobs, kb, execution, submit


@pytest.mark.parametrize("action", ["build", "reconcile", "reset"])
async def test_active_job_rejects_before_graph_or_submission(graph_boundary, action):
    """活动任务优先拒绝，不能先改配置、存储或登记第二个任务。"""
    jobs, kb, execution, submit = graph_boundary
    jobs.find_job_by_payload.return_value = SimpleNamespace(status="pending")
    with pytest.raises(StateConflictError, match="正在运行"):
        if action == "build":
            await graph_build.start_graph_build("kb")
        elif action == "reconcile":
            await graph_build.reconcile_graph_build("kb", mode="all_vectors")
        else:
            await graph_build.reset_graph_build("kb", clear_extraction_result=True, clear_config=False)
    kb.get_knowledge_base_info.assert_not_awaited()
    execution.get_status.assert_not_awaited()
    execution.reset.assert_not_awaited()
    submit.assert_not_awaited()


async def test_mode_validation_precedes_active_job_check(graph_boundary):
    """非法修复范围保留最高错误优先级，不访问持久任务。"""
    jobs, _, _, submit = graph_boundary
    with pytest.raises(InvalidOperationError, match="mode 必须"):
        await graph_build.reconcile_graph_build("kb", mode="invalid")
    jobs.find_job_by_payload.assert_not_awaited()
    submit.assert_not_awaited()


async def test_missing_kb_and_unlocked_configuration_reject_before_submission(graph_boundary):
    """不存在和未锁配置分别返回原错误，不创建任务。"""
    _, kb, execution, submit = graph_boundary
    kb.get_knowledge_base_info.return_value = None
    with pytest.raises(ResourceNotFoundError, match="知识库 kb 不存在"):
        await graph_build.start_graph_build("kb")
    execution.get_status.assert_not_awaited()
    kb.get_knowledge_base_info.return_value = SimpleNamespace(name="知识库")
    execution.get_status.return_value = {"locked": False}
    with pytest.raises(InvalidOperationError, match="请先确认"):
        await graph_build.start_graph_build("kb")
    submit.assert_not_awaited()


@pytest.mark.parametrize("action", ["build", "reconcile"])
async def test_registration_race_still_rejects_without_executing(graph_boundary, action):
    """预检通过后仍以唯一登记结果裁决，不在请求内执行图谱。"""
    _, _, execution, submit = graph_boundary
    submit.return_value = (SimpleNamespace(id="existing"), False)
    with pytest.raises(StateConflictError, match="已有正在运行"):
        if action == "build":
            await graph_build.start_graph_build("kb")
        else:
            await graph_build.reconcile_graph_build("kb", mode="all_vectors")
    assert submit.await_args.kwargs["payload_match"] == {"kb_id": "kb"}
    assert submit.await_args.kwargs["payload"] == (
        {"kb_id": "kb", "action": "build"} if action == "build" else {"kb_id": "kb", "action": "reconcile", "reconcile_mode": "all_vectors"}
    )
    execution.reset.assert_not_awaited()


@pytest.mark.parametrize(
    "file, chunk_count, error",
    [
        (None, 1, ResourceNotFoundError),
        (SimpleNamespace(kb_id="other", is_folder=False), 1, ResourceNotFoundError),
        (SimpleNamespace(kb_id="kb", is_folder=True), 1, InvalidOperationError),
        (SimpleNamespace(kb_id="kb", is_folder=False, active_generation=2), 0, InvalidOperationError),
    ],
)
async def test_file_build_rejects_invalid_target(graph_boundary, monkeypatch, file, chunk_count, error):
    """跨库、目录和没有当前分块的文件不得登记任务。"""
    files = SimpleNamespace(get_by_file_id=AsyncMock(return_value=file))
    chunks = SimpleNamespace(count_by_file_id=AsyncMock(return_value=chunk_count))
    monkeypatch.setattr(graph_build, "KnowledgeFileRepository", lambda: files)
    monkeypatch.setattr(graph_build, "KnowledgeChunkRepository", lambda: chunks)
    with pytest.raises(error):
        await graph_build.start_graph_build("kb", file_id="file")
    graph_boundary[3].assert_not_awaited()


async def test_file_build_records_generation_and_keeps_kb_mutex(graph_boundary, monkeypatch):
    """作业持久化范围，互斥仍以整个知识库为单位。"""
    files = SimpleNamespace(get_by_file_id=AsyncMock(return_value=SimpleNamespace(kb_id="kb", is_folder=False, active_generation=3)))
    chunks = SimpleNamespace(count_by_file_id=AsyncMock(return_value=2))
    monkeypatch.setattr(graph_build, "KnowledgeFileRepository", lambda: files)
    monkeypatch.setattr(graph_build, "KnowledgeChunkRepository", lambda: chunks)
    await graph_build.start_graph_build("kb", file_id="file")
    kwargs = graph_boundary[3].await_args.kwargs
    assert kwargs["payload"] == {"kb_id": "kb", "action": "build", "file_id": "file", "generation": 3}
    assert kwargs["payload_match"] == {"kb_id": "kb"}


async def test_clear_rejects_pending_build_and_registration_race(graph_boundary, monkeypatch):
    """旧构建的排队意图与清理互斥，预检后的并发登记仍由持久唯一键拒绝。"""
    jobs, kb, _, submit = graph_boundary
    jobs.find_job_by_payload.return_value = SimpleNamespace(status="pending")
    with pytest.raises(StateConflictError):
        await graph_build.clear_file_graph("kb", "file")
    submit.assert_not_awaited()
    jobs.find_job_by_payload.return_value = None
    kb.get_knowledge_base_info.return_value = SimpleNamespace(name="KB", kb_type="milvus")
    file = SimpleNamespace(kb_id="kb", is_folder=False, status="indexed", filename="file", active_generation=2)
    monkeypatch.setattr(graph_build, "KnowledgeFileRepository", lambda: SimpleNamespace(get_by_file_id=AsyncMock(return_value=file)))
    submit.return_value = (SimpleNamespace(id="existing"), False)
    with pytest.raises(StateConflictError):
        await graph_build.clear_file_graph("kb", "file")
    assert submit.await_args.kwargs["payload"] == {"kb_id": "kb", "action": "clear_file", "file_id": "file", "generation": 2}
    assert submit.await_args.kwargs["payload_match"] == {"kb_id": "kb"}


@pytest.mark.parametrize(
    "scope",
    [
        {},
        {"file_id": "file"},
        {"file_id": "", "generation": 1},
        {"file_id": "file", "generation": True},
        {"file_id": "file", "generation": "1"},
        {"file_id": "file", "generation": 0},
        {"file_id": "file", "generation": -1},
    ],
)
async def test_clear_worker_rejects_malformed_scope(monkeypatch, scope):
    """不可信的持久任务范围不得进入清理执行器。"""
    from yuxi.modules.knowledge.services import background_jobs

    executor = SimpleNamespace(clear_file_graph=AsyncMock())
    monkeypatch.setattr(background_jobs, "KnowledgeGraphService", lambda: executor)
    context = SimpleNamespace(payload={"kb_id": "kb", "action": "clear_file", **scope})
    with pytest.raises(ValueError, match="清除任务范围无效"):
        await background_jobs.run_knowledge_graph(context)
    executor.clear_file_graph.assert_not_awaited()


@pytest.mark.parametrize(
    "kb_type, file, error",
    [
        ("other", None, InvalidOperationError),
        ("milvus", None, ResourceNotFoundError),
        ("milvus", SimpleNamespace(kb_id="other"), ResourceNotFoundError),
        ("milvus", SimpleNamespace(kb_id="kb", is_folder=True), InvalidOperationError),
        ("milvus", SimpleNamespace(kb_id="kb", is_folder=False, status="indexing"), StateConflictError),
    ],
)
async def test_clear_rejects_unsupported_or_unstable_target(graph_boundary, monkeypatch, kb_type, file, error):
    """不支持的知识库、跨库文件、目录与处理中状态不能登记破坏性任务。"""
    _, kb, _, submit = graph_boundary
    kb.get_knowledge_base_info.return_value = SimpleNamespace(name="KB", kb_type=kb_type)
    monkeypatch.setattr(graph_build, "KnowledgeFileRepository", lambda: SimpleNamespace(get_by_file_id=AsyncMock(return_value=file)))
    with pytest.raises(error):
        await graph_build.clear_file_graph("kb", "file")
    submit.assert_not_awaited()
