"""Cube 生命周期、普通用户命令和受信来宾程序适配。"""

import base64
import json
import os
import re
import secrets
import struct
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass, field

import httpx
from cubesandbox import Config, Sandbox

from yuxi.modules.workspace.runtime_paths import WORKSPACE_PATH
from yuxi.shared.errors import StateConflictError

USER = "user"
MAX_FRAME_BYTES = 1024 * 1024

# 仅终止带本次执行标识的命令组；由普通账号核对进程组和继承的执行标识，再发送信号。
# PID 会复用，不能按 PID 直接终止。tombstone 拦截"取消先于启动"的竞态：pgid 来自
# start 事件、扫描到带执行标识的进程或取得真实 end，都证明 wrapper 已启动，进程组
# 确认终止后不存在更晚的启动可拦截，此时移除 tombstone；无法证明启动时保留。
_CANCEL_SCRIPT = """import os,signal,time
from pathlib import Path
pgid = int(os.environ["YUXI_PGID"]) if os.environ["YUXI_PGID"] else None
marker = ("YUXI_EXECUTION_ID=" + os.environ["YUXI_EXECUTION"]).encode()
matched = False
fd = os.open("/tmp/.yuxi-cancel-" + os.environ["YUXI_EXECUTION"], os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600)
os.close(fd)
verified_groups=set()
def members():
    global matched
    groups=verified_groups
    pids=[]
    for path in Path("/proc").iterdir():
        if not path.name.isdigit(): continue
        try:
            pid=int(path.name)
            group=os.getpgid(pid)
            if pgid is not None and group!=pgid: continue
            fields=(path/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0]=='Z': continue
            pids.append((pid,group))
            if marker in (path/'environ').read_bytes().split(b"\\0"):
                matched = True
                if group==pid or group==pgid: groups.add(group)
                elif pgid is None: groups.add(pid)
        except (OSError,ValueError): continue
    found=[]
    for pid,group in pids:
        if group in groups: found.append((group,True))
        elif pid in groups: found.append((pid,False))
        elif pgid is not None and group==pgid:
            # leader 标识已丢失但组仍有进程，不能把未知归属当成清理成功。
            raise RuntimeError('process group owner cannot be confirmed')
    return found
for target, is_group in set(members()):
    try:
        (os.killpg if is_group else os.kill)(target, signal.SIGTERM)
    except ProcessLookupError:
        pass
if members():
    time.sleep(0.3)
    if members():
        for target, is_group in set(members()):
            try:
                (os.killpg if is_group else os.kill)(target, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.1)
if members():
    raise SystemExit("目标命令组未终止")
if os.environ['YUXI_TERMINAL']=='1' or os.environ['YUXI_PGID'] or matched:
    Path('/tmp/.yuxi-cancel-'+os.environ['YUXI_EXECUTION']).unlink(missing_ok=True)
"""


@dataclass
class Environment:
    """保存实例引用与控制侧凭据，不对浏览器公开令牌。"""

    sandbox_id: str
    traffic_token: str = field(repr=False)
    envd_token: str = field(repr=False)


def connect_events(chunks: Iterator[bytes]) -> Iterator[dict]:
    """解析有界 Connect JSON 帧，拒绝断流和协议错误。"""
    buffer = bytearray()
    for chunk in chunks:
        buffer.extend(chunk)
        while len(buffer) >= 5:
            flags, size = buffer[0], struct.unpack(">I", buffer[1:5])[0]
            if size > MAX_FRAME_BYTES:
                raise RuntimeError("执行协议帧超过预算")
            if len(buffer) < size + 5:
                break
            body = bytes(buffer[5 : size + 5])
            del buffer[: size + 5]
            if flags & 1:
                raise RuntimeError("不支持压缩执行帧")
            payload = json.loads(body)
            if flags & 2:
                if payload.get("error"):
                    raise RuntimeError("远端执行流返回协议错误")
            else:
                event = payload.get("event", {})
                if "end" in event and "exitCode" not in event["end"]:
                    event["end"]["exitCode"] = _exit_code(event["end"])
                yield event
    if buffer:
        raise RuntimeError("执行流包含不完整帧")


def _exit_code(event: dict) -> int:
    """把 envd 的终态文字转换为稳定的 POSIX 退出码。"""
    status = event.get("status", "")
    match = re.fullmatch(r"exit status (\d+)", status)
    if match:
        return int(match.group(1))
    signal_codes = {"signal: terminated": 143, "signal: killed": 137}
    if status in signal_codes:
        return signal_codes[status]
    raise RuntimeError("Cube 执行流终态无法转换为退出码")


class CubeAdapter:
    """连接 Cube 控制面及沙盒内 envd。"""

    def __init__(self, config: Config | None = None):
        """装配固定版本 SDK，固定控制面和代理地址。"""
        self.config = config or Config(
            api_url=os.environ.get("CUBE_API_URL", "http://127.0.0.1:13000"),
            proxy_node_ip=os.environ.get("CUBE_PROXY_NODE_IP", "127.0.0.1"),
            proxy_port=int(os.environ.get("CUBE_PROXY_PORT_HTTP", "18080")),
            template_id=os.environ.get("CUBE_TEMPLATE_ID", "yuxi-user"),
        )

    def _control(self, method: str, path: str, *, timeout: float, payload: dict | None = None) -> dict:
        """控制请求设置明确超时，不继承平台 HTTP 代理。"""
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.request(method, f"{self.config.api_url}{path}", headers=self._api_headers(), json=payload)
            response.raise_for_status()
            return response.json() if response.content else {}

    def health(self) -> dict:
        """确认控制面、模板与 HTTP 代理在线，不创建或唤醒沙盒。"""
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.get(f"{self.config.api_url}/health", headers=self._api_headers())
            response.raise_for_status()
            template = client.get(f"{self.config.api_url}/templates/aliases/{self.config.template_id}", headers=self._api_headers())
            template.raise_for_status()
            try:
                proxy = client.get(f"http://{self.config.proxy_node_ip}:{self.config.proxy_port}/")
            except httpx.HTTPError as exc:
                raise RuntimeError("CubeProxy 数据代理未能响应 HTTP 请求") from exc
            if proxy.status_code != 400 or proxy.json().get("error") != "bad request":
                raise RuntimeError(f"CubeProxy 数据代理健康探测返回异常 HTTP {proxy.status_code}")
            return response.json()

    def create(
        self,
        *,
        mounts: list[dict],
        metadata: dict[str, str],
        envd_token: str,
        on_created: Callable[[Environment], None],
    ) -> Environment:
        """挂载独立 Workspace 并在任何用户代码运行前保护 envd。"""
        data = self._control(
            "POST",
            "/sandboxes",
            timeout=120,
            payload={
                "templateID": self.config.template_id,
                "timeout": -1,
                "lifecycle": {"onTimeout": "pause", "autoResume": False},
                "network": {
                    "allowPublicTraffic": True,
                    "denyOut": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10"],
                },
                "metadata": {
                    "yuxi-sandbox": "true",
                    **metadata,
                    "host-mount": json.dumps(mounts),
                },
            },
        )
        sandbox = Sandbox(data, config=self.config)
        environment = Environment(sandbox.sandbox_id, sandbox.traffic_access_token or "", envd_token)
        try:
            on_created(environment)
            client = sandbox._build_data_client()
            with client:
                response = client.post(
                    f"http://{sandbox.get_host(49983)}/init",
                    json={"accessToken": environment.envd_token, "defaultUser": USER, "defaultWorkdir": WORKSPACE_PATH},
                    timeout=30,
                )
                response.raise_for_status()
            self._install_tokens(sandbox, environment)
            result = sandbox.commands.run("id -u", user=USER, cwd=WORKSPACE_PATH, timeout=10)
            if result.exit_code != 0 or not result.stdout.strip().isdigit() or int(result.stdout.strip()) == 0:
                raise RuntimeError("模板普通用户不可用")
            return environment
        finally:
            sandbox.close()

    def connect(self, environment: Environment) -> Sandbox:
        """重连或唤醒原实例，恢复控制侧凭据。"""
        data = self._control("POST", f"/sandboxes/{environment.sandbox_id}/connect", timeout=60, payload={"timeout": -1})
        sandbox = Sandbox(data, config=self.config)
        self._install_tokens(sandbox, environment)
        return sandbox

    def state(self, environment: Environment) -> str:
        """读取状态，不调用会自动恢复的 connect。"""
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.get(f"{self.config.api_url}/sandboxes/{environment.sandbox_id}", headers=self._api_headers())
            response.raise_for_status()
            return response.json().get("state", "unknown")

    def vm_stats(self) -> dict[str, int]:
        """只读控制面全部实例，休眠 VM 计入总数且不会被唤醒。"""
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.get(f"{self.config.api_url}/v2/sandboxes", headers=self._api_headers())
            response.raise_for_status()
            instances = response.json()
        if not isinstance(instances, list) or any(
            not isinstance(instance, dict) or not isinstance(instance.get("state"), str) for instance in instances
        ):
            raise ValueError("Cube 实例列表格式异常")
        return {
            "active_vms": sum(instance["state"].lower() == "running" for instance in instances),
            "total_vms": len(instances),
        }

    def pause(self, environment: Environment) -> bool:
        """暂停原实例；返回是否由本次操作确认休眠，已暂停返回 False。"""
        if self.state(environment).lower() == "paused":
            return False
        self._control("POST", f"/sandboxes/{environment.sandbox_id}/pause", timeout=60)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.state(environment).lower() == "paused":
                return True
            time.sleep(0.5)
        raise TimeoutError("Cube 未在 60 秒内完成休眠")

    def destroy(self, environment: Environment) -> None:
        """删除旧 VM，Workspace 由独立目录拥有。"""
        with httpx.Client(timeout=60, trust_env=False) as client:
            response = client.delete(f"{self.config.api_url}/sandboxes/{environment.sandbox_id}", headers=self._api_headers())
            if response.status_code != 404:
                response.raise_for_status()

    def run(self, environment: Environment, command: str, cwd: str = WORKSPACE_PATH, timeout: float = 30):
        """执行控制侧探针，执行身份始终固定普通用户。"""
        sandbox = self.connect(environment)
        try:
            return sandbox.commands.run(command, user=USER, cwd=cwd, timeout=timeout)
        finally:
            sandbox.close()

    def stream(
        self,
        environment: Environment,
        command: str,
        cwd: str,
        execution_id: str,
        envs: dict[str, str] | None = None,
        timeout: float = 180,
        stdin_data: bytes | None = None,
        python: bool = False,
    ) -> Iterator[dict]:
        """直接读取 envd 流，为每条命令创建独立进程组。"""
        wrapper = """import os,sys,subprocess,signal,time
from pathlib import Path
os.setsid()
if Path('/tmp/.yuxi-cancel-'+os.environ['YUXI_EXECUTION_ID']).exists():
    sys.exit(143)
# leader 保留执行标识；子命令清空环境也不能脱离所属进程组。
process=subprocess.Popen(sys.argv[1:])
code=process.wait()
def followers():
    found=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name)==os.getpid(): continue
        try:
            if os.getpgid(int(p.name))==os.getpid():
                stat=(p/'stat').read_text().rsplit(')',1)[1].split()
                if stat[0]!='Z': found.append(int(p.name))
        except (OSError,ValueError): pass
    return found
for sig in (signal.SIGTERM,signal.SIGKILL):
    for pid in followers():
        try: os.kill(pid,sig)
        except ProcessLookupError: pass
    time.sleep(.1)
if followers(): raise RuntimeError('ordinary child processes remain')
sys.exit(code if code>=0 else 128-code)
"""
        payload = {
            "process": {
                "cmd": "python3",
                "args": ["-c", wrapper, "/usr/bin/python3" if python else "/bin/bash", "-c" if python else "-lc", command],
                "cwd": cwd,
                "envs": {**(envs or {}), "YUXI_EXECUTION_ID": execution_id},
            },
            "stdin": stdin_data is not None,
        }
        with closing(self.connect(environment)) as sandbox:
            yield from self._stream_process(sandbox, environment, USER, payload, timeout, stdin_data)

    def run_guest_program(
        self,
        environment: Environment,
        *,
        user: str,
        command: str,
        arguments: list[str],
        timeout: float,
        stdin_data: bytes | None = None,
        before_start: Callable[[], None] | None = None,
    ) -> tuple[int, bytes]:
        """通过 envd 执行平台指定的程序，私有输入发送完毕后关闭 stdin。"""
        payload = {"process": {"cmd": command, "args": arguments, "cwd": "/"}, "stdin": stdin_data is not None}
        exit_code = None
        stdout = bytearray()
        with (
            closing(self._running_sandbox(environment)) as sandbox,
            closing(
                self._stream_process(
                    sandbox, environment, user, payload, timeout, stdin_data, hard_deadline=True, before_start=before_start
                )
            ) as events,
        ):
            for event in events:
                if (chunk := event.get("data", {}).get("stdout")) is not None:
                    try:
                        stdout.extend(base64.b64decode(chunk, validate=True))
                    except (ValueError, TypeError):
                        raise RuntimeError("Cube 来宾程序输出编码无效") from None
                    if len(stdout) > 4096:
                        raise RuntimeError("Cube 来宾程序输出超出限制")
                if "end" in event:
                    code = event["end"]["exitCode"]
                    if type(code) is not int or not 0 <= code <= 255:
                        raise RuntimeError("Cube 来宾程序退出码无效")
                    exit_code = code
        if exit_code is None:
            raise RuntimeError("Cube 进程退出未确认")
        return exit_code, bytes(stdout)

    def cancel(self, environment: Environment, pgid: int | None, execution_id: str, *, terminal_confirmed: bool = False) -> None:
        """仅终止带本次标识的命令组，保留同用户其他任务。"""
        # 清理不能读取用户 shell 启动配置，否则阻塞的 profile 会阻断取消。
        ended = False
        for event in self.stream(
            environment,
            _CANCEL_SCRIPT,
            "/home/user",
            secrets.token_hex(16),
            {
                "YUXI_PGID": str(pgid) if pgid is not None else "",
                "YUXI_EXECUTION": execution_id,
                "YUXI_TERMINAL": "1" if terminal_confirmed else "0",
            },
            timeout=10,
            python=True,
        ):
            if "end" in event:
                if event["end"]["exitCode"] != 0:
                    raise RuntimeError("目标命令组取消失败")
                ended = True
        if not ended:
            raise RuntimeError("目标命令组取消未确认")

    def find_created(self, operation_id: str) -> list[dict]:
        """核对控制调用断连后是否已经创建带本次操作标识的实例。"""
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.get(f"{self.config.api_url}/v2/sandboxes", headers=self._api_headers())
            response.raise_for_status()
            return [row for row in response.json() if row.get("metadata", {}).get("yuxi-operation") == operation_id]

    def _running_sandbox(self, environment: Environment) -> Sandbox:
        """从只读详情构造 envd 连接，避免隐式恢复 VM。"""
        data = self._control("GET", f"/sandboxes/{environment.sandbox_id}", timeout=10)
        if not isinstance(data, dict) or data.get("sandboxID") != environment.sandbox_id:
            raise RuntimeError("Cube 实例身份未确认")
        if data.get("state") != "running":
            raise StateConflictError("当前 Cube 未运行", code="sandbox_changed")
        sandbox = Sandbox(data, config=self.config)
        self._install_tokens(sandbox, environment)
        return sandbox

    def _stream_process(
        self,
        sandbox: Sandbox,
        environment: Environment,
        user: str,
        payload: dict,
        timeout: float,
        stdin_data: bytes | None,
        *,
        hard_deadline: bool = False,
        before_start: Callable[[], None] | None = None,
    ) -> Iterator[dict]:
        """使用固定的 envd 传输读取进程终态和发送限定的 stdin。"""
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/connect+json",
            "Connect-Protocol-Version": "1",
            "Connect-Content-Encoding": "identity",
            "Authorization": "Basic " + base64.b64encode(f"{user}:".encode()).decode(),
            "X-Access-Token": environment.envd_token,
        }
        stream_headers = {**headers, "Connect-Timeout-Ms": str(int(timeout * 1000))} if hard_deadline else headers
        ended = False
        address = f"http://{sandbox.get_host(49983)}/process.Process"
        with sandbox._build_data_client() as client:
            # 控制面查询与启动间可能已取消 Run，在发送进程请求前重验。
            if before_start is not None:
                before_start()
            with client.stream(
                "POST",
                f"{address}/Start",
                content=b"\x00" + struct.pack(">I", len(body)) + body,
                headers=stream_headers,
                timeout=httpx.Timeout(connect=15, read=timeout + 10, write=30, pool=15),
            ) as response:
                response.raise_for_status()
                for event in connect_events(response.iter_raw()):
                    if "end" in event:
                        ended = True
                    yield event
                    if "start" in event and stdin_data is not None:
                        self._send_process_stdin(client, address, headers, int(event["start"]["pid"]), stdin_data)
            if not ended:
                raise RuntimeError("执行流中断，未取得终态")

    def _send_process_stdin(self, client: httpx.Client, address: str, headers: dict[str, str], pid: int, stdin_data: bytes) -> None:
        """向同一 envd 进程按序发送全部字节及 EOF。"""
        headers = {**headers, "Content-Type": "application/json"}
        process = {"pid": pid}
        for offset in range(0, len(stdin_data), 65536):
            data = base64.b64encode(stdin_data[offset : offset + 65536]).decode()
            response = client.post(f"{address}/SendInput", json={"process": process, "input": {"stdin": data}}, headers=headers, timeout=30)
            if not self._stdin_request_succeeded(response, pid):
                return
        response = client.post(f"{address}/CloseStdin", json={"process": process}, headers=headers, timeout=30)
        self._stdin_request_succeeded(response, pid)

    @staticmethod
    def _stdin_request_succeeded(response: httpx.Response, pid: int) -> bool:
        """识别固定 envd 版本的输入关闭竞态，实际退出由 Start 流确认。"""
        if response.status_code in (404, 500):
            try:
                error = response.json()
            except ValueError:
                error = None
            if isinstance(error, dict):
                code, message = error.get("code"), error.get("message")
                if response.status_code == 404 and code == "not_found" and message == f"process with pid {pid} not found":
                    return False
                if response.status_code == 500:
                    if code == "unknown" and message == "error closing stdin: close |1: file already closed":
                        return False
                    if code == "internal":
                        prefix = f"error writing to stdin: error writing to stdin of process '{pid}': write |1: "
                        if message in (prefix + "file already closed", prefix + "broken pipe"):
                            return False
        response.raise_for_status()
        return True

    def _api_headers(self) -> dict[str, str]:
        """只向控制面附加部署凭据。"""
        return {"X-API-Key": self.config.api_key} if self.config.api_key else {}

    @staticmethod
    def _install_tokens(sandbox: Sandbox, environment: Environment) -> None:
        """将 SDK 缺少公开参数的令牌适配限制在一处。"""
        # 固定 cubesandbox 0.7.3；connect 不回传 traffic token，envd token 默认为 None。
        sandbox._data["envdAccessToken"] = environment.envd_token
        sandbox._data["trafficAccessToken"] = environment.traffic_token
