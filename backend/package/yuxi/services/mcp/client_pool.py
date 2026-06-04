from __future__ import annotations
import asyncio
import hashlib
import json
import logging
from typing import Any, AsyncGenerator, TYPE_CHECKING
import httpx

from langchain_mcp_adapters.client import MultiServerMCPClient
from yuxi.services.mcp_auth.orchestrator import mcp_auth_context_var

if TYPE_CHECKING:
    from mcp import ClientSession

logger = logging.getLogger("yuxi.mcp.client_pool")


class DynamicMCPTokenAuth(httpx.Auth):
    """动态 MCP Token 认证拦截器，每次 HTTP 请求前从 ContextVar 动态读取并注入 Authorization 头部"""

    def __init__(self, server_name: str):
        self.server_name = server_name

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        # NOTE: 1. 从当前协程上下文读取 AuthContext
        auth_context = mcp_auth_context_var.get()
        if auth_context:
            try:
                # 导入数据库会话管理器以获取连接与 Token
                from yuxi.storage.postgres.manager import pg_manager
                async with pg_manager.get_async_session_context() as session:
                    from yuxi.services.mcp_service import get_runtime_mcp_server_config
                    # NOTE: 2. 读取当前上下文对应的最新运行时配置（含 Token 自动刷新逻辑）
                    runtime_config = await get_runtime_mcp_server_config(
                        self.server_name,
                        auth_context=auth_context,
                        db=session,
                    )
                    if runtime_config:
                        # NOTE: 3. 将最新的头部注入到当前 HTTP 请求中
                        headers = runtime_config.get("headers") or {}
                        for key, val in headers.items():
                            request.headers[key] = str(val)
            except Exception as exc:
                logger.error(
                    f"DynamicMCPTokenAuth failed to resolve token headers for '{self.server_name}': {exc}"
                )
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
        except Exception as exc:
            logger.error(f"Error in long-lived MCP session loop for {self.server_name}: {exc}")
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
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for long-lived session of {self.server_name} to stop.")
                self._loop_task.cancel()
            except Exception as exc:
                logger.debug(f"Exception during long-lived session cleanup of {self.server_name}: {exc}")
            self._loop_task = None


class MCPClientPool:
    """MCP 客户端连接池实现"""

    def __init__(self):
        # 缓存键格式: (server_name, partition_key) -> (LongLivedSession, config_hash)
        self._sessions: dict[tuple[str, str], tuple[LongLivedSession, str]] = {}
        self._lock = asyncio.Lock()

    def _calculate_config_hash(self, config: dict[str, Any]) -> str:
        """根据配置计算 Hash 用于比对配置是否脏变"""
        clean_config = {
            k: v
            for k, v in config.items()
            if k not in {
                "__yuxi_cache_partition",
                "__yuxi_allow_global_cache",
                "disabled_tools",
            }
        }
        # 剔除 header 中可能随时变化的 token/Authorization 以便准确比对静态配置
        headers = dict(clean_config.get("headers") or {})
        headers.pop("Authorization", None)
        if headers:
            clean_config["headers"] = headers
        elif "headers" in clean_config:
            clean_config["headers"] = {}

        payload = json.dumps(clean_config, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    async def get_session(
        self,
        server_name: str,
        partition_key: str,
        runtime_config: dict[str, Any],
    ) -> ClientSession:
        """获取或重建匹配当前配置的 ClientSession"""
        config_hash = self._calculate_config_hash(runtime_config)
        cache_key = (server_name, partition_key)

        async with self._lock:
            existing = self._sessions.get(cache_key)
            if existing:
                ll_session, cached_hash = existing
                # NOTE: 如果配置无变化且 Session 处于活动状态，直接复用
                if cached_hash == config_hash and ll_session.session is not None:
                    return ll_session.session
                
                # 如果发生配置变化或 Session 断开，执行销毁
                logger.info(f"Destroying stale/disconnected MCP session for {cache_key}")
                await ll_session.stop()
                self._sessions.pop(cache_key, None)

            # NOTE: 针对 HTTP/SSE 协议，注入自定义的 httpx.Auth 认证流以支持长连接动态 Token
            client_config = dict(runtime_config)
            # 清理框架保留魔法键
            for magic_k in (
                "__yuxi_cache_partition",
                "__yuxi_allow_global_cache",
                "disabled_tools",
            ):
                client_config.pop(magic_k, None)

            if client_config.get("transport") in ("sse", "http", "streamable_http", "streamable-http"):
                # 注入 DynamicMCPTokenAuth，让底层 httpx 在长连接执行每个具体请求时动态提取最新 Token
                client_config["auth"] = DynamicMCPTokenAuth(server_name)

            logger.info(f"Creating new long-lived MCP session for {cache_key} (transport: {client_config.get('transport')})")
            from yuxi.services.mcp_service import get_mcp_client
            client = await get_mcp_client({server_name: client_config})
            if client is None:
                raise RuntimeError(f"Failed to initialize MCP client for {server_name}")
            ll_session = LongLivedSession(client, server_name)
            await ll_session.start()
            
            self._sessions[cache_key] = (ll_session, config_hash)
            return ll_session.session

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
        async with self._lock:
            for cache_key, (ll_session, _) in list(self._sessions.items()):
                logger.info(f"Stopping MCP session for {cache_key} during shutdown")
                await ll_session.stop()
            self._sessions.clear()


# 全局单例连接池
mcp_client_pool = MCPClientPool()
