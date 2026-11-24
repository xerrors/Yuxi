"""个人及 APP 的 Cube 浏览器登记与 APP 运行委托。"""

import asyncio
import hashlib
import os
from typing import Literal

from pydantic import BaseModel

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sandbox import RegisteredSandbox, registered_sandbox
from yuxi.modules.agents.runtime.sandbox.context import sandbox_run_owner
from yuxi.modules.agents.runtime.sandbox.provider import get_sandbox_provider
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.extensions.plugins.abridge.browser_enrollment import (
    BrowserBinding,
    read_binding,
    register_browser,
    sign_browser_request,
)
from yuxi.modules.extensions.plugins.abridge.browser_guest import browser_status, require_browser_client, start_browser
from yuxi.modules.extensions.plugins.abridge.options import AbridgeConnection, connection_settings
from yuxi.modules.identity.models import User
from yuxi.modules.identity.repositories.api_keys import APIKeyRepository
from yuxi.modules.identity.repositories.users import UserRepository
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import PermissionDeniedError, StateConflictError


class ManagedBrowserStatus(BaseModel):
    """公开 API 只公开当前用户的登记 ID 和连接状态。"""

    device_id: str
    status: Literal["ready", "connecting"]


class PersonalBrowserStatus(BaseModel):
    """产品页只展示本人浏览器的连接状态。"""

    status: Literal["not_connected", "offline", "connecting", "ready", "paused", "revoked", "unavailable"]


async def provision_managed_browser(scope: ActorScope) -> ManagedBrowserStatus:
    """Public API 只允许 APP Key 代表自己的终端用户登记。"""
    if not scope.app_id or scope.api_key_id is None:
        raise PermissionDeniedError("托管浏览器登记需要 APP Key 和终端用户身份", code="abridge_managed_browser_app_required")
    return await _provision(scope)


async def provision_personal_browser(uid: str) -> PersonalBrowserStatus:
    """为登录用户在其 aBridge 账号上连接一个普通浏览器设备。"""
    result = await _provision(ActorScope(uid=uid, app_id=None))
    return PersonalBrowserStatus(status=result.status)


async def personal_browser_status(uid: str) -> PersonalBrowserStatus:
    """按当前 VM 的安装标识查询 aBridge 登记，刷新页面无需执行来宾命令。"""
    scope = ActorScope(uid=uid, app_id=None)
    async with pg_manager.get_async_session_context() as db:
        await _require_actor(db, scope)
        settings = await connection_settings(db)
        instance = await registered_sandbox(db, uid)
    provider = get_sandbox_provider()
    if provider.backend != "cube" or not _managed_origin_allowed(settings):
        return PersonalBrowserStatus(status="unavailable")
    try:
        binding = await read_binding(scope, settings)
    except StateConflictError as exc:
        if exc.code == "abridge_managed_browser_upstream_disabled":
            return PersonalBrowserStatus(status="unavailable")
        raise
    if binding is None or instance is None or instance.state == "failed":
        return PersonalBrowserStatus(status="not_connected")
    if binding.status == "revoked" or binding.status == "paused":
        return PersonalBrowserStatus(status=binding.status)
    if not binding.key_issued or binding.installation_id != hashlib.sha256(instance.sandbox_id.encode()).hexdigest():
        return PersonalBrowserStatus(status="not_connected")
    if instance.state == "running" and binding.status == "online":
        return PersonalBrowserStatus(status="ready")
    return PersonalBrowserStatus(status="offline")


async def managed_run_delegation(scope: ActorScope, run_id: str, worker_id: str, settings: AbridgeConnection) -> str:
    """只为 APP 的当前执行者签发已登记设备的受限委托。"""
    sandbox = await _app_run_sandbox(scope, run_id, worker_id, settings)
    instance = sandbox.identity
    provider = get_sandbox_provider()
    if sandbox.state == "failed":
        raise PermissionDeniedError("沙盒运行环境异常，请联系部署管理员恢复", code="sandbox_unavailable")
    binding = await read_binding(scope, settings)
    if binding is not None:
        binding.require_available()
    if binding is None or binding.installation_id != hashlib.sha256(instance[1].encode()).hexdigest() or not binding.key_issued:
        raise StateConflictError("请先登记当前用户的托管浏览器", code="abridge_managed_browser_binding_missing")
    if sandbox.state != "running" or binding.status != "online":
        # 排队和审批期间可能休眠。Worker 仅恢复当前安装已经确认的绑定，无权登记或换钥。
        owner = sandbox_run_owner.set((run_id, worker_id))
        try:
            await asyncio.to_thread(provider.prepare_instance, scope.uid, expected=instance)
            guest = await browser_status(provider, scope.uid, instance[0])
            if not guest.matches(binding, settings):
                raise PermissionDeniedError("沙盒浏览器绑定已丢失，请通过 APP 接口重新连接", code="abridge_managed_browser_binding_missing")
            if guest.status != "running":
                await _installation_access(scope, settings, instance=instance)
                await start_browser(provider, scope.uid, instance[0], settings, binding, token=None)
            online = await _wait_browser_online(provider, scope, instance, settings, binding)
        except StateConflictError as exc:
            if exc.code == "lease_lost":
                raise PermissionDeniedError("托管浏览器执行租约已失效") from exc
            raise
        finally:
            sandbox_run_owner.reset(owner)
        if not online:
            raise StateConflictError("沙盒浏览器已离线，请通过 APP 接口重新连接", code="abridge_managed_browser_offline")
    current = await _app_run_sandbox(scope, run_id, worker_id, settings)
    if current.identity != instance:
        raise StateConflictError("运行环境已替换，请重试", code="sandbox_changed")
    return sign_browser_request(
        scope.uid, settings, "/mcp", "abridge-delegation", installation_id=binding.installation_id, revision=binding.revision
    )


async def _provision(scope: ActorScope) -> ManagedBrowserStatus:
    """使用 aBridge 登记当前来宾，并核对同一绑定的本地和远端状态。"""
    settings, _ = await _installation_access(scope)

    provider = get_sandbox_provider()
    if provider.backend != "cube":
        raise StateConflictError("aBridge 托管浏览器需要 Cube 沙盒", code="abridge_managed_browser_requires_cube")
    binding = await read_binding(scope, settings)
    if binding is not None:
        binding.require_available()
    await _installation_access(scope, settings)
    try:
        instance = await asyncio.to_thread(provider.prepare_instance, scope.uid)
    except StateConflictError as exc:
        if scope.app_id and exc.code == "sandbox_unavailable":
            raise StateConflictError("当前 APP 终端用户的运行环境异常，请联系部署管理员恢复", code=exc.code) from None
        raise
    generation, sandbox_id = instance
    installation = hashlib.sha256(sandbox_id.encode()).hexdigest()

    # 登记接口按预检版本比较并交换，准备 VM 期间发生换绑会返回冲突。
    await require_browser_client(provider, scope.uid, generation)
    guest = await browser_status(provider, scope.uid, generation)
    if guest.status != "unbound" and (
        guest.browser_origin != settings.public_origin
        or binding is None
        or guest.device_id != binding.device_id
        or guest.revision is None
        or guest.revision > binding.revision
    ):
        raise StateConflictError("浏览器绑定与 aBridge 登记不一致，请核对设备归属", code="abridge_managed_browser_binding_changed")
    replace = binding is not None and (
        binding.installation_id != installation or (binding.key_issued and not guest.matches(binding, settings))
    )
    if binding is None or replace or not binding.key_issued:
        _, username = await _installation_access(scope, settings, instance=instance)
        binding = await register_browser(scope, settings, installation, binding, replace=replace, username=username)

    if guest.status != "running" or not guest.matches(binding, settings):
        await _installation_access(scope, settings, instance=instance)
        await start_browser(provider, scope.uid, generation, settings, binding, token=binding.bootstrap_token)
    ready = await _wait_browser_online(provider, scope, instance, settings, binding)
    await _installation_access(scope, settings, instance=instance)
    return ManagedBrowserStatus(device_id=binding.device_id, status="ready" if ready else "connecting")


async def _wait_browser_online(
    provider, scope: ActorScope, instance: tuple[int, str], settings: AbridgeConnection, binding: BrowserBinding
) -> bool:
    """以内外两端对同一设备和版本的确认判定就绪。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 4
    while True:
        current = await read_binding(scope, settings)
        if current is None or current.device_id != binding.device_id or current.revision != binding.revision:
            raise StateConflictError("aBridge 托管浏览器登记已改变，请重试", code="abridge_managed_browser_stale")
        current.require_available()
        if current.status == "online":
            guest = await browser_status(provider, scope.uid, instance[0])
            if guest.ready(current, settings):
                return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.5)


async def _app_run_sandbox(scope: ActorScope, run_id: str, worker_id: str, settings: AbridgeConnection) -> RegisteredSandbox:
    """从当前 Run 复核执行身份、APP 权限及绑定的 VM，在签发前再次读取。"""
    async with pg_manager.get_async_session_context() as db:
        run = await AgentRunRepository(db).get_run_for_user(run_id, scope.uid)
        if (
            not scope.app_id
            or scope.api_key_id is None
            or run is None
            or (run.app_id, run.api_key_id) != (scope.app_id, scope.api_key_id)
            or run.status != "running"
            or not worker_id
            or run.worker_id != worker_id
            or run.lease_expires_at is None
            or run.lease_expires_at <= utc_now()
        ):
            raise PermissionDeniedError("托管浏览器需要当前执行身份和有效运行租约")
        await _authorized_settings(db, scope, settings)
        if get_sandbox_provider().backend != "cube":
            raise PermissionDeniedError("当前运行环境不支持 Cube 托管浏览器", code="abridge_managed_browser_requires_cube")
        sandbox = await registered_sandbox(db, scope.uid)
        if sandbox is None:
            raise StateConflictError("请先登记当前用户的托管浏览器", code="abridge_managed_browser_binding_missing")
        return sandbox


async def _require_actor(db, scope: ActorScope) -> User:
    """复核登录用户，或有效的 APP 所属用户及当前 Key。"""
    users = UserRepository(db)
    actor = await users.get_by_uid(scope.uid)
    if actor is None or actor.is_deleted or actor.is_login_locked():
        raise PermissionDeniedError("托管浏览器用户不可用")
    if actor.user_kind == "human" and scope.app_id is None and scope.api_key_id is None:
        return actor
    if actor.user_kind != "end_user" or actor.app_id != scope.app_id:
        raise PermissionDeniedError("托管浏览器用户不可用")
    if not scope.app_id or not scope.api_key_id:
        raise PermissionDeniedError("托管浏览器需要当前 APP 凭据")
    owner = await users.get_by_id(actor.owner_user_id)
    if owner is None or owner.user_kind != "human" or owner.is_deleted or owner.is_login_locked():
        raise PermissionDeniedError("托管浏览器 APP 所有者不可用")
    access = await APIKeyRepository(db).get_accessible(api_key_id=scope.api_key_id, requester_user_id=owner.id, is_superadmin=False)
    key = access.api_key
    if key is None or not key.is_valid() or key.app_id != scope.app_id or key.access_level not in {"agents", "full"}:
        raise PermissionDeniedError("托管浏览器 APP Key 不可用")
    return actor


async def _installation_access(
    scope: ActorScope, expected: AbridgeConnection | None = None, *, instance: tuple[int, str] | None = None
) -> tuple[AbridgeConnection, str]:
    """读取当前授权和部署配置，后续调用可同时核对已选配置及 VM。"""
    async with pg_manager.get_async_session_context() as db:
        current, username = await _authorized_settings(db, scope, expected)
        if instance is not None:
            sandbox = await registered_sandbox(db, scope.uid)
            if sandbox is None or sandbox.identity != instance:
                raise StateConflictError("运行环境已替换，请重试", code="sandbox_changed")
        return current, username


async def _authorized_settings(db, scope: ActorScope, expected: AbridgeConnection | None) -> tuple[AbridgeConnection, str]:
    """确保当前主体仍可使用这次请求选择的部署。"""
    actor = await _require_actor(db, scope)
    current = await connection_settings(db)
    if scope.app_id is not None and not current.allow_shadow_managed_browser:
        raise PermissionDeniedError("未允许 APP 终端用户使用 aBridge 托管浏览器", code="abridge_managed_browser_disabled")
    if not _managed_origin_allowed(current):
        raise StateConflictError("aBridge 需要可达的 HTTPS 来源；HTTP 部署需显式开启", code="abridge_managed_browser_invalid_origin")
    if expected is not None and current != expected:
        raise StateConflictError("aBridge 部署已改变，请重试")
    return current, actor.username


def _managed_origin_allowed(settings: AbridgeConnection) -> bool:
    """默认使用 HTTPS，显式开启时只接受配置的 HTTP 来源。"""
    return settings.configured and (
        settings.public_origin.startswith("https://")
        or (settings.public_origin.startswith("http://") and os.getenv("ABRIDGE_ALLOW_INSECURE_HTTP") == "true")
    )
