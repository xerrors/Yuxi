"""启动时汇总各域配置并同步持久定义；汇总属于组合根，不进入 system 模块。"""

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.documents.options import DOCUMENT_OPTIONS
from yuxi.modules.documents.service import migrate_mineru_configuration
from yuxi.modules.extensions.options import WEB_PROVIDER_OPTIONS, remote_skill_source_policy
from yuxi.modules.system.options import system_options
from yuxi.modules.system.repositories.options import sync_option_definitions
from yuxi.modules.system.services.options import Option


def get_option_definitions() -> dict[str, Option]:
    """显式汇总各模块配置定义，系统管理机制不拥有领域定义。"""
    return {
        option.key: option for option in (*DOCUMENT_OPTIONS, *WEB_PROVIDER_OPTIONS.values(), remote_skill_source_policy, system_options)
    }


async def ensure_options_in_db(db: AsyncSession) -> bool:
    """同步定义并迁移 MinerU 旧设置，返回是否需要在提交后失效缓存。"""
    await sync_option_definitions(db, get_option_definitions())
    return await migrate_mineru_configuration(db)
