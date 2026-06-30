import os

from fastapi import APIRouter

from server.routers.agent_invocation_router import agent_invocation_router
from server.routers.agent_router import agent_router
from server.routers.auth_dept_router import department
from server.routers.auth_router import auth
from server.routers.chat_router import chat
from server.routers.dashboard_router import dashboard
from server.routers.filesystem_router import filesystem_router
from server.routers.mcp_router import mcp
from server.routers.mention_router import mention_router
from server.routers.model_provider_router import model_providers
from server.routers.skill_router import skills, user_skills
from server.routers.system_router import system
from server.routers.system_task_router import tasks
from server.routers.tool_router import tools
from server.routers.user_router import user_router
from server.routers.workspace_router import workspace

_LITE_MODE = os.environ.get("LITE_MODE", "").lower() in ("true", "1")

router = APIRouter()

# 基础系统接口：健康检查、配置、认证与聊天主链路。
router.include_router(system)  # /api/system/* 系统状态与全局配置
router.include_router(auth)  # /api/auth/* 登录、用户信息与 CLI 浏览器登录授权
router.include_router(agent_router)  # /api/agent/* 智能体管理与运行态
router.include_router(agent_invocation_router)  # /api/agent-invocation/* 外部 Agent 调用与评估
router.include_router(chat)  # /api/chat/* 对话线程、消息历史与附件

# 管理与工作台接口：后台任务、权限域以及工具体系配置。
router.include_router(dashboard)  # /api/dashboard/* 仪表盘聚合数据
router.include_router(department)  # /api/departments/* 部门与权限相关数据
router.include_router(tasks)  # /api/tasks/* 后台任务查询与管理
router.include_router(mcp)  # /api/system/mcp-servers/* MCP 服务管理
router.include_router(model_providers)  # /api/system/model-providers/* 独立模型配置
router.include_router(skills)  # /api/system/skills/* Skills 管理
router.include_router(user_skills)  # /api/skills/* 用户可用 Skills
router.include_router(tools)  # /api/system/tools/* 工具列表与配置
router.include_router(user_router)  # /api/user/* 用户级配置与凭据
router.include_router(filesystem_router)  # /api/viewer/filesystem/* 工作台文件系统视图
router.include_router(workspace)  # /api/workspace/* 用户个人工作区
router.include_router(mention_router)  # /api/mention/* 提及文件搜索接口

if not _LITE_MODE:
    from server.routers.graph_router import graph
    from server.routers.knowledge_eval_router import evaluation
    from server.routers.knowledge_router import knowledge

    # 知识库与图谱能力依赖较重，LITE 模式下跳过这组接口。
    router.include_router(knowledge)  # /api/knowledge/* 知识库管理与检索
    router.include_router(evaluation)  # /api/evaluation/* 知识库评估
    router.include_router(graph)  # /api/graph/* 图谱查询与管理
