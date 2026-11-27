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
from yuxi.modules.agents.input_messages import build_chat_input_message
from yuxi.modules.agents.models.attachments import AgentAttachment
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.services import attachments, inputs, runs, scheduler, threads
from yuxi.modules.agents.services.run_lifecycle import settle_run
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.paths import create_managed_workdir, user_workspace_dir
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import DomainError, InvalidInputError, StateConflictError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
pytest_plugins = ["test.integration.services.agent_input_fixtures"]


@pytest.fixture
async def files_env(sessions, tmp_path, monkeypatch):  # noqa: F811
    """保留真实文件与对象边界，只有 ARQ 投递沿用隔离 fixture。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    path = f"agents/tasks/2026-10-09/new-chat-{uuid.uuid4().hex[:6]}"
    async with sessions() as db:
        project = await db.get(Project, "input-project")
        project.workdir_path = path
        await db.commit()
    create_managed_workdir(SCOPE.uid, path)
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
    Workspace(SCOPE.uid).delete_authorized_path(workdir.root_path, root="/")
    file = await upload()
    async with factory() as db:
        assert await AttachmentRepository(db).list_for_thread("input-thread", SCOPE.uid, SCOPE.app_id) == []
        assert await db.scalar(select(func.count()).select_from(AgentInput)) == 0
    with pytest.raises(FileNotFoundError):
        workdir.list_directory("/")
    with pytest.raises(StateConflictError) as missing:
        await submit(file, "one-submission")
    assert missing.value.code == "session_workdir_unavailable"
    create_managed_workdir(SCOPE.uid, workdir.relative_path)
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


@pytest.mark.parametrize("resolution", ["restore", "discard", "discard-after-restore"])
@pytest.mark.parametrize("phase", ["before-preparation", "after-write", "after-check"])
async def test_deleted_workdir_preserves_received_source_until_restore_or_explicit_delete(files_env, monkeypatch, resolution, phase):
    """接收后到就绪提交前删除目录，保留附件来源与原输入。"""
    factory, upload, submit, workdir = files_env
    file = await upload()

    def remove_workdir():
        """模拟个人空间在附件写入流程之外删除目录。"""
        Workspace(SCOPE.uid).delete_authorized_path(workdir.root_path, root="/")

    with monkeypatch.context() as scoped:
        if phase == "after-check":
            check_files = attachments.require_attachment_files

            def check_then_delete(uid, paths):
                """文件检查成功后、数据库提交前删除目录。"""
                check_files(uid, paths)
                remove_workdir()

            scoped.setattr(attachments, "require_attachment_files", check_then_delete)
        else:
            operation = "_store_attachment" if phase == "after-write" else "prepare_input_attachments"
            original = getattr(attachments, operation)

            async def remove_workdir_during_preparation(**kwargs):
                if phase == "before-preparation":
                    remove_workdir()
                result = await original(**kwargs)
                if phase == "after-write":
                    remove_workdir()
                return result

            scoped.setattr(attachments, operation, remove_workdir_during_preparation)
        receipt = await submit(file, "remove-before-ready")
    client = get_minio_client()
    bucket = client.KB_BUCKETS["documents"]
    delete_args = {"thread_id": "input-thread", "file_id": file["id"], "current_uid": SCOPE.uid}

    async with factory() as db:
        record = await db.get(AgentAttachment, file["id"])
        assert record.status == "preparing"
        assert ("工作目录" if phase == "before-preparation" else "附件文件") in record.error
        object_name = record.object_name
        assert await client.adownload_file(bucket, object_name) == b"original"
        assert (await db.get(AgentInput, receipt["input_id"])).status == "pending"
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
        with pytest.raises(StateConflictError, match="被输入使用"):
            await attachments.delete_thread_attachment_view(db=db, **delete_args)
        await db.rollback()

    if resolution != "restore":
        async with factory() as db:
            item = await db.get(AgentInput, receipt["input_id"])
            await AgentInputRepository(db).cancel(item)
            await db.commit()
        await scheduler.recover_pending_dispatches()
        assert await client.adownload_file(bucket, object_name) == b"original"
        directory_restored = resolution == "discard-after-restore"
        if directory_restored:
            async with factory() as db:

                async def fail_commit():
                    """来源已清除时模拟删除事务提交失败。"""
                    raise RuntimeError("delete commit interrupted")

                with monkeypatch.context() as scoped:
                    scoped.setattr(db, "commit", fail_commit)
                    with pytest.raises(RuntimeError, match="delete commit interrupted"):
                        await attachments.delete_thread_attachment_view(db=db, **delete_args)
                await db.rollback()
            assert await client.astat_file(bucket, object_name) is None
            create_managed_workdir(SCOPE.uid, workdir.relative_path)
            await scheduler.recover_pending_dispatches()
            async with factory() as db:
                assert (await db.get(AgentAttachment, file["id"])).status == "preparing"
        async with factory() as db:
            await attachments.delete_thread_attachment_view(db=db, **delete_args)
        async with factory() as db:
            assert await db.get(AgentAttachment, file["id"]) is None
            assert (await db.get(AgentInput, receipt["input_id"])).status == "cancelled"
            assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
        assert await client.astat_file(bucket, object_name) is None
        assert (user_workspace_dir(SCOPE.uid) / workdir.relative_path).is_dir() == directory_restored
    else:
        create_managed_workdir(SCOPE.uid, workdir.relative_path)
        await scheduler.recover_pending_dispatches()
        async with factory() as db:
            record = await db.get(AgentAttachment, file["id"])
            assert record.status == "ready" and record.error is None and record.object_name is None
            assert (await db.get(AgentInput, receipt["input_id"])).status == "pending"
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


@pytest.mark.parametrize("claim", ["dispatch", "continue", "steer"])
async def test_claim_keeps_ready_source_when_formal_file_has_disappeared(files_env, monkeypatch, claim):
    """正式文件丢失但 MinIO 来源仍在时，三个领取入口都等待重新准备。"""
    factory, upload, submit, workdir = files_env
    current = await _start(factory) if claim == "steer" else None
    file = await upload()
    client = get_minio_client()

    async def skip_cleanup(**_kwargs):
        """留下就绪事务提交后、清理来源前的持久状态。"""

    with monkeypatch.context() as scoped:
        scoped.setattr(attachments, "cleanup_prepared_sources", skip_cleanup)
        receipt = await submit(file, "ready-before-cleanup", mode="steer" if current is not None else "follow_up")
    Workspace(SCOPE.uid).delete_authorized_path(workdir.root_path, root="/")
    create_managed_workdir(SCOPE.uid, workdir.relative_path)

    async with factory() as db:
        attachment = await db.get(AgentAttachment, file["id"])
        assert attachment.status == "ready"
        object_name = attachment.object_name
        if claim == "continue":
            control = await threads.continue_queue(
                db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="continue-missing-attachment"
            )
            assert control["run_id"] is None
        else:
            if claim == "steer":
                run = await db.get(AgentRun, current.id)
                output = Message(
                    session_record_id=run.session_record_id,
                    role="assistant",
                    content="Current run is done.",
                    run_id=run.id,
                    turn_id=run.turn_id,
                    delivery_status="complete",
                )
                db.add(output)
                await db.flush()
                run.output_message_id = output.id
                assert not await runs.should_yield_for_steer(run.id)
                with monkeypatch.context() as scoped:
                    scoped.setattr(client, "astat_file", lambda *_args: pytest.fail("当前 Run 结算不应探测对象来源"))
                    settlement = await settle_run(db=db, run=run, worker_id="owner", status="completed", token_usage=None)
                assert settlement.status == "completed" and settlement.next_run_id is None
                run.runtime_cleanup_pending = False
            else:
                (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused = False
            await db.commit()
    if claim == "dispatch":
        assert await scheduler.dispatch_next_input(uid=SCOPE.uid, agent_slug="main", thread_id="input-thread")

    bucket = client.KB_BUCKETS["documents"]
    async with factory() as db:
        attachment = await db.get(AgentAttachment, file["id"])
        assert attachment.status == ("preparing" if claim == "dispatch" else "ready")
        assert attachment.object_name == object_name
        item = await db.get(AgentInput, receipt["input_id"])
        assert item.status == "pending" and item.consumed_run_id is None
    assert await client.adownload_file(bucket, object_name) == b"original"

    await scheduler.recover_pending_dispatches()
    await scheduler.recover_pending_dispatches()
    async with factory() as db:
        item = await db.get(AgentInput, receipt["input_id"])
        assert item.status == "consumed"
        assert (await db.get(AgentRun, item.consumed_run_id)).status == "pending"
        assert (await db.get(AgentAttachment, file["id"])).object_name is None
    assert await client.astat_file(bucket, object_name) is None
    assert workdir.read_file(f"/uploads/{file['id']}_source.txt", 1024) == b"original"


async def test_incomplete_source_cleanup_does_not_leave_an_input_waiting_forever(files_env, monkeypatch):
    """临时对象已删除但清除列未提交时，领取后走 worker 的文件缺失终态。"""
    factory, upload, submit, workdir = files_env
    file = await upload()

    async def skip_cleanup(**_kwargs):
        """保留已提交的临时对象引用。"""

    with monkeypatch.context() as scoped:
        scoped.setattr(attachments, "cleanup_prepared_sources", skip_cleanup)
        receipt = await submit(file, "source-already-removed")
    async with factory() as db:
        attachment = await db.get(AgentAttachment, file["id"])
        object_name = attachment.object_name
        path = attachment.path
        (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused = False
        await db.commit()
    client = get_minio_client()
    bucket = client.KB_BUCKETS["documents"]
    await client.adelete_file(bucket, object_name)
    assert await client.astat_file(bucket, object_name) is None
    workdir.delete(f"/uploads/{file['id']}_source.txt")

    await scheduler.recover_pending_dispatches()
    await scheduler.recover_pending_dispatches()
    async with factory() as db:
        item = await db.get(AgentInput, receipt["input_id"])
        assert item.status == "consumed" and item.consumed_run_id
        assert (await db.get(AgentAttachment, file["id"])).object_name is None
    with pytest.raises(StateConflictError, match="附件文件") as unavailable:
        attachments.require_attachment_files(SCOPE.uid, [path])
    assert unavailable.value.code == "attachment_file_unavailable"


@pytest.mark.parametrize("phase", ["after-copy", "before-cleanup", "missing-parsed-source", "missing-parsed-resource"])
async def test_parsed_attachment_checks_original_before_losing_source(files_env, monkeypatch, phase):
    """仅原件被删除时可从完整来源恢复，部分来源缺失时保留现存的正式文档。"""
    factory, upload, submit, workdir = files_env
    file = await upload("parsed.pdf")
    source_path = f"tmp/chat_attachments/{SCOPE.uid}/{file['id']}/parsed/v1"
    original_path = f"/uploads/{file['id']}_parsed.pdf"
    markdown_path = f"/uploads/attachments/{file['id']}/document.md"
    (user_workspace_dir(SCOPE.uid) / source_path).mkdir(parents=True)
    source = Workdir.open_existing(SCOPE.uid, source_path)
    markdown = b"# Parsed\n![image](image.png)"
    source.copy_file_from_stream("/document.md", BytesIO(markdown), max_bytes=1024)
    source.copy_file_from_stream("/image.png", BytesIO(b"parsed image"), max_bytes=1024)
    async with factory() as db:
        draft = await db.get(AgentAttachment, file["id"])
        object_name = draft.object_name
        draft.parsed_source = f"{source_path}/document.md"
        await db.commit()

    with monkeypatch.context() as scoped:
        if phase == "after-copy":
            store = attachments._store_attachment

            async def remove_original_after_copy(**kwargs):
                record = await store(**kwargs)
                workdir.delete(original_path)
                return record

            scoped.setattr(attachments, "_store_attachment", remove_original_after_copy)
        else:

            async def skip_cleanup(**_kwargs):
                """模拟待清理来源的已提交附件。"""

            scoped.setattr(attachments, "cleanup_prepared_sources", skip_cleanup)
        receipt = await submit(file, "parsed-original-removed")
    if phase != "after-copy":
        workdir.delete(original_path)
        if phase == "missing-parsed-source":
            source.delete("/document.md")
        if phase == "missing-parsed-resource":
            source.delete("/image.png")
        await scheduler.recover_pending_dispatches()
    assert workdir.read_file(markdown_path, 1024) == markdown

    client = get_minio_client()
    bucket = client.KB_BUCKETS["documents"]
    if phase.startswith("missing-parsed-"):
        async with factory() as db:
            attachment = await db.get(AgentAttachment, file["id"])
            assert attachment.status == "ready" and attachment.object_name is None
            (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused = False
            await db.commit()
        await scheduler.recover_pending_dispatches()
        async with factory() as db:
            assert (await db.get(AgentInput, receipt["input_id"])).status == "consumed"
        assert await client.astat_file(bucket, object_name) is None
        assert workdir.read_file(markdown_path, 1024) == markdown
        return
    async with factory() as db:
        attachment = await db.get(AgentAttachment, file["id"])
        assert attachment.status == "preparing" and attachment.object_name == object_name
        assert attachment.parsed_source == f"{source_path}/document.md"
    assert await client.adownload_file(bucket, object_name) == b"original"

    await scheduler.recover_pending_dispatches()
    assert workdir.read_file(original_path, 1024) == b"original"
    assert workdir.read_file(markdown_path, 1024) == markdown
    assert await client.astat_file(bucket, object_name) is None


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
    source_path = f"{prefix}/parsed/v1"
    (user_workspace_dir(SCOPE.uid) / source_path).mkdir(parents=True)
    source = Workdir.open_existing(SCOPE.uid, source_path)
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


async def test_over_quota_prepares_accepted_attachment_and_defers_turn(files_env, monkeypatch):
    """超额时仍接收输入和准备附件，用量恢复后才绑定正式消息和 Run。"""
    from yuxi.modules.workspace.repositories.usage import save_usage

    factory, upload, submit, workdir = files_env
    file = await upload()
    threshold = 65536
    monkeypatch.setenv("WORKSPACE_QUOTA_BYTES", str(threshold))

    async with factory.begin() as db:
        session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        session.queue_paused = False
        await save_usage(db, SCOPE.uid, threshold, utc_now())

    receipt = await submit(file, "quota-before-turn")
    target = f"/uploads/{file['id']}_source.txt"
    assert workdir.read_file(target, 1024) == b"original"
    async with factory.begin() as db:
        item = await db.get(AgentInput, receipt["input_id"])
        attachment = await db.get(AgentAttachment, file["id"])
        assert item.status == "pending" and item.turn_id is None and item.consumed_run_id is None
        assert attachment.status == "ready" and attachment.message_id is None
        await save_usage(db, SCOPE.uid, threshold - 1, utc_now())

    await scheduler.recover_pending_dispatches()
    async with factory() as db:
        item = await db.get(AgentInput, receipt["input_id"])
        attachment = await db.get(AgentAttachment, file["id"])
        assert item.status == "consumed" and item.turn_id
        assert attachment.status == "ready" and attachment.object_name is None and attachment.message_id
        [message] = await AgentInputRepository(db).list_messages(item.id)
        assert message.id == attachment.message_id
