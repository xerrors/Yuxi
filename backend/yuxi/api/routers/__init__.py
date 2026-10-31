from fastapi import APIRouter

from yuxi.api.routers.agents.feedback import router as feedback_router
from yuxi.api.routers.agents.files import router as agent_files_router
from yuxi.api.routers.agents.management import agent_router
from yuxi.api.routers.agents.mentions import mention_router
from yuxi.api.routers.agents.personal import router as personal_router
from yuxi.api.routers.agents.references import reference_router
from yuxi.api.routers.agents.session_links import router as session_links_router
from yuxi.api.routers.agents.tracing import trace_router
from yuxi.api.routers.background_jobs import background_jobs
from yuxi.api.routers.dashboard import dashboard as dashboard_routes
from yuxi.api.routers.extensions.mcp import mcp
from yuxi.api.routers.extensions.skills import skills, user_skills
from yuxi.api.routers.extensions.tools import tools
from yuxi.api.routers.extensions.web import web_tools
from yuxi.api.routers.identity.auth import auth
from yuxi.api.routers.identity.departments import department
from yuxi.api.routers.identity.sandbox import router as user_sandbox_router
from yuxi.api.routers.identity.users import user_router
from yuxi.api.routers.knowledge.dashboard import knowledge_dashboard
from yuxi.api.routers.knowledge.evaluation import evaluation
from yuxi.api.routers.knowledge.graphs import graph
from yuxi.api.routers.knowledge.management import knowledge as knowledge_routes
from yuxi.api.routers.models import model_providers
from yuxi.api.routers.public_v1 import public_agents_router, public_knowledge_router
from yuxi.api.routers.schedules import scheduled_agents
from yuxi.api.routers.system import system as system_routes
from yuxi.api.routers.workspace.projects import projects
from yuxi.api.routers.workspace.viewer import filesystem_router
from yuxi.api.routers.workspace.workspace import workspace as workspace_routes
from yuxi.api.routers.workspace.workspace import workspace_knowledge
from yuxi.bootstrap.capabilities import knowledge_enabled

router = APIRouter()
router.include_router(public_agents_router)

# 基础系统接口：健康检查、配置、认证与聊天主链路。
router.include_router(system_routes)  # /api/system/* 系统状态与全局配置
router.include_router(auth)  # /api/auth/* 登录、用户信息与 CLI 浏览器登录授权
router.include_router(agent_files_router)  # /api/agent/files* 与 /api/agent/images 产品预处理
router.include_router(personal_router)
router.include_router(agent_router)  # /api/agent/* 智能体管理与运行态
router.include_router(session_links_router)
router.include_router(feedback_router)  # /api/agent/threads/* Web 用户反馈
router.include_router(reference_router)  # /api/agent/threads/* Web 回答来源标注
router.include_router(trace_router)  # Web 管理员 Turn 观测跳转
router.include_router(projects)  # /api/projects* 项目创建与选择
router.include_router(scheduled_agents)  # /api/scheduled-tasks* 用户自建 Agent 定时任务

# 管理与工作台接口：后台作业、权限域以及工具体系配置。
router.include_router(dashboard_routes)  # /api/dashboard/* 仪表盘聚合数据
router.include_router(department)  # /api/departments/* 部门与权限相关数据
router.include_router(background_jobs)  # /api/background-jobs/* 后台作业查询与管理
router.include_router(mcp)  # /api/system/mcp-servers/* MCP 服务管理
router.include_router(model_providers)  # /api/system/model-providers/* 独立模型配置
router.include_router(skills)  # /api/system/skills/* Skills 管理
router.include_router(user_skills)  # /api/skills/* 用户可用 Skills
router.include_router(tools)  # /api/system/tools/* 工具列表与配置
router.include_router(web_tools)
router.include_router(user_sandbox_router)
router.include_router(user_router)  # /api/user/* 用户级配置与凭据
router.include_router(filesystem_router)  # /api/viewer/filesystem/* 工作台文件系统视图
router.include_router(workspace_routes)  # /api/workspace/* 用户个人工作区
router.include_router(mention_router)  # /api/mention/* 提及文件搜索接口

if knowledge_enabled():
    router.include_router(public_knowledge_router)
    router.include_router(knowledge_dashboard)  # /api/dashboard/stats/knowledge 知识域仪表盘
    router.include_router(knowledge_routes)  # /api/knowledge/* 知识库管理
    router.include_router(evaluation)  # /api/evaluation/* 知识库评估
    router.include_router(graph)  # /api/graph/* 图谱查询与管理
    router.include_router(workspace_knowledge)  # /api/workspace/knowledge/* 工作区知识文件只读视图
