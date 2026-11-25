"""启动时汇总各域配置并同步持久定义；汇总属于组合根，不进入 system 模块。"""

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.documents.options import DOCUMENT_OPTIONS
from yuxi.modules.extensions.options import WEB_PROVIDER_OPTIONS, remote_skill_source_policy
from yuxi.modules.extensions.plugins.abridge.options import abridge_options
from yuxi.modules.knowledge.options import knowledge_url_import_policy
from yuxi.modules.system.options import system_options
from yuxi.modules.system.repositories.options import get_option, sync_option_definitions
from yuxi.modules.system.services.options import Option


def get_option_definitions() -> dict[str, Option]:
    """显式汇总各模块配置定义，系统管理机制不拥有领域定义。"""
    return {
        option.key: option
        for option in (
            *DOCUMENT_OPTIONS,
            *WEB_PROVIDER_OPTIONS.values(),
            remote_skill_source_policy,
            abridge_options,
            knowledge_url_import_policy,
            system_options,
        )
    }


async def ensure_options_in_db(db: AsyncSession) -> None:
    """同步配置定义并清理 aBridge 旧凭据。"""
    await sync_option_definitions(db, get_option_definitions())
    # 删除无消费者的旧凭据，避免定义移除后将历史值当普通字段返回。
    abridge = await get_option(db, abridge_options.key)
    if abridge is not None and "admin_token" in (abridge.value or {}):
        abridge.value = {key: value for key, value in abridge.value.items() if key != "admin_token"}
