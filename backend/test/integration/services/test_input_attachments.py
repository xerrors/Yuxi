"""真实 PG、MinIO 和文件验证附件接收与准备恢复。"""

import asyncio
import uuid
from datetime import timedelta
from io import BytesIO

import pytest
from sqlalchemy import func, select

from test.integration.services.agent_input_fixtures import SCOPE
from test.integration.services.agent_input_fixtures import send_input as _send
from test.integration.services.agent_input_fixtures import start_input_run as _start
from yuxi.infrastructure.minio import get_minio_client
from yuxi.modules.agents.models.attachments import AgentAttachment
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.services import attachments, inputs, runs, scheduler, threads
from yuxi.modules.agents.input_messages import build_chat_input_message
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.paths import ensure_user_workspace
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.modules.workspace.services.bindings import resolve_session_workdir_binding
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import DomainError, InvalidInputError, StateConflictError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
pytest_plugins = ["test.integration.services.agent_input_fixtures"]


@pytest.fixture
async def files_env(sessions, tmp_path, monkeypatch):  # noqa: F811
    """保留真实文件与对象边界，只有 ARQ 投递沿用隔离 fixture。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    ensure_user_workspace(SCOPE.uid)
    path = f"projects/2026-10-09_00-00-00_{uuid.uuid4().hex[:8]}"
    async with sessions() as db:
        project = await db.get(Project, "input-project")
        project.workdir_path = path
        await db.commit()
    monkeypatch.setattr(inputs, "resolve_session_workdir_binding", resolve_session_workdir_binding)
    monkeypatch.setattr(scheduler, "resolve_session_workdir_binding", resolve_session_workdir_binding)
    uploaded = []

    async def upload(name="source.txt", content=b"original"):
        async with sessions() as db:
            result = await attachments.upload_draft_file(file_content=content, filename=name, content_type="text/plain", scope=SCOPE, db=db)
            await db.commit()
        uploaded.append(result)
        return result

    async def submit(file, key, thread_id="input-thread", mode="follow_up"):
        """以队列测试来源接收，保留暂停状态直到显式消费。"""
        async with sessions() as db:
            return await inputs.accept_message(
                db=db,
                scope=SCOPE,
                thread_id=thread_id,
                idempotency_key=key,
                source="test_queue",
                mode=mode,
                messages=[build_chat_input_message("保留附件")],
                attachment_file_ids=[file["id"]],
            )

    try:
        yield sessions, upload, submit, Workdir(path, Workspace(SCOPE.uid))
    finally:
        client = get_minio_client()
        for file in uploaded:
            await client.adelete_objects_by_prefix(client.KB_BUCKETS["documents"], f"tmp/chat_attachments/{SCOPE.uid}/{file['id']}/")
            try:
                Workspace(SCOPE.uid).delete_authorized_path(f"/tmp/chat_attachments/{SCOPE.uid}/{file['id']}", root="/")
            except FileNotFoundError:
                pass


async def test_session_connectors_persist_without_inheriting_input_attachments_or_model_override(files_env):
    """连接器追加到会话；附件与单次模型覆盖只属于接受它们的 Input。"""
    factory, upload, _, _ = files_env
    file = await upload("scope.txt")
    async with factory() as db:
        first = await inputs.accept_message(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            idempotency_key="with-attachments-and-session-connectors",
            source="test_queue",
            mode="follow_up",
            messages=[build_chat_input_message("读取本次附件")],
            attachment_file_ids=[file["id"]],
            mcps=["extra"],
            model_spec="test:one-off",
        )
        following = await inputs.accept_message(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            idempotency_key="following-with-session-defaults",
            source="test_queue",
            mode="follow_up",
            messages=[build_chat_input_message("继续使用会话连接器")],
        )
    async with factory() as db:
        session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        initial = await db.get(AgentInput, first["input_id"])
        next_input = await db.get(AgentInput, following["input_id"])
        assert session.config_snapshot["mcps"] == initial.input_payload["context_snapshot"]["mcps"] == ["extra"]
        assert next_input.input_payload["context_snapshot"]["mcps"] == ["extra"]
        assert initial.input_payload["context_snapshot"]["model"] == "test:one-off"
        assert session.config_snapshot["model"] == next_input.input_payload["context_snapshot"]["model"] == "test:chat"
        assert "attachment_file_ids" not in session.config_snapshot
        assert initial.attachment_file_ids == [file["id"]]
        assert next_input.attachment_file_ids == []
        attachment = await db.get(AgentAttachment, file["id"])
        assert attachment.input_id == initial.id and attachment.message_id is None


async def test_multi_message_files_bind_last_projection_and_receipt_stays_unchanged(files_env):
    """提交级附件只挂末条消息，消费不修改接收回执。"""
    from sqlalchemy.exc import IntegrityError

    from yuxi.modules.agents.models.messages import Message

    factory, upload, submit, workdir = files_env
    first, second = await upload("first.txt"), await upload("second.txt")
    async with factory() as db:
        accepted = await inputs.accept_message(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            idempotency_key="two-messages",
            source="test_queue",
            mode="follow_up",
            messages=[
                build_chat_input_message("first"),
                build_chat_input_message("second"),
            ],
            attachment_file_ids=[first["id"], second["id"]],
        )
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        records = [await db.get(AgentAttachment, file["id"]) for file in (first, second)]
        assert [(item.input_id, item.message_id) for item in records] == [(accepted["input_id"], None)] * 2
        before = dict(
            (await db.execute(select(AgentInputReceipt.__table__).where(AgentInputReceipt.id == accepted["event_id"]))).mappings().one()
        )
        await threads.continue_queue(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="consume-files")
    async with factory() as db:
        projected = await AgentInputRepository(db).list_messages(accepted["input_id"])
        assert [item.content for item in projected] == ["first", "second"]
        by_message = await AttachmentRepository(db).for_messages([item.id for item in projected])
        assert by_message[projected[0].id] == []
        assert {a.id for a in by_message[projected[1].id]} == {first["id"], second["id"]}
        after = dict(
            (await db.execute(select(AgentInputReceipt.__table__).where(AgentInputReceipt.id == accepted["event_id"]))).mappings().one()
        )
        assert after == before
        assert await inputs.get_receipt_snapshot(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="two-messages") == accepted
        with pytest.raises(IntegrityError, match="fk_agent_attachments_message_origin"):
            async with db.begin_nested():
                attachment = await db.get(AgentAttachment, first["id"])
                other = await AgentInputRepository(db).create(
                    input_id="another-input",
                    messages=[{"role": "user", "content": [], "metadata": {}}],
                    kind="follow_up",
                    thread_id="input-thread",
                    uid=SCOPE.uid,
                    app_id=None,
                    agent_slug="main",
                )
                attachment.input_id = other.id
                await db.flush()
    draft = await upload("duplicate.txt")
    async with factory() as db:
        with pytest.raises(DomainError) as failure:
            await inputs.accept_message(
                db=db,
                scope=SCOPE,
                thread_id="input-thread",
                idempotency_key="duplicate-file",
                source="test_queue",
                mode="follow_up",
                messages=[build_chat_input_message(text) for text in ("A", "B")],
                attachment_file_ids=[draft["id"], draft["id"]],
            )
        assert isinstance(failure.value, InvalidInputError)
        await db.rollback()
        assert (await db.get(AgentAttachment, draft["id"])).input_id is None


async def test_unready_steer_head_blocks_later_ready_inputs(files_env, monkeypatch):
    """优先队头未就绪时，不跳过领取后来的引导或普通输入。"""
    factory, upload, submit, workdir = files_env
    current = await _start(factory)
    file = await upload()
    write = attachments._write_workdir_file

    async def fail(*args, **kwargs):
        """模拟队头文件准备失败。"""
        raise OSError("unavailable")

    monkeypatch.setattr(attachments, "_write_workdir_file", fail)
    head = await submit(file, "blocked-head", mode="steer")
    later = await _send(factory, "ready-later")
    await _send(factory, "ordinary", "follow_up")
    async with factory() as db:
        assert await AgentInputRepository(db).ready_batch(thread_id="input-thread", uid=SCOPE.uid, app_id=None) == []
    assert not await runs.should_yield_for_steer(current.id)
    monkeypatch.setattr(attachments, "_write_workdir_file", write)
    await scheduler.recover_pending_dispatches()
    async with factory() as db:
        batch = await AgentInputRepository(db).ready_batch(thread_id="input-thread", uid=SCOPE.uid, app_id=None)
        assert [item.id for item in batch] == [head["input_id"], later["input_id"]]


async def test_steer_batch_anchors_each_input_and_rolls_back_all_projections(files_env):
    """合批仍逐 Input 锚定；回滚后所有消息和附件关联都未消费。"""
    from yuxi.modules.agents.models.messages import Message

    factory, upload, _, _ = files_env
    files = [await upload("A.txt"), await upload("C.txt")]
    receipts = []
    async with factory() as db:
        for label, file in zip(("A", "C"), files, strict=True):
            receipts.append(
                await inputs.accept_message(
                    db=db,
                    scope=SCOPE,
                    thread_id="input-thread",
                    idempotency_key=label,
                    source="test_queue",
                    mode="steer",
                    messages=[build_chat_input_message(f"{label}{position}") for position in (1, 2)],
                    attachment_file_ids=[file["id"]],
                )
            )
        session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
        session.queue_paused = False
        await scheduler.claim_next_input(db=db, agent_session=session)
        assert await db.scalar(select(func.count()).select_from(Message)) == 4
        await db.rollback()
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
        for file, receipt in zip(files, receipts, strict=True):
            assert (await db.get(AgentAttachment, file["id"])).message_id is None
            assert (await db.get(AgentInput, receipt["input_id"])).status == "pending"
        await threads.continue_queue(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="consume-batch")
    async with factory() as db:
        run_ids = set()
        for file, receipt in zip(files, receipts, strict=True):
            projected = await AgentInputRepository(db).list_messages(receipt["input_id"])
            assert len(projected) == 2
            run_ids.add(projected[-1].run_id)
            assert (await db.get(AgentAttachment, file["id"])).message_id == projected[-1].id
            assert (
                await inputs.get_receipt_snapshot(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key=projected[0].content[0])
                == receipt
            )
        assert len(run_ids) == 1


async def test_draft_send_and_replay_have_one_durable_submission(files_env):
    """上传没有线程引用，发送与响应丢失重试只有一份 Input 和目标文件。"""
    factory, upload, submit, workdir = files_env
    file = await upload()
    async with factory() as db:
        assert await AttachmentRepository(db).list_for_thread("input-thread", SCOPE.uid, SCOPE.app_id) == []
        assert await db.scalar(select(func.count()).select_from(AgentInput)) == 0
    with pytest.raises(FileNotFoundError):
        workdir.list_directory("/")
    receipt = await submit(file, "one-submission")
    assert receipt["run_id"] is None
    async with factory() as db:
        item = await db.get(AgentInput, receipt["input_id"])
        assert not {"attachments_ready", "attachment_drafts", "attachment_error"} & item.input_payload.keys()
        attachment = await db.get(AgentAttachment, file["id"])
        assert attachment.status == "ready" and attachment.object_name is None
        [record] = await AttachmentRepository(db).list_for_thread("input-thread", SCOPE.uid, SCOPE.app_id)
        assert record.input_id == item.id
        messages = await AgentInputRepository(db).list_messages(item.id)
        assert messages == []
        assert record.message_id is None
        assert item.attachment_file_ids == [file["id"]]
    assert workdir.read_file(f"/uploads/{file['id']}_source.txt", 1024) == b"original"
    assert await submit(file, "one-submission") == receipt
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentInput)) == 1
        assert await db.scalar(select(func.count()).select_from(AgentInputReceipt)) == 1
    assert len(workdir.list_directory("/uploads")) == 1
    with pytest.raises(DomainError) as error:
        await submit(file, "another-submission")
    assert isinstance(error.value, StateConflictError)


@pytest.mark.parametrize("archive_via", [None, "session", "project"])
async def test_failed_preparation_blocks_dispatch_and_cancel_keeps_submission(files_env, monkeypatch, archive_via):
    """失败有持久错误；取消执行后恢复仍补全文件，不创建该 Input 的 Run。"""
    factory, upload, submit, workdir = files_env
    file = await upload()
    original_write = attachments._write_workdir_file

    async def fail(*args, **kwargs):
        raise OSError("private-host-path must not become public")

    monkeypatch.setattr(attachments, "_write_workdir_file", fail)
    receipt = await submit(file, "disk-error")
    async with factory() as db:
        session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        session.queue_paused = False
        await db.commit()
    assert await scheduler.dispatch_next_input(uid=SCOPE.uid, agent_slug="main", thread_id="input-thread") is True
    async with factory() as db:
        item = await db.get(AgentInput, receipt["input_id"])
        assert item.status == "pending" and await AttachmentRepository(db).has_unready(item.id)
        error = (await db.get(AgentAttachment, file["id"])).error
        assert "附件准备失败" in error
        assert "private-host-path" not in error
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
        assert await AgentInputRepository(db).ready_batch(thread_id="input-thread", uid=SCOPE.uid, app_id=None) == []
        with pytest.raises(DomainError) as blocked:
            await threads.promote_input(db=db, scope=SCOPE, thread_id="input-thread", input_id=item.id, idempotency_key="not-ready")
        assert isinstance(blocked.value, StateConflictError)
        await AgentInputRepository(db).cancel(item)
        await db.commit()
    if archive_via:
        async with factory() as db:
            if archive_via == "session":
                await threads.archive_thread(db=db, scope=SCOPE, thread_id="input-thread")
            else:
                project = await db.get(Project, "input-project")
                await ProjectRepository(db).delete_project_and_archive_threads(project, deleted_at=utc_now())
                await db.commit()
    monkeypatch.setattr(attachments, "_write_workdir_file", original_write)
    if archive_via:
        await scheduler.recover_pending_dispatches()
    else:
        assert await scheduler.dispatch_next_input(uid=SCOPE.uid, agent_slug="main", thread_id="input-thread") is True
    async with factory() as db:
        item = await db.get(AgentInput, receipt["input_id"])
        assert item.status == "cancelled" and not await AttachmentRepository(db).has_unready(item.id)
        assert (await db.get(AgentAttachment, file["id"])).error is None
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
        session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        assert session.status == ("archived" if archive_via else "active")
    assert workdir.read_file(f"/uploads/{file['id']}_source.txt", 1024) == b"original"


async def test_crash_after_file_write_replays_the_same_input_and_path(files_env, monkeypatch):
    """文件准备后、就绪事务提交前崩溃，可以从已接收来源收敛。"""
    factory, upload, submit, workdir = files_env
    file = await upload()
    prepare = attachments.prepare_input_attachments

    class ProcessCrash(BaseException):
        """模拟无法执行应用清理的进程退出。"""

    async def crash(**kwargs):
        await prepare(**kwargs)
        raise ProcessCrash()

    monkeypatch.setattr(attachments, "prepare_input_attachments", crash)
    with pytest.raises(ProcessCrash):
        await submit(file, "crash-replay")
    assert workdir.read_file(f"/uploads/{file['id']}_source.txt", 1024) == b"original"
    async with factory() as db:
        [item] = list(await db.scalars(select(AgentInput)))
        input_id = item.id
        assert await AttachmentRepository(db).has_unready(item.id)
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
    monkeypatch.setattr(attachments, "prepare_input_attachments", prepare)
    receipt = await submit(file, "crash-replay")
    async with factory() as db:
        assert await inputs.get_receipt_snapshot(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="crash-replay") == receipt
        assert await AttachmentRepository(db).has_unready(input_id)
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
    await scheduler.recover_pending_dispatches()
    assert receipt["input_id"] == input_id
    assert len(workdir.list_directory("/uploads")) == 1
    async with factory() as db:
        assert not await AttachmentRepository(db).has_unready(input_id)
        assert await db.scalar(select(func.count()).select_from(AgentInputReceipt)) == 1


async def test_expiration_cannot_remove_received_source_and_steer_waits_for_ready(files_env, monkeypatch):
    """接收后的过期源仍可恢复，未就绪 steer 不触发运行接管。"""
    factory, upload, submit, workdir = files_env
    current = await _start(factory)
    file = await upload()
    write = attachments._write_workdir_file

    async def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(attachments, "_write_workdir_file", fail)
    receipt = await submit(file, "steer-preparing", mode="steer")
    assert await runs.should_yield_for_steer(current.id) is False
    client = get_minio_client()
    bucket = client.KB_BUCKETS["documents"]
    async with factory() as db:
        row = await db.get(AgentAttachment, file["id"])
        row.expires_at = utc_now() - timedelta(seconds=1)
        object_name = row.object_name
        await db.commit()
    await upload("trigger-cleanup.txt")
    assert await client.adownload_file(bucket, object_name) == b"original"
    monkeypatch.setattr(attachments, "_write_workdir_file", write)
    assert await submit(file, "steer-preparing", mode="steer") == receipt
    await scheduler.recover_pending_dispatches()
    assert await runs.should_yield_for_steer(current.id) is True
    assert workdir.read_file(f"/uploads/{file['id']}_source.txt", 1024) == b"original"


@pytest.mark.parametrize("reverse_files", [False, True])
async def test_same_draft_cannot_be_received_by_two_threads(files_env, monkeypatch, reverse_files):
    """跨线程并发提交在真实 PG 文件锁下只产生一个归属。"""
    factory, upload, submit, workdir = files_env
    file = await upload()
    async with factory() as db:
        await SessionRepository(db).add_session(
            uid=SCOPE.uid,
            agent_id="main",
            thread_id="second-thread",
            project_id="input-project",
            config_snapshot={"model": "test:chat", "tool_approval_mode": "default"},
        )
        await db.commit()
    if reverse_files:
        second = await upload("second.txt")
        stage = inputs.stage_input_attachments

        async def yield_after_stage(**kwargs):
            """放大文件反向锁竞争，未统一锁顺序时产生数据库死锁。"""
            result = await stage(**kwargs)
            await asyncio.sleep(0.05)
            return result

        monkeypatch.setattr(inputs, "stage_input_attachments", yield_after_stage)

        async def submit_messages(thread_id, files):
            """同一组文件以相反引用顺序提交到不同 Session。"""
            async with factory() as db:
                return await inputs.accept_message(
                    db=db,
                    scope=SCOPE,
                    thread_id=thread_id,
                    idempotency_key=thread_id,
                    source="test_queue",
                    mode="follow_up",
                    messages=[build_chat_input_message(item["filename"]) for item in files],
                    attachment_file_ids=[item["id"] for item in files],
                )

        requests = [submit_messages("input-thread", [file, second]), submit_messages("second-thread", [second, file])]
    else:
        requests = [submit(file, "thread-one"), submit(file, "thread-two", "second-thread")]
    async with asyncio.timeout(10):
        results = await asyncio.gather(*requests, return_exceptions=True)
    assert sum(isinstance(item, dict) for item in results) == 1
    [failure] = [item for item in results if isinstance(item, Exception)]
    assert isinstance(failure, DomainError) and isinstance(failure, StateConflictError)
    async with factory() as db:
        owner = await db.get(AgentAttachment, file["id"])
        assert owner.input_id == next(item["input_id"] for item in results if isinstance(item, dict))
        assert await db.scalar(select(func.count()).select_from(AgentInput)) == 1
    assert workdir.read_file(f"/uploads/{file['id']}_source.txt", 1024) == b"original"


async def test_ready_steer_append_preserves_expired_source_and_committed_files(files_env, monkeypatch):
    """追加失败与重试不覆盖既有原件和解析目录，已提交来源不会到期清理。"""
    factory, upload, submit, workdir = files_env
    current = await _start(factory)
    first = await upload("first.pdf")
    client = get_minio_client()
    bucket = client.KB_BUCKETS["documents"]
    prefix = f"tmp/chat_attachments/{SCOPE.uid}/{first['id']}"
    parsed_source = f"{prefix}/parsed/v1/document.md"
    source = Workdir(f"{prefix}/parsed/v1", Workspace(SCOPE.uid))
    source.copy_file_from_stream("/document.md", BytesIO(b"![image](images/page.png)"), max_bytes=1024)
    source.copy_file_from_stream("/images/page.png", BytesIO(b"parsed image"), max_bytes=1024)
    async with factory() as db:
        (await db.get(AgentAttachment, first["id"])).parsed_source = parsed_source
        await db.commit()
    receipt = await submit(first, "first-ready-steer", mode="steer")
    assert await runs.should_yield_for_steer(current.id) is True
    original_path = f"/uploads/{first['id']}_first.pdf"
    parsed_path = f"/uploads/attachments/{first['id']}"
    workdir.write_file(original_path, b"user modified original")
    workdir.write_file(f"{parsed_path}/document.md", b"user modified markdown")
    second = await upload("second.txt")
    assert await client.alist_object_metadata(bucket, f"{prefix}/") == []
    with pytest.raises(FileNotFoundError):
        source.read_file("/images/page.png", 1024)
    write = attachments._write_workdir_file

    async def fail(*args, **kwargs):
        raise OSError("second file unavailable")

    monkeypatch.setattr(attachments, "_write_workdir_file", fail)
    appended = await submit(second, "second-ready-steer", mode="steer")
    assert appended["input_id"] != receipt["input_id"]
    assert await runs.should_yield_for_steer(current.id) is True
    assert workdir.read_file(original_path, 1024) == b"user modified original"
    assert workdir.read_file(f"{parsed_path}/document.md", 1024) == b"user modified markdown"
    assert workdir.read_file(f"{parsed_path}/images/page.png", 1024) == b"parsed image"
    async with factory() as db:
        assert len(await AttachmentRepository(db).list_for_thread("input-thread", SCOPE.uid, SCOPE.app_id)) == 2
        assert not await AttachmentRepository(db).has_unready(receipt["input_id"])
        assert await AttachmentRepository(db).has_unready(appended["input_id"])
    monkeypatch.setattr(attachments, "_write_workdir_file", write)
    assert await submit(second, "second-ready-steer", mode="steer") == appended
    await scheduler.recover_pending_dispatches()
    assert await runs.should_yield_for_steer(current.id) is True
    assert workdir.read_file(original_path, 1024) == b"user modified original"
    assert workdir.read_file(f"{parsed_path}/document.md", 1024) == b"user modified markdown"
    assert workdir.read_file(f"/uploads/{second['id']}_second.txt", 1024) == b"original"
    async with factory() as db:
        assert len(await AttachmentRepository(db).list_for_thread("input-thread", SCOPE.uid, SCOPE.app_id)) == 2


async def test_attachment_delete_guards_and_failed_commit_preserve_formal_bytes(files_env, monkeypatch):
    """待消费、执行中和事务失败均不能删除正式文件，成功才删除关系与字节。"""
    from unittest.mock import AsyncMock

    from yuxi.modules.agents.models.turns import AgentTurn

    factory, upload, submit, workdir = files_env
    file = await upload()
    receipt = await submit(file, "delete-guards")
    path = f"/uploads/{file['id']}_source.txt"

    async def delete(db):
        return await attachments.delete_thread_attachment_view(thread_id="input-thread", file_id=file["id"], db=db, current_uid=SCOPE.uid)

    async with factory() as db:
        with pytest.raises(DomainError) as error:
            await delete(db)
        assert isinstance(error.value, StateConflictError)
        await AgentInputRepository(db).cancel(await db.get(AgentInput, receipt["input_id"]))
        await db.commit()
    async with factory() as db:
        monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
        with pytest.raises(RuntimeError, match="commit failed"):
            await delete(db)
    assert workdir.read_file(path, 1024) == b"original"
    async with factory() as db:
        assert await db.get(AgentAttachment, file["id"]) is not None
    run = await _start(factory)
    async with factory() as db:
        with pytest.raises(DomainError) as error:
            await delete(db)
        assert isinstance(error.value, StateConflictError)
    assert workdir.read_file(path, 1024) == b"original"
    async with factory() as db:
        active = await db.get(AgentRun, run.id)
        active.status = "cancelled"
        (await db.get(AgentTurn, active.turn_id)).status = "cancelled"
        await db.commit()
        assert (await delete(db))["message"] == "附件已删除"
    with pytest.raises(FileNotFoundError):
        workdir.read_file(path, 1024)
    async with factory() as db:
        assert await db.get(AgentAttachment, file["id"]) is None
        assert (await db.get(AgentInput, receipt["input_id"])).attachment_file_ids == [file["id"]]
        messages = await AgentInputRepository(db).list_messages(receipt["input_id"])
        assert messages == []
