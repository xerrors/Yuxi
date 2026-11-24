"""MCP 服务配置的 PostgreSQL 查询和写入。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.extensions.mcp.builtin import BUILTIN_MCP_SERVERS, supports_direct_mcp_selection
from yuxi.modules.extensions.mcp.models import MCPServer


async def get_mcp_server(db: AsyncSession, slug: str, *, lock: bool = False, published_only: bool = True) -> MCPServer | None:
    """按稳定标识回读配置，凭证发布与配置变更可持有同一服务锁。"""
    query = select(MCPServer).filter(MCPServer.slug == slug).execution_options(populate_existing=True)
    if published_only:
        query = query.where(_published())
    if lock:
        query = query.with_for_update()
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_all_mcp_servers(db: AsyncSession, *, direct_only: bool = False) -> list[MCPServer]:
    """读取已发布的服务配置，可限定为允许直接选择的连接器。"""
    result = await db.execute(select(MCPServer).where(_published(direct_only=direct_only)))
    return list(result.scalars().all())


async def list_enabled_mcp_servers(db: AsyncSession, *, names: list[str] | None = None, direct_only: bool = False) -> list[MCPServer]:
    """按服务开关和受支持传输读取配置目录。"""
    from yuxi.modules.extensions.mcp.runtime import SUPPORTED_TRANSPORTS

    stmt = select(MCPServer).where(
        _published(direct_only=direct_only), MCPServer.enabled == 1, MCPServer.transport.in_(SUPPORTED_TRANSPORTS)
    )
    if names is not None:
        stmt = stmt.where(MCPServer.slug.in_(names))
    return list((await db.scalars(stmt)).all())


async def save_mcp_server(db: AsyncSession, server: MCPServer, *, new: bool = False) -> None:
    """写入服务配置，事务仍由调用方提交。"""
    if new:
        db.add(server)
    await db.flush()


async def delete_mcp_server_row(db: AsyncSession, server: MCPServer) -> None:
    """删除服务配置，事务仍由调用方提交。"""
    await db.delete(server)
    await db.flush()


async def active_mcp_user(db, *, user_id=None, uid=None):
    """运行用户必须由可信 ID 或 uid 解析，停用账号不能读取凭据。"""
    from yuxi.modules.identity.models import User
    from yuxi.shared.errors import PermissionDeniedError

    query = select(User).where(User.is_deleted == 0)
    query = query.where(User.id == user_id) if user_id is not None else query.where(User.uid == uid)
    user = await db.scalar(query.execution_options(populate_existing=True))
    if user is None:
        raise PermissionDeniedError("MCP 用户不可用")
    return user


async def mcp_connection(db, server_id, user_id, *, lock=False, create=False):
    """唯一约束与行锁共同串行化个人连接发布、撤销和刷新。"""
    from sqlalchemy.dialects.postgresql import insert

    from yuxi.modules.extensions.mcp.models import MCPConnection

    if create:
        await db.execute(insert(MCPConnection).values(server_id=server_id, user_id=user_id).on_conflict_do_nothing())
    query = select(MCPConnection).where(MCPConnection.server_id == server_id, MCPConnection.user_id == user_id)
    if lock:
        query = query.with_for_update()
    return await db.scalar(query.execution_options(populate_existing=True))


async def consume_oauth_attempt(db, state_hash, browser_hash):
    """错误浏览器不能消费事务；合法消费使用 DELETE RETURNING 原子执行。"""
    from sqlalchemy import delete

    from yuxi.modules.extensions.mcp.models import MCPOAuthAttempt
    from yuxi.shared.datetime import utc_now

    return await db.scalar(
        delete(MCPOAuthAttempt)
        .where(
            MCPOAuthAttempt.state_hash == state_hash,
            MCPOAuthAttempt.browser_hash == browser_hash,
            MCPOAuthAttempt.expires_at > utc_now(),
        )
        .returning(MCPOAuthAttempt)
    )


async def get_oauth_attempt(db, state_hash, browser_hash, user_id):
    """完成授权前按当前用户筛选，错误账号不能消费他人的结果。"""
    from yuxi.modules.extensions.mcp.models import MCPConnection, MCPOAuthAttempt
    from yuxi.shared.datetime import utc_now

    return await db.scalar(
        select(MCPOAuthAttempt)
        .join(MCPConnection, MCPConnection.id == MCPOAuthAttempt.connection_id)
        .where(
            MCPOAuthAttempt.state_hash == state_hash,
            MCPOAuthAttempt.browser_hash == browser_hash,
            MCPOAuthAttempt.expires_at > utc_now(),
            MCPConnection.user_id == user_id,
        )
        .with_for_update(of=MCPOAuthAttempt)
    )


async def personal_mcp_connections(db, server_ids, user_id):
    """只读取本次服务列表中属于当前用户的连接。"""
    from yuxi.modules.extensions.mcp.models import MCPConnection

    if not server_ids:
        return {}
    rows = (await db.scalars(select(MCPConnection).where(MCPConnection.user_id == user_id, MCPConnection.server_id.in_(server_ids)))).all()
    return {row.server_id: row for row in rows}


async def get_mcp_server_by_id(db, server_id, *, lock=False):
    """配置发布与动态注册共用服务器行，回读不采用旧 identity map。"""
    query = select(MCPServer).where(_published(), MCPServer.id == server_id)
    if lock:
        query = query.with_for_update()
    return await db.scalar(query.execution_options(populate_existing=True))


async def get_mcp_connection_by_id(db, connection_id, *, lock=False):
    """回调发布前锁定最新连接世代。"""
    from yuxi.modules.extensions.mcp.models import MCPConnection

    query = select(MCPConnection).where(MCPConnection.id == connection_id)
    if lock:
        query = query.with_for_update()
    return await db.scalar(query.execution_options(populate_existing=True))


async def delete_expired_oauth_attempts(db):
    """跳过完成请求已锁定的结果，避免清理与连接行锁形成反向等待。"""
    from sqlalchemy import delete

    from yuxi.modules.extensions.mcp.models import MCPOAuthAttempt
    from yuxi.shared.datetime import utc_now

    expired = select(MCPOAuthAttempt.state_hash).where(MCPOAuthAttempt.expires_at <= utc_now()).with_for_update(skip_locked=True)
    await db.execute(delete(MCPOAuthAttempt).where(MCPOAuthAttempt.state_hash.in_(expired)))


async def runtime_mcp_servers(db, slugs, user_id):
    """一次读取本次候选服务与调用者自己的连接。"""
    from sqlalchemy import and_

    from yuxi.modules.extensions.mcp.models import MCPConnection

    return (
        await db.execute(
            select(MCPServer, MCPConnection)
            .outerjoin(MCPConnection, and_(MCPConnection.server_id == MCPServer.id, MCPConnection.user_id == user_id))
            .where(_published(), MCPServer.slug.in_(slugs))
            .execution_options(populate_existing=True)
        )
    ).all()


async def runtime_mcp_policy(db, slug, uid):
    """同一语句读取有效调用者和最新服务策略，不读取个人凭据。"""
    from yuxi.modules.identity.models import User

    return (
        await db.execute(
            select(User, MCPServer)
            .select_from(User)
            .outerjoin(MCPServer, (MCPServer.slug == slug) & _published())
            .where(User.uid == uid, User.is_deleted == 0)
            .execution_options(populate_existing=True)
        )
    ).first()


async def invalidate_mcp_connections(db, server_id):
    """配置变更推进连接世代并清空凭证，保留用户主动清除的状态。"""
    from sqlalchemy import case, update

    from yuxi.modules.extensions.mcp.models import MCPConnection

    await db.execute(
        update(MCPConnection)
        .where(MCPConnection.server_id == server_id)
        .values(
            generation=MCPConnection.generation + 1,
            status=case((MCPConnection.status == "disconnected", "disconnected"), else_="reconnect_required"),
            credentials=None,
            expires_at=None,
        )
    )


def _published(*, direct_only: bool = False):
    """只读取本版发布的行，直接选择目录在检查部署可用性前排除专属连接。"""
    published = MCPServer.code_owned == MCPServer.slug.in_(BUILTIN_MCP_SERVERS)
    if direct_only:
        private_slugs = [slug for slug in BUILTIN_MCP_SERVERS if not supports_direct_mcp_selection(slug)]
        return published & MCPServer.slug.not_in(private_slugs)
    return published
