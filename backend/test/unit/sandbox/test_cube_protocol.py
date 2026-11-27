"""用固定协议帧与真实子进程验证执行协议与取消脚本。"""

import base64
import json
import os
import struct
import subprocess
import sys
import uuid
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cubesandbox import Sandbox

from yuxi.modules.agents.repositories.sandbox import SandboxRepository
from yuxi.modules.agents.runtime.sandbox import cube_provider
from yuxi.modules.agents.runtime.sandbox.context import sandbox_run_owner
from yuxi.modules.agents.runtime.sandbox.cube import _CANCEL_SCRIPT, MAX_FRAME_BYTES, CubeAdapter, Environment, connect_events
from yuxi.modules.agents.runtime.sandbox.cube_provider import CubeSandboxProvider
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import StateConflictError, UpstreamError

GUEST_PROGRAM = {
    "user": "service-account",
    "command": "/opt/service/check",
    "arguments": ["check", "an argument; $HOME"],
    "timeout": 5,
}


def frame(payload, flags=0):
    """构造显式审阅的测试协议帧。"""
    body = json.dumps(payload).encode()
    return bytes([flags]) + struct.pack(">I", len(body)) + body


def test_connect_frames_can_span_network_chunks():
    """网络分块不会丢失启动与终态事件。"""
    data = frame({"event": {"start": {"pid": 12}}}) + frame({"event": {"end": {"exitCode": 0}}})
    chunks = [data[:2], data[2:9], data[9:]]
    assert list(connect_events(iter(chunks))) == [{"start": {"pid": 12}}, {"end": {"exitCode": 0}}]


@pytest.mark.parametrize(
    "status,exit_code",
    [("exit status 0", 0), ("exit status 7", 7), ("signal: terminated", 143), ("signal: killed", 137)],
)
def test_connect_end_event_uses_cube_status_text(status, exit_code):
    """Cube 0.7.3 的终态文字转为 工具使用的 POSIX 退出码。"""
    data = frame({"event": {"end": {"exited": True, "status": status}}})
    assert list(connect_events(iter([data]))) == [{"end": {"exited": True, "status": status, "exitCode": exit_code}}]


@pytest.mark.parametrize(
    "data,reason",
    [
        (b"\0" + struct.pack(">I", MAX_FRAME_BYTES + 1), "超过预算"),
        (frame({"event": {}}, 1), "压缩"),
        (frame({"error": {"code": "internal"}}, 2), "协议错误"),
        (frame({"event": {}})[:-1], "不完整"),
    ],
)
def test_connect_rejects_invalid_stream(data, reason):
    """协议缺陷不会被当成有效空输出。"""
    with pytest.raises(RuntimeError, match=reason):
        list(connect_events(iter([data])))


@pytest.mark.parametrize(
    "stdin_data",
    [None, b"", b"private test input", bytes(range(256)) * 520],
    ids=["none", "empty", "without-newline", "multiple-chunks"],
)
def test_guest_program_sends_arguments_and_stdin_separately(monkeypatch, stdin_data):
    """直接执行参数数组，按顺序向已启动的进程发送私有数据及 EOF。"""
    environment = Environment("vm", "", "envd-test-token")
    requests = []
    control_requests = []

    def control(method, path, *, timeout):
        """来宾定位只使用读请求。"""
        control_requests.append((method, path))
        return {"sandboxID": "vm", "domain": "cube.example", "state": "running"}

    def envd(request: httpx.Request) -> httpx.Response:
        """用 Connect 进程事件重放来宾响应并保留收到的 HTTP 字节。"""
        requests.append(request)
        assert request.method == "POST"
        assert request.url.host == "49983-vm.cube.example"
        assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"service-account:").decode()
        assert request.headers["X-Access-Token"] == "envd-test-token"
        if request.url.path == "/process.Process/Start":
            assert request.headers["Connect-Timeout-Ms"] == "5000"
            assert json.loads(request.content[5:]) == {
                "process": {"cmd": "/opt/service/check", "args": ["check", "an argument; $HOME"], "cwd": "/"},
                "stdin": stdin_data is not None,
            }
            stream = (
                frame({"event": {"start": {"pid": 31}}})
                + frame({"event": {"data": {"stdout": "cmVzdWx0", "stderr": "aWdub3JlZA=="}}})
                + frame({"event": {"end": {"exited": True, "status": "exit status 7"}}})
                + frame({}, 2)
            )
            return httpx.Response(200, stream=httpx.ByteStream(stream))
        return httpx.Response(200)

    adapter = CubeAdapter()
    monkeypatch.setattr(adapter, "_control", control)
    monkeypatch.setattr(Sandbox, "_build_data_client", lambda self: httpx.Client(transport=httpx.MockTransport(envd)))
    assert adapter.run_guest_program(environment, **GUEST_PROGRAM, stdin_data=stdin_data) == (7, b"result")
    assert control_requests == [("GET", "/sandboxes/vm")]
    assert requests[0].url.path == "/process.Process/Start"
    if stdin_data is None:
        assert len(requests) == 1
        return

    assert requests[-1].url.path == "/process.Process/CloseStdin"
    assert json.loads(requests[-1].content) == {"process": {"pid": 31}}
    chunks = []
    for request in requests[1:-1]:
        assert request.url.path == "/process.Process/SendInput"
        payload = json.loads(request.content)
        assert payload["process"] == {"pid": 31}
        chunks.append(base64.b64decode(payload["input"]["stdin"], validate=True))
    assert all(0 < len(chunk) <= 65536 for chunk in chunks)
    assert b"".join(chunks) == stdin_data


@pytest.mark.parametrize(
    ("rpc", "status", "error", "ended", "failure"),
    [
        ("SendInput", 404, {"code": "not_found", "message": "process with pid 31 not found"}, True, None),
        ("CloseStdin", 404, {"code": "not_found", "message": "process with pid 31 not found"}, True, None),
        (
            "SendInput",
            500,
            {
                "code": "internal",
                "message": "error writing to stdin: error writing to stdin of process '31': write |1: file already closed",
            },
            True,
            None,
        ),
        (
            "SendInput",
            500,
            {"code": "internal", "message": "error writing to stdin: error writing to stdin of process '31': write |1: broken pipe"},
            True,
            None,
        ),
        ("CloseStdin", 500, {"code": "unknown", "message": "error closing stdin: close |1: file already closed"}, True, None),
        ("CloseStdin", 404, {"code": "not_found", "message": "process with pid 31 not found"}, False, RuntimeError),
        ("CloseStdin", 404, {"code": "not_found", "message": "unknown procedure"}, True, httpx.HTTPStatusError),
        ("CloseStdin", 401, {"code": "unauthenticated"}, True, httpx.HTTPStatusError),
        ("SendInput", 500, {"code": "internal", "message": "unrelated write error"}, True, httpx.HTTPStatusError),
        ("CloseStdin", 500, {"code": "unknown", "message": "unrelated close error"}, True, httpx.HTTPStatusError),
    ],
    ids=[
        "gone-before-write",
        "gone-before-close",
        "closed-pipe",
        "broken-pipe",
        "already-closed",
        "no-terminal-event",
        "unknown-rpc",
        "auth",
        "other-write-error",
        "other-close-error",
    ],
)
def test_guest_stdin_uses_terminal_result_only_for_expected_pipe_errors(monkeypatch, rpc, status, error, ended, failure):
    """进程提前停止读取时等待原流终态，认证和其他请求错误仍然暴露。"""
    adapter = CubeAdapter()
    monkeypatch.setattr(adapter, "_control", lambda *args, **kwargs: {"sandboxID": "vm", "domain": "cube.example", "state": "running"})

    def envd(request: httpx.Request) -> httpx.Response:
        """重放 EOF 竞态中的独立一元响应和 Start 终态。"""
        if request.url.path == "/process.Process/Start":
            stream = frame({"event": {"start": {"pid": 31}}})
            if ended:
                stream += frame({"event": {"data": {"stdout": "cmVzdWx0"}}}) + frame({"event": {"end": {"exitCode": 23}}})
            return httpx.Response(200, stream=httpx.ByteStream(stream + frame({}, 2)))
        if request.url.path == f"/process.Process/{rpc}":
            return httpx.Response(status, json=error)
        return httpx.Response(200)

    monkeypatch.setattr(Sandbox, "_build_data_client", lambda self: httpx.Client(transport=httpx.MockTransport(envd)))
    environment = Environment("vm", "", "envd-test-token")
    if failure is None:
        assert adapter.run_guest_program(environment, **GUEST_PROGRAM, stdin_data=b"unread") == (23, b"result")
    else:
        match = "未取得终态" if not ended else f"{status}"
        with pytest.raises(failure, match=match):
            adapter.run_guest_program(environment, **GUEST_PROGRAM, stdin_data=b"unread")


@pytest.mark.parametrize(
    ("events", "error"),
    [
        ([{"data": {"stdout": "!"}}], "输出编码无效"),
        ([{"data": {"stdout": base64.b64encode(b"a" * 4096)}}, {"data": {"stdout": "YQ=="}}], "输出超出限制"),
        ([{"end": {"exitCode": "0"}}], "退出码无效"),
        ([], "进程退出未确认"),
    ],
)
def test_guest_program_rejects_unconfirmed_process_output(monkeypatch, events, error):
    """来宾输出必须可解码、在预算内且带有效终态。"""
    adapter = CubeAdapter()
    monkeypatch.setattr(adapter, "_running_sandbox", lambda environment: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(adapter, "_stream_process", lambda *args, **kwargs: (event for event in events))
    with pytest.raises(RuntimeError, match=error):
        adapter.run_guest_program(Environment("vm", "", ""), **GUEST_PROGRAM)


@pytest.mark.parametrize(
    ("connection", "row", "code"),
    [
        (None, None, "sandbox_busy"),
        (object(), None, "sandbox_changed"),
        (object(), {"generation": 8, "state": "running", "sandbox_id": "vm-8"}, "sandbox_changed"),
        (object(), {"generation": 7, "state": "paused", "sandbox_id": "vm"}, "sandbox_changed"),
        (object(), {"generation": 7, "state": "running", "sandbox_id": None}, "sandbox_changed"),
    ],
)
def test_guest_program_checks_lifecycle_before_guest_access(connection, row, code):
    """持锁且只有目标代次运行时才可向执行节点发请求。"""
    repository = SimpleNamespace(user_lock=lambda uid, wait: nullcontext(connection), environment=lambda db, uid: row)
    provider = CubeSandboxProvider(adapter=SimpleNamespace(), repository=repository)
    with pytest.raises(StateConflictError) as failure:
        provider.run_guest_program("uid", 7, **GUEST_PROGRAM)
    assert failure.value.code == code


@pytest.mark.parametrize(("touch_idle", "exit_code", "extend_idle"), [(False, 0, False), (True, 0, True), (True, 4, False)])
def test_guest_program_holds_lifecycle_lock_and_only_touches_idle_on_explicit_success(touch_idle, exit_code, extend_idle):
    """密集查询和失败动作不拖延休眠，成功动作在相同代次刷新时间。"""
    last_active = utc_now() - timedelta(minutes=1)
    row = {
        "generation": 7,
        "state": "running",
        "sandbox_id": "vm",
        "traffic_token": "",
        "envd_token": "",
        "last_active_at": last_active,
    }
    persisted = row.copy()
    locked = False

    @contextmanager
    def user_lock(uid, *, wait):
        """模拟完整的用户锁生命周期。"""
        nonlocal locked
        assert uid == "uid" and not wait
        locked = True
        try:
            yield object()
        finally:
            locked = False

    def execute(environment, **kwargs):
        """模拟必须在用户锁内完成的 envd 进程。"""
        assert locked and environment.sandbox_id == "vm"
        return exit_code, b"result"

    def save_environment(connection, updated):
        """仅在用户锁内写入持久时间。"""
        assert locked
        persisted.update(updated)

    repository = SimpleNamespace(user_lock=user_lock, environment=lambda db, uid: row, save_environment=save_environment)
    provider = CubeSandboxProvider(adapter=SimpleNamespace(run_guest_program=execute), repository=repository)
    assert provider.run_guest_program("uid", 7, **GUEST_PROGRAM, touch_idle=touch_idle) == (exit_code, b"result")
    assert (persisted["last_active_at"] > last_active) is extend_idle


def test_preparing_expected_instance_rejects_replacement():
    """准备指定实例时，VM 已更换则在访问执行节点前拒绝。"""
    repository = SimpleNamespace(
        user_lock=lambda uid, wait: nullcontext(object()),
        environment=lambda db, uid: {"generation": 8, "sandbox_id": "new-vm"},
    )
    provider = CubeSandboxProvider(adapter=SimpleNamespace(), repository=repository)
    with pytest.raises(StateConflictError) as failure:
        provider.prepare_instance("uid", expected=(7, "old-vm"))
    assert failure.value.code == "sandbox_changed"


@pytest.mark.parametrize("initial_state", ["running", "paused"])
def test_cube_pause_uses_only_native_control_plane(monkeypatch, initial_state):
    """已休眠 VM 幂等返回，运行中 VM 通过原生暂停请求改变状态。"""
    adapter = CubeAdapter()
    state = initial_state

    def control(method, path, *, timeout):
        """只允许原生暂停请求变更实例状态。"""
        nonlocal state
        assert state == "running"
        assert (method, path) == ("POST", "/sandboxes/vm/pause")
        state = "paused"
        return {}

    monkeypatch.setattr(adapter, "_control", control)
    monkeypatch.setattr(adapter, "state", lambda reference: state)
    assert adapter.pause(Environment("vm", "", "")) is (initial_state == "running")
    assert state == "paused"


@pytest.mark.parametrize("prepare", [True, False])
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"status": "cancel_requested"}, "已取消"),
        ({"worker_id": "different-attempt"}, "已改变"),
        ({"lease_valid": False}, "已过期"),
    ],
)
def test_revoked_worker_cannot_prepare_vm_or_execute_guest_program(monkeypatch, prepare, change, message):
    """晚到的准备或来宾执行必须在用户锁内拒绝不可用的 Run 租约。"""
    repository = SandboxRepository()
    row = {"uid": "uid", "generation": 2, "sandbox_id": "vm", "state": "running"}
    run = {"uid": "uid", "status": "running", "worker_id": "worker", "lease_valid": True, **change}
    connection = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchone=lambda: run))
    monkeypatch.setattr(repository, "user_lock", lambda uid, wait: nullcontext(connection))
    monkeypatch.setattr(repository, "environment", lambda db, uid: row)
    provider = CubeSandboxProvider(adapter=SimpleNamespace(), repository=repository)
    owner = sandbox_run_owner.set(("run", "worker"))
    try:
        with pytest.raises(StateConflictError, match=message) as failure:
            if prepare:
                provider.prepare_instance("uid", expected=(2, "vm"))
            else:
                provider.run_guest_program("uid", 2, **GUEST_PROGRAM)
        assert failure.value.code == "lease_lost"
    finally:
        sandbox_run_owner.reset(owner)


@pytest.mark.parametrize("from_worker", [True, False])
def test_prepare_instance_rechecks_owner_after_cube_lookup(monkeypatch, from_worker):
    """控制面查询期间撤权不唤醒或写入失败，普通准备可恢复原实例。"""
    repository = SandboxRepository()
    paused_row = {
        "generation": 2,
        "sandbox_id": "vm",
        "state": "paused",
        "traffic_token": "",
        "envd_token": "",
        "error": None,
        "paused_at": utc_now(),
    }
    row = paused_row.copy()
    run = {"uid": "uid", "status": "running", "worker_id": "worker", "lease_valid": True}
    saved_states = []
    control_requests = []
    connection = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchone=lambda: run))
    monkeypatch.setattr(repository, "user_lock", lambda uid, wait: nullcontext(connection))
    monkeypatch.setattr(repository, "environment", lambda db, uid: row)
    monkeypatch.setattr(repository, "save_environment", lambda db, updated: saved_states.append(updated["state"]))

    def state(environment):
        """收到运行状态时，另一事务已撤销 Worker。"""
        run["status"] = "cancel_requested"
        return "paused"

    def control(method, path, *, timeout, payload):
        """观察真实恢复调用使用的控制请求。"""
        control_requests.append((method, path))
        return {"sandboxID": "vm"}

    adapter = CubeAdapter()
    monkeypatch.setattr(adapter, "state", state)
    monkeypatch.setattr(adapter, "_control", control)
    provider = CubeSandboxProvider(adapter=adapter, repository=repository)
    owner = sandbox_run_owner.set(("run", "worker") if from_worker else None)
    try:
        if from_worker:
            with pytest.raises(StateConflictError) as failure:
                provider.prepare_instance("uid", expected=(2, "vm"))
            assert failure.value.code == "lease_lost"
            assert not control_requests and not saved_states and row == paused_row
        else:
            assert provider.prepare_instance("uid", expected=(2, "vm")) == (2, "vm")
            assert control_requests == [("POST", "/sandboxes/vm/connect")]
            assert saved_states == ["resuming", "running"]
            assert row["paused_at"] is None and row["error"] is None
    finally:
        sandbox_run_owner.reset(owner)


def test_cube_reobserved_running_after_lost_resume_response_registers_workspace(monkeypatch, tmp_path):
    """Cube 已接收唤醒但响应丢失后，再取得运行实例时仍补登用量。"""
    from yuxi.modules.workspace.usage_changes import acknowledge_workspace_change, pending_workspace_changes

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    row = {"sandbox_id": "vm", "state": "paused", "traffic_token": "", "envd_token": "", "error": None}
    remote = {"state": "paused"}

    def resume(_):
        """唤醒生效，API 客户端没有收到确认。"""
        remote["state"] = "running"
        raise TimeoutError("fixture lost response")

    repository = SimpleNamespace(user_lock=lambda _uid: nullcontext(None), environment=lambda *_: row, save_environment=lambda *_: None)
    adapter = SimpleNamespace(state=lambda _: remote["state"], connect=resume)
    provider = CubeSandboxProvider(adapter=adapter, repository=repository)
    with pytest.raises(UpstreamError, match="无法恢复"):
        provider.get("uid")
    assert row["state"] == "paused" and row["error"]
    [first] = pending_workspace_changes()
    acknowledge_workspace_change(first)
    assert pending_workspace_changes() == []
    assert provider.get("uid").sandbox_id == "vm"
    assert row["state"] == "running" and row["error"] is None
    [following] = pending_workspace_changes()
    assert following.uid == "uid" and following.generation != first.generation


def test_preparing_new_instance_rechecks_owner_after_mount_setup(monkeypatch, tmp_path):
    """准备共享目录期间撤销的租约不会留下创建记录或发起 VM 创建。"""
    repository = SandboxRepository()
    run = {"uid": "uid", "status": "running", "worker_id": "worker", "lease_valid": True}
    connection = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchone=lambda: run))
    monkeypatch.setattr(repository, "user_lock", lambda uid, wait: nullcontext(connection))
    monkeypatch.setattr(repository, "environment", lambda db, uid: None)
    monkeypatch.setattr(repository, "save_environment", lambda *args: pytest.fail("撤权后写入了创建状态"))
    monkeypatch.setattr(cube_provider, "get_user_skills_root_dir", lambda uid: tmp_path / "skills")
    monkeypatch.setenv("CUBE_NODE_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CUBE_NODE_SKILL_PROJECTION_DIR", str(tmp_path))

    def ensure_workspace(uid):
        """共享目录准备完成前使租约过期。"""
        run["lease_valid"] = False

    monkeypatch.setattr(cube_provider, "ensure_user_workspace", ensure_workspace)
    adapter = SimpleNamespace(create=lambda **kwargs: pytest.fail("撤权后仍创建 VM"))
    provider = CubeSandboxProvider(adapter=adapter, repository=repository)
    owner = sandbox_run_owner.set(("run", "worker"))
    try:
        with pytest.raises(StateConflictError) as failure:
            provider.prepare_instance("uid")
        assert failure.value.code == "lease_lost"
    finally:
        sandbox_run_owner.reset(owner)


def test_guest_program_rechecks_execution_right_after_cube_lookup(monkeypatch):
    """控制面响应前撤销的租约不能发起 envd 进程，包括私有 stdin 动作。"""
    repository = SandboxRepository()
    row = {"generation": 2, "sandbox_id": "vm", "state": "running", "traffic_token": "", "envd_token": ""}
    run = {"uid": "uid", "status": "running", "worker_id": "worker", "lease_valid": True}
    connection = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchone=lambda: run))
    monkeypatch.setattr(repository, "user_lock", lambda uid, wait: nullcontext(connection))
    monkeypatch.setattr(repository, "environment", lambda db, uid: row)

    def control(method, path, *, timeout):
        """响应 VM 查询前模拟独立事务取消 Run。"""
        assert method == "GET" and path == "/sandboxes/vm"
        run["status"] = "cancel_requested"
        return {"sandboxID": "vm", "domain": "cube.example", "state": "running"}

    adapter = CubeAdapter()
    monkeypatch.setattr(adapter, "_control", control)
    transport = httpx.MockTransport(lambda request: pytest.fail("撤销租约后仍访问 envd"))
    monkeypatch.setattr(Sandbox, "_build_data_client", lambda self: httpx.Client(transport=transport))
    provider = CubeSandboxProvider(adapter=adapter, repository=repository)
    owner = sandbox_run_owner.set(("run", "worker"))
    try:
        with pytest.raises(StateConflictError) as failure:
            provider.run_guest_program("uid", 2, **GUEST_PROGRAM, stdin_data=b"private test input\n")
        assert failure.value.code == "lease_lost"
        assert run["status"] == "cancel_requested"
    finally:
        sandbox_run_owner.reset(owner)


def test_guest_program_redacts_transport_failure():
    """传输异常以稳定的错误表达，远端错误正文不进入响应。"""
    row = {"generation": 2, "sandbox_id": "vm", "state": "running", "traffic_token": "", "envd_token": ""}
    repository = SimpleNamespace(user_lock=lambda uid, wait: nullcontext(object()), environment=lambda db, uid: row)

    def unavailable(environment, **kwargs):
        """模拟包含私有内容的传输异常。"""
        raise RuntimeError("private test detail")

    provider = CubeSandboxProvider(adapter=SimpleNamespace(run_guest_program=unavailable), repository=repository)
    with pytest.raises(UpstreamError) as failure:
        provider.run_guest_program("uid", 2, **GUEST_PROGRAM)
    assert failure.value.code == "sandbox_service_unavailable"
    assert "private test detail" not in str(failure.value)


@pytest.mark.parametrize("actual", [(3, "running"), (2, "running"), (3, "failed")])
def test_failed_environment_recovery_cannot_destroy_another_vm(actual):
    """另一个标签或管理员已恢复环境时，旧确认不会再次删除。"""
    generation, state = actual
    repository = SimpleNamespace(
        user_lock=lambda uid: nullcontext(object()),
        environment=lambda db, uid: {"generation": generation, "state": state},
        active_runs=lambda db, uid: [],
        commands=lambda db, uid: [],
    )
    provider = CubeSandboxProvider(adapter=SimpleNamespace(), repository=repository)
    with pytest.raises(StateConflictError) as failure:
        provider.rebuild("uid", failed_generation=2)
    assert failure.value.code == "sandbox_changed"


@pytest.mark.parametrize(
    "detail, error, message",
    [
        ({"sandboxID": "fixture", "state": "paused"}, StateConflictError, "未运行"),
        ({"sandboxID": "different", "state": "running"}, RuntimeError, "身份未确认"),
    ],
)
def test_guest_program_transport_rejects_incorrect_live_target(monkeypatch, detail, error, message):
    """控制面报告休眠或其他实例时，禁止恢复及 envd 调用。"""
    adapter = CubeAdapter()

    def control(method, path, *, timeout):
        """拒绝会恢复 VM 的控制面请求。"""
        assert method == "GET" and path == "/sandboxes/fixture"
        return detail

    monkeypatch.setattr(adapter, "_control", control)
    with pytest.raises(error, match=message):
        adapter.run_guest_program(Environment("fixture", "", ""), **GUEST_PROGRAM)


@pytest.mark.parametrize(("persisted_state", "requires_rebuild"), [("running", False), ("failed", True), ("creating", True)])
def test_cube_status_keeps_recovery_distinct_from_a_read_failure(persisted_state, requires_rebuild):
    """已失败的创建可重建，临时无法查询正在运行的 VM 只提示刷新。"""
    row = {
        "generation": 1,
        "state": persisted_state,
        "error": None,
        "sandbox_id": "vm" if persisted_state == "running" else None,
        "traffic_token": "",
        "envd_token": "",
        "last_active_at": utc_now(),
    }
    repository = SimpleNamespace(
        connect=lambda: nullcontext(object()),
        user_lock=lambda uid, wait: nullcontext(object()),
        environment=lambda db, uid: row,
        active_runs=lambda db, uid: [],
        commands=lambda db, uid: [],
    )

    def unavailable(environment):
        """只在有运行实例时模拟控制面暂时不可用。"""
        assert persisted_state == "running"
        raise OSError("temporary read failure")

    status = CubeSandboxProvider(adapter=SimpleNamespace(state=unavailable), repository=repository).status("uid")
    assert status["state"] == "failed"
    assert status["rebuild_required"] is requires_rebuild


def _run_cancel_script(env: dict) -> subprocess.CompletedProcess:
    """以独立进程执行取消脚本，避免在测试进程内发信号。"""
    return subprocess.run([sys.executable, "-c", _CANCEL_SCRIPT], env=env, capture_output=True, text=True, timeout=30)


def _tombstone(execution: str) -> Path:
    return Path("/tmp") / f".yuxi-cancel-{execution}"


def test_cancel_script_removes_tombstone_after_confirmed_group_stop():
    """恢复泄漏缺陷：已启动命令组取消确认终止后必须移除 tombstone。"""
    execution = "unit-" + uuid.uuid4().hex
    tombstone = _tombstone(execution)
    child = subprocess.Popen(["sleep", "30"], env={**os.environ, "YUXI_EXECUTION_ID": execution}, start_new_session=True)
    try:
        result = _run_cancel_script(
            {"YUXI_PGID": str(child.pid), "YUXI_EXECUTION": execution, "YUXI_TERMINAL": "0", "PATH": os.environ["PATH"]}
        )
        assert result.returncode == 0, result.stderr
        assert child.wait(timeout=5) != 0
        assert not tombstone.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(5)
        tombstone.unlink(missing_ok=True)


def test_cancel_script_removes_tombstone_for_recorded_group_that_already_ended():
    """start 事件已登记 pgid 的命令结束后取消，tombstone 不再保留。"""
    execution = "unit-" + uuid.uuid4().hex
    tombstone = _tombstone(execution)
    try:
        result = _run_cancel_script({"YUXI_PGID": "999999", "YUXI_EXECUTION": execution, "YUXI_TERMINAL": "0", "PATH": os.environ["PATH"]})
        assert result.returncode == 0, result.stderr
        assert not tombstone.exists()
    finally:
        tombstone.unlink(missing_ok=True)


def test_cancel_script_removes_tombstone_on_confirmed_terminal():
    """取得真实 end 的取消保持移除 tombstone。"""
    execution = "unit-" + uuid.uuid4().hex
    tombstone = _tombstone(execution)
    try:
        result = _run_cancel_script({"YUXI_PGID": "", "YUXI_EXECUTION": execution, "YUXI_TERMINAL": "1", "PATH": os.environ["PATH"]})
        assert result.returncode == 0, result.stderr
        assert not tombstone.exists()
    finally:
        tombstone.unlink(missing_ok=True)


def test_cancel_script_keeps_tombstone_when_start_cannot_be_confirmed():
    """无法证明 wrapper 已启动时保留 tombstone 拦截可能迟到的启动。"""
    execution = "unit-" + uuid.uuid4().hex
    tombstone = _tombstone(execution)
    try:
        result = _run_cancel_script({"YUXI_PGID": "", "YUXI_EXECUTION": execution, "YUXI_TERMINAL": "0", "PATH": os.environ["PATH"]})
        assert result.returncode == 0, result.stderr
        assert tombstone.exists()
    finally:
        tombstone.unlink(missing_ok=True)
