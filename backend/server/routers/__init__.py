import os

from fastapi import APIRouter

from server.routers.auth_router import auth
from server.routers.chat_router import chat
from server.routers.dashboard_router import dashboard
from server.routers.department_router import department
from server.routers.filesystem_router import filesystem_router, viewer_filesystem_router
from server.routers.mcp_router import mcp
from server.routers.skill_router import skills
from server.routers.subagent_router import subagents_router
from server.routers.system_router import system
from server.routers.task_router import tasks
from server.routers.tool_router import tools
from server.routers.apikey_router import apikey_router

_LITE_MODE = os.environ.get("LITE_MODE", "").lower() in ("true", "1")

router = APIRouter()

# 注册路由结构
router.include_router(system)  # /api/system/*
router.include_router(auth)  # /api/auth/*
router.include_router(chat)  # /api/chat/*
router.include_router(dashboard)  # /api/dashboard/*
router.include_router(department)  # /api/departments/*
router.include_router(tasks)  # /api/tasks/*
router.include_router(mcp)  # /api/system/mcp-servers/*
router.include_router(skills)  # /api/system/skills/*
router.include_router(subagents_router)  # /api/system/subagents/*
router.include_router(tools)  # /api/system/tools/*
router.include_router(apikey_router)  # /api/apikey/*
router.include_router(filesystem_router)  # /api/filesystem/*
router.include_router(viewer_filesystem_router)  # /api/viewer/filesystem/*

if not _LITE_MODE:
    from server.routers.graph_router import graph
    from server.routers.knowledge_router import knowledge
    from server.routers.evaluation_router import evaluation
    from server.routers.mindmap_router import mindmap

    router.include_router(knowledge)  # /api/knowledge/*
    router.include_router(evaluation)  # /api/evaluation/*
    router.include_router(mindmap)  # /api/mindmap/*
    router.include_router(graph)  # /api/graph/*
