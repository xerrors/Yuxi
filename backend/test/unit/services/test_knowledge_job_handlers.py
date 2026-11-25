"""知识文档作业的阶段顺序、attempt 身份、取消及有界结果契约。"""

import asyncio
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.services import background_jobs as jobs


class JobContext:
    """记录对执行上下文的可观察写入，并在指定安全点取消。"""

    job_id = "document-job"
    worker_id = "document-attempt"

    def __init__(self, payload, *, cancel_at=None, cancellation=None):
        """绑定持久输入和可选取消边界。"""
        self.payload = payload
        self.result = None
        self.progress = []
        self.checks = 0
        self.cancel_at = cancel_at
        self.cancellation = cancellation

    async def raise_if_cancelled(self):
        """在真实 Handler 选择的安全点响应取消。"""
        self.checks += 1
        if self.checks == self.cancel_at:
            raise self.cancellation

    async def set_progress(self, progress, message=None):
        """保存领域发布的进度与阶段说明。"""
        self.progress.append((progress, message))

    async def set_result(self, result):
        """保存失败收尾前已发布的完整摘要。"""
        self.result = result


@pytest.mark.parametrize("action", ["parse", "index"])
@pytest.mark.parametrize("scope", ["files", "pending"])
@pytest.mark.parametrize("params", [{}, {"chunk_preset_id": "general", "chunk_parser_config": {"size": 123}}])
async def test_document_action_preserves_arguments_owner_and_input_order(monkeypatch, action, scope, params):
    """解析与索引执行接收原参数及归属，并保持输入顺序。"""
    payload = {
        "kb_id": "kb",
        "operator_id": "operator",
        "file_ids": ["a", "b"],
        "scope": scope,
        "statuses": ["uploaded"],
        "count": 2,
        "params": params,
    }
    original = deepcopy(payload)
    context = JobContext(payload)
    trace = []

    async def process(kb_id, file_id, **kwargs):
        """锁定动作边界收到的 attempt 身份及参数。"""
        expected = {"operator_id": "operator", "processing_job_id": context.job_id, "processing_owner": context.worker_id}
        expected["params"] = params
        assert kb_id == "kb" and kwargs == expected
        trace.append((action, file_id))
        return {"file_id": file_id, "status": "parsed" if action == "parse" else "indexed"}

    async def pending(kb_id, *, statuses, after_file_id, limit):
        """消费固定的两文件状态集合。"""
        assert (kb_id, statuses, limit) == ("kb", ["uploaded"], 500)
        return ["a", "b"] if after_file_id is None else []

    monkeypatch.setattr(jobs.document_actions, f"{action}_file", process)
    monkeypatch.setattr(jobs.knowledge_documents, "list_document_file_ids_by_statuses", pending)
    result = await getattr(jobs, f"run_knowledge_{action}")(context)
    assert trace == [(action, "a"), (action, "b")]
    assert result == context.result and result["succeeded"] == 2
    assert payload == original


@pytest.mark.parametrize("action", ["parse", "index"])
async def test_pending_pages_advance_after_failures_and_count_beyond_result_sample(monkeypatch, action):
    """第 500、501 项失败仍计入终态，游标不能因失败停滞。"""
    ids = [f"file-{index:04d}" for index in range(502)]
    context = JobContext({"kb_id": "kb", "operator_id": "operator", "scope": "pending", "statuses": ["uploaded"], "count": 500})
    cursors, processed = [], []

    async def pending(kb_id, *, statuses, after_file_id, limit):
        """按公开游标返回真实大小的多页输入。"""
        assert (kb_id, statuses, limit) == ("kb", ["uploaded"], 500)
        cursors.append(after_file_id)
        start = 0 if after_file_id is None else ids.index(after_file_id) + 1
        return ids[start : start + limit]

    async def process(_kb_id, file_id, **_kwargs):
        """覆盖样本外异常和正常返回失败的两种业务结局。"""
        processed.append(file_id)
        if file_id == ids[499]:
            raise RuntimeError("private failure")
        return {"file_id": file_id, "status": "failed" if file_id == ids[500] else "parsed"}

    monkeypatch.setattr(jobs.knowledge_documents, "list_document_file_ids_by_statuses", pending)
    monkeypatch.setattr(jobs.document_actions, f"{action}_file", process)
    with pytest.raises(RuntimeError, match="失败 2 个"):
        await getattr(jobs, f"run_knowledge_{action}")(context)
    assert cursors == [None, ids[499], ids[-1]]
    assert processed == ids
    assert context.result == {
        "items": [{"file_id": file_id, "status": "parsed"} for file_id in ids[:200]],
        "processed": 502,
        "succeeded": 500,
        "failed": 2,
        "outcome": "partial",
        "result_truncated": True,
    }
    assert context.progress[-2][0] == 95.0 and context.progress[-1][0] == 100.0


@pytest.mark.parametrize("action", ["parse", "index"])
@pytest.mark.parametrize("scope", ["files", "pending"])
@pytest.mark.parametrize("source", ["checkpoint", "action"])
async def test_document_action_propagates_cancellation_without_finishing(monkeypatch, action, scope, source):
    """安全点或底层动作取消均不变成单文件失败，也不执行后续文件。"""
    cancellation = asyncio.CancelledError("cancelled attempt")
    context = JobContext(
        {"kb_id": "kb", "operator_id": "operator", "scope": scope, "file_ids": ["a", "b"], "statuses": ["uploaded"], "count": 2},
        cancel_at=2 if source == "checkpoint" else None,
        cancellation=cancellation,
    )
    processed = []

    async def process(_kb_id, file_id, **_kwargs):
        """在第一项内部或其完成后的安全点取消。"""
        processed.append(file_id)
        if source == "action":
            raise cancellation
        return {"file_id": file_id, "status": "parsed"}

    async def pending(*_args, **_kwargs):
        """提供未执行的后续文件以证明停止边界。"""
        return ["a", "b"]

    monkeypatch.setattr(jobs.document_actions, f"{action}_file", process)
    monkeypatch.setattr(jobs.knowledge_documents, "list_document_file_ids_by_statuses", pending)
    with pytest.raises(asyncio.CancelledError) as error:
        await getattr(jobs, f"run_knowledge_{action}")(context)
    assert error.value is cancellation
    assert processed == ["a"] and context.result is None
    assert all(progress < 100 for progress, _ in context.progress)


@pytest.mark.parametrize("auto_index", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
async def test_ingest_preserves_global_stages_owner_and_input_result_order(monkeypatch, auto_index, legacy):
    """混合失败不中断后续文件，所有阶段保持批次顺序和原输入位置。"""
    items = ["ok-a", "parse-bad", "index-bad", "ok-b"]
    context = JobContext(
        {
            "kb_id": "kb",
            "operator_id": "operator",
            "files": [{"index": index, "item": item, "file_id": item, "status": "uploaded"} for index, item in enumerate(items)],
            "params": {"auto_index": auto_index, "chunk_preset_id": "general", "chunk_parser_config": None},
        }
    )
    if not legacy:
        context.payload["file_ids"] = [item["file_id"] for item in context.payload.pop("files")]
    trace = []

    async def parse(kb_id, file_id, **kwargs):
        """解析只接收原身份，不接收索引专用参数。"""
        assert kb_id == "kb" and kwargs == {
            "operator_id": "operator",
            "processing_job_id": context.job_id,
            "processing_owner": context.worker_id,
        }
        trace.append(("parse", file_id))
        if file_id == "parse-bad":
            raise TimeoutError("parse timeout")
        return {"file_id": file_id, "status": "parsed", "error": None}

    async def index(kb_id, file_id, **kwargs):
        """记录解析阶段完全结束后才开始的索引动作。"""
        assert kb_id == "kb" and kwargs == {
            "operator_id": "operator",
            "processing_job_id": context.job_id,
            "processing_owner": context.worker_id,
            "params": {"chunk_preset_id": "general"},
        }
        trace.append(("index", file_id))
        if file_id == "index-bad":
            raise RuntimeError("index failure")
        return {"file_id": file_id, "status": "indexed", "error": None}

    for name, fn in (("parse_file", parse), ("index_file", index)):
        monkeypatch.setattr(jobs.document_actions, name, fn)
    with pytest.raises(RuntimeError, match=f"失败 {2 if auto_index else 1} 个"):
        await jobs.run_knowledge_ingest(context)
    expected = [("parse", item) for item in items]
    if auto_index:
        expected += [("index", item) for item in ["ok-a", "index-bad", "ok-b"]]
    assert trace == expected
    result = context.result
    assert result["submitted"] == result["processed"] == 4
    assert result["failed"] == (2 if auto_index else 1)
    assert result["items"][0] == {"file_id": "ok-a", "status": "indexed" if auto_index else "parsed", "error": None}
    assert result["items"][-1]["file_id"] == "ok-b"
    assert [item["status"] for item in result["items"]] == [
        "indexed" if auto_index else "parsed",
        "failed",
        "failed" if auto_index else "parsed",
        "indexed" if auto_index else "parsed",
    ]
    assert [progress for progress, message in context.progress if message.startswith("[2/3]")][-1] == (60.0 if auto_index else 95.0)
    if auto_index:
        assert [progress for progress, message in context.progress if message.startswith("[3/3]")][-1] == 95.0


@pytest.mark.parametrize("stage", ["parse", "index"])
@pytest.mark.parametrize("source", ["checkpoint", "action"])
async def test_ingest_preserves_cancellation_at_each_stage(monkeypatch, stage, source):
    """取消保留原进度提示，终止余下动作且不发布完成摘要。"""
    cancellation = asyncio.CancelledError("cancelled ingest")
    context = JobContext(
        {
            "kb_id": "kb",
            "operator_id": "operator",
            "files": [
                {"index": 0, "item": "a", "file_id": "a", "status": "uploaded"},
                {"index": 1, "item": "b", "file_id": "b", "status": "uploaded"},
            ],
            "params": {"auto_index": True},
        },
        cancel_at={"parse": 1, "index": 3}[stage] if source == "checkpoint" else None,
        cancellation=cancellation,
    )
    trace = []

    async def parse(_kb_id, file_id, **_kwargs):
        """在解析动作中恢复取消。"""
        trace.append(("parse", file_id))
        if stage == "parse" and source == "action":
            raise cancellation
        return {"file_id": file_id, "status": "parsed"}

    async def index(_kb_id, file_id, **_kwargs):
        """在索引动作中恢复取消。"""
        trace.append(("index", file_id))
        if stage == "index" and source == "action":
            raise cancellation
        return {"file_id": file_id, "status": "indexed"}

    for name, fn in (("parse_file", parse), ("index_file", index)):
        monkeypatch.setattr(jobs.document_actions, name, fn)
    with pytest.raises(asyncio.CancelledError) as error:
        await jobs.run_knowledge_ingest(context)
    assert error.value is cancellation and context.result is None
    assert context.progress[-1] == (100.0, "任务已取消")
    expected = []
    if stage == "index":
        expected += [("parse", "a"), ("parse", "b")]
    if source == "action":
        expected.append((stage, "a"))
    assert trace == expected


@pytest.mark.parametrize("path", ["files-parse", "files-index", "pending-parse", "pending-index", "ingest-parse", "ingest-index"])
async def test_failure_logs_interpolate_identifiers_without_paths_or_exception_secrets(monkeypatch, path):
    """真实日志 sink 拒绝字面占位符、原始上传路径与异常秘密。"""
    messages = []
    context = JobContext(
        {
            "kb_id": "kb",
            "operator_id": "operator",
            "file_ids": ["file"],
            "files": [{"index": 0, "item": "secret-upload-path", "file_id": "file", "status": "uploaded"}],
            "scope": "pending" if path.startswith("pending") else "files",
            "statuses": ["uploaded"],
            "count": 1,
            "params": {"auto_index": True},
        }
    )
    broken = path.split("-")[-1]

    async def parse(*_args, **_kwargs):
        """提供正文解析结果或异常。"""
        if broken == "parse":
            raise RuntimeError("secret-exception-content")
        return {"file_id": "file", "status": "parsed"}

    async def index(*_args, **_kwargs):
        """恢复索引异常。"""
        raise RuntimeError("secret-exception-content")

    async def pending(*_args, after_file_id, **_kwargs):
        """提供一次待处理输入。"""
        return [] if after_file_id else ["file"]

    for name, fn in (
        ("parse_file", parse),
        ("index_file", index),
        ("list_document_file_ids_by_statuses", pending),
    ):
        owner = jobs.knowledge_documents if name == "list_document_file_ids_by_statuses" else jobs.document_actions
        monkeypatch.setattr(owner, name, fn)
    sink = logger.add(lambda message: messages.append(message.record["message"]), level="ERROR")
    try:
        handler = jobs.run_knowledge_ingest if path.startswith("ingest") else getattr(jobs, f"run_knowledge_{broken}")
        with pytest.raises(RuntimeError):
            await handler(context)
    finally:
        logger.remove(sink)
    assert len(messages) == 1
    message = messages[0]
    assert context.job_id in message and "RuntimeError" in message
    assert "%s" not in message and "secret-upload-path" not in message and "secret-exception-content" not in message
    assert "file_id=file" in message


@pytest.mark.parametrize("enabled", [False, True])
def test_shipping_projection_cron_uses_only_the_owned_cleanup_module(enabled):
    """冷启动注册正确执行入口，禁用知识能力时不注册清理任务。"""
    script = """
import os
from yuxi.workers.settings import WorkerSettings
from yuxi.modules.knowledge.services import background_jobs
jobs = [job for job in WorkerSettings.cron_jobs if job.coroutine.__name__ == 'process_knowledge_projections']
assert len(jobs) == int(os.environ['YUXI_KNOWLEDGE_ENABLED'] == 'true')
assert not hasattr(background_jobs, 'process_knowledge_projections')
if jobs:
    assert jobs[0].coroutine.__module__ == 'yuxi.modules.knowledge.services.projection_cleanup'
    assert jobs[0].second == {15, 45}
    assert jobs[0].run_at_startup is True
"""
    env = {**os.environ, "YUXI_KNOWLEDGE_ENABLED": str(enabled).lower()}
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[3],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("action", "scope"),
    [("parse", "files"), ("index", "files"), ("parse", "pending"), ("index", "pending"), ("index", "ingest")],
)
async def test_parameter_claim_failure_continues_with_later_files(monkeypatch, action, scope):
    """动作内的参数领取失败仍保留部分结果，并继续处理后续文件。"""
    context = JobContext(
        {
            "kb_id": "kb",
            "operator_id": "operator",
            "scope": scope,
            "file_ids": ["bad", "ok"],
            "files": [
                {"index": 0, "item": "bad", "file_id": "bad", "status": "uploaded"},
                {"index": 1, "item": "ok", "file_id": "ok", "status": "uploaded"},
            ],
            "statuses": ["uploaded"] if action == "parse" else ["parsed", "error_indexing"],
            "count": 2,
            "params": {"auto_index": True, "chunk_preset_id": "general"},
        }
    )
    trace = []

    async def parse(_kb_id, file_id, **_kwargs):
        """由解析入口报告参数领取失败。"""
        trace.append(("parse", file_id))
        if action == "parse" and file_id == "bad":
            raise ValueError("private parameter failure")
        return {"file_id": file_id, "status": "parsed"}

    async def index(_kb_id, file_id, **_kwargs):
        """由索引入口报告参数领取失败。"""
        trace.append(("index", file_id))
        if file_id == "bad":
            raise ValueError("private parameter failure")
        return {"file_id": file_id, "status": "indexed"}

    async def pending(*_args, after_file_id, **_kwargs):
        """提供含失败项和后续项的一页输入。"""
        return ["bad", "ok"] if after_file_id is None else []

    for name, fn in (
        ("parse_file", parse),
        ("index_file", index),
        ("list_document_file_ids_by_statuses", pending),
    ):
        owner = jobs.knowledge_documents if name == "list_document_file_ids_by_statuses" else jobs.document_actions
        monkeypatch.setattr(owner, name, fn)
    handler = jobs.run_knowledge_ingest if scope == "ingest" else getattr(jobs, f"run_knowledge_{action}")
    with pytest.raises(RuntimeError, match="失败 1 个"):
        await handler(context)
    prefix = [("parse", "bad"), ("parse", "ok")] if scope == "ingest" else []
    assert trace == prefix + [(action, "bad"), (action, "ok")]
    assert context.result["processed"] == 2 and context.result["failed"] == context.result["succeeded"] == 1
    assert context.result["items"][0]["status"] == "failed"
    assert context.result["items"][1] == {"file_id": "ok", "status": "parsed" if action == "parse" else "indexed"}
    assert context.progress[-1][0] == 100.0
