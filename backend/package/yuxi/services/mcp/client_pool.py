from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient
from yuxi.services.mcp_auth.orchestrator import mcp_auth_context_var

if TYPE_CHECKING:
    from mcp import ClientSession

from cachetools import TTLCache

# 缓存存储格式: (server_name, user_id, department_id) -> resolved_headers
_resolved_headers_cache: TTLCache = TTLCache(maxsize=1024, ttl=15.0)


def clear_resolved_headers_cache() -> None:
    """清除解析后的 headers 缓存"""
    _resolved_headers_cache.clear()


def clear_server_resolved_headers_cache(server_name: str) -> None:
    """清除指定服务器的解析后 headers 缓存"""
    stale_keys = [k for k in _resolved_headers_cache if k[0] == server_name]
    for key in stale_keys:
        _resolved_headers_cache.pop(key, None)


logger = logging.getLogger("yuxi.mcp.client_pool")


class DynamicMCPTokenAuth(httpx.Auth):
    """动态 MCP Token 认证拦截器，每次 HTTP 请求前从 ContextVar 动态读取并注入 Authorization 头部"""

    def __init__(self, server_name: str):
        self.server_name = server_name

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        auth_context = mcp_auth_context_var.get()
        if auth_context:
            try:
                cache_key = (self.server_name, auth_context.user_id, auth_context.department_id)
                cached_headers = _resolved_headers_cache.get(cache_key)
                if cached_headers is not None:
                    for key, val in cached_headers.items():
                        request.headers[key] = str(val)
                    yield request
                    return

                from yuxi.services.mcp_auth.proxy_service import INTERNAL_PROXY_TOKEN_HEADER, create_proxy_access_token

                if INTERNAL_PROXY_TOKEN_HEADER.lower() in request.headers:
                    # NOTE: 代理模式下，直接在本地生成新的代理 JWT，跳过 DB 事务
                    new_token = create_proxy_access_token(self.server_name, auth_context)
                    request.headers[INTERNAL_PROXY_TOKEN_HEADER] = new_token
                    _resolved_headers_cache[cache_key] = dict(request.headers)
                    yield request
                    return

                # 导入数据库会话管理器以获取连接与 Token
                from yuxi.storage.postgres.manager import pg_manager

                async with pg_manager.get_async_session_context() as session:
                    from yuxi.services.mcp.server_service import get_runtime_mcp_server_config

                    # NOTE: 读取当前上下文对应的最新运行时配置（含 Token 自动刷新逻辑）
                    runtime_config = await get_runtime_mcp_server_config(
                        self.server_name,
                        auth_context=auth_context,
                        db=session,
                    )
                    if runtime_config:
                        headers = dict(runtime_config.get("headers") or {})
                        _resolved_headers_cache[cache_key] = headers

                        for key, val in headers.items():
                            request.headers[key] = str(val)
            except Exception as exc:
                logger.error(f"DynamicMCPTokenAuth failed to resolve token headers for '{self.server_name}': {exc}")
        yield request


class LongLivedSession:
    """长期存活的 MCP Client 及其 Session 生命周期管理器"""

    def __init__(self, client: MultiServerMCPClient, server_name: str):
        self.client = client
        self.server_name = server_name
        self.session: ClientSession | None = None
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._ready_event = asyncio.Event()
        self._stop_event = asyncio.Event()

    async def start(self):
        """在后台启动长连接 Session"""
        if not hasattr(self.client, "session"):
            self.session = self.client
            self._ready_event.set()
            return

        self._running = True
        self._stop_event.clear()
        self._ready_event.clear()
        self._loop_task = asyncio.create_task(self._run_loop())
        # 等待 Session 成功连接并完成 initialize()
        await self._ready_event.wait()
        if not self.session:
            raise RuntimeError(f"Failed to startup MCP ClientSession for {self.server_name}")

    async def _run_loop(self):
        try:
            # NOTE: 利用 client.session 会在退出上下文时自动释放底层的 Stdio 子进程或 HTTP Keep-Alive 连接
            async with self.client.session(self.server_name) as session:
                self.session = session
                self._ready_event.set()
                # 挂起直到收到停止指令
                await self._stop_event.wait()
        except Exception:
            logger.error(f"Error in long-lived MCP session loop for {self.server_name}", exc_info=True)
        finally:
            self.session = None
            self._running = False
            self._ready_event.set()

    async def stop(self):
        """停止长连接，回收子进程与 TCP 连接资源"""
        self._stop_event.set()
        if self._loop_task:
            try:
                await asyncio.wait_for(self._loop_task, timeout=5.0)
            except TimeoutError:
                logger.warning(f"Timeout waiting for long-lived session of {self.server_name} to stop.")
                self._loop_task.cancel()
            except Exception as exc:
                logger.debug(f"Exception during long-lived session cleanup of {self.server_name}: {exc}")
            self._loop_task = None


class MCPClientPool:
    """MCP 客户端连接池实现"""

    def __init__(self):
        # 缓存键格式: (server_name, partition_key) -> tuple[LongLivedSession, str] | asyncio.Future
        self._sessions: dict[tuple[str, str], Any] = {}
        self._dict_lock = asyncio.Lock()

    def _calculate_config_hash(self, config: dict[str, Any]) -> str:
        """根据配置计算 Hash 用于比对配置是否脏变"""
        clean_config = {
            k: v
            for k, v in config.items()
            if k
            not in {
                "__yuxi_cache_partition",
                "__yuxi_allow_global_cache",
                "disabled_tools",
            }
        }
        # 剔除 header 中可能随时变化的 token，以便准确比对静态配置
        from yuxi.services.mcp_auth.proxy_service import INTERNAL_PROXY_TOKEN_HEADER

        transient_header_names = {"authorization", INTERNAL_PROXY_TOKEN_HEADER.lower()}
        headers = dict(clean_config.get("headers") or {})
        headers = {key: value for key, value in headers.items() if key.lower() not in transient_header_names}
        if headers:
            clean_config["headers"] = headers
        elif "headers" in clean_config:
            clean_config["headers"] = {}

        payload = json.dumps(clean_config, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    async def _get_mcp_client(self, server_configs: dict[str, Any] | None = None) -> MultiServerMCPClient | None:
        try:
            client = MultiServerMCPClient(server_configs)  # pyright: ignore[reportArgumentType]
            logger.info(f"Initialized MCP client with servers: {list(server_configs.keys() or [])}")
            return client
        except Exception as e:
            logger.error(f"Failed to initialize MCP client: {e}")
            return None

    async def get_session(
        self,
        server_name: str,
        partition_key: str,
        runtime_config: dict[str, Any],
    ) -> ClientSession:
        """获取或重建匹配当前配置的 ClientSession"""
        config_hash = self._calculate_config_hash(runtime_config)
        cache_key = (server_name, partition_key)

        while True:
            async with self._dict_lock:
                existing = self._sessions.get(cache_key)

                if existing is not None:
                    if isinstance(existing, asyncio.Future):
                        future = existing
                        stale_session = None
                    else:
                        ll_session, cached_hash = existing
                        if cached_hash == config_hash and ll_session.session is not None:
                            return ll_session.session

                        self._sessions.pop(cache_key, None)
                        stale_session = ll_session
                        future = None
                else:
                    future = None
                    stale_session = None

                    stale_keys = [k for k in self._sessions if k[0] == server_name and k != cache_key]
                    stale_other_sessions = []
                    for stale_key in stale_keys:
                        stale_val = self._sessions.pop(stale_key)
                        if not isinstance(stale_val, asyncio.Future):
                            stale_other_sessions.append(stale_val[0])

                    init_future = asyncio.get_running_loop().create_future()
                    self._sessions[cache_key] = init_future
                    break

            if future is not None:
                await future
                continue

            if stale_session is not None:
                logger.info(f"Destroying stale/disconnected MCP session for {cache_key}")
                await stale_session.stop()
                continue

        for s_session in stale_other_sessions:
            logger.info("Evicting stale MCP session")
            await s_session.stop()

        try:
            client_config = dict(runtime_config)
            for magic_k in (
                "__yuxi_cache_partition",
                "__yuxi_allow_global_cache",
                "disabled_tools",
            ):
                client_config.pop(magic_k, None)

            if client_config.get("transport") in ("sse", "http", "streamable_http", "streamable-http"):
                client_config["auth"] = DynamicMCPTokenAuth(server_name)

            logger.info(
                f"Creating new long-lived MCP session for {cache_key} (transport: {client_config.get('transport')})"
            )
            client = await self._get_mcp_client({server_name: client_config})
            if client is None:
                raise RuntimeError(f"Failed to initialize MCP client for {server_name}")
            ll_session = LongLivedSession(client, server_name)
            await ll_session.start()

            result = (ll_session, config_hash)
            init_future.set_result(result)
            async with self._dict_lock:
                self._sessions[cache_key] = result
            return ll_session.session

        except BaseException as exc:
            if not init_future.done():
                init_future.set_exception(exc)
            async with self._dict_lock:
                if self._sessions.get(cache_key) is init_future:
                    self._sessions.pop(cache_key, None)
    async def remove_session(self, server_name: str, partition_key: str):
        """移除指定 key 的连接，强制下一次请求重新创建"""
        cache_key = (server_name, partition_key)
        async with self._dict_lock:
            val = self._sessions.pop(cache_key, None)
            if val is not None and not isinstance(val, asyncio.Future):
                ll_session, _ = val
                logger.info(f"Removing invalid session for {cache_key} from pool")
                await ll_session.stop()

    async def ensure_prewarm(
        self,
        server_name: str,
        partition_key: str,
        runtime_config: dict[str, Any],
    ):
        """后台异步预热加载，减少首次访问时的冷启动卡顿"""
        try:
            await self.get_session(server_name, partition_key, runtime_config)
        except Exception as exc:
            logger.warning(f"Failed to pre-warm MCP server '{server_name}': {exc}")

    async def shutdown(self):
        """关闭并回收连接池中的所有连接"""
        async with self._dict_lock:
            sessions_to_stop = []
            for cache_key, val in list(self._sessions.items()):
                if isinstance(val, asyncio.Future):
                    val.cancel()
                else:
                    ll_session, _ = val
                    sessions_to_stop.append((cache_key, ll_session))
            self._sessions.clear()

        for cache_key, ll_session in sessions_to_stop:
            logger.info(f"Stopping MCP session for {cache_key} during shutdown")
            await ll_session.stop()


# 全局单例连接池
mcp_client_pool = MCPClientPool()
