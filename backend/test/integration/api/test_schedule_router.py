"""schedule_router 集成测试 — 路由层 agent_config 归属校验（Task 7）。

测试场景：
1. 普通用户绑定非自己的 agent_config_id 创建 schedule 应被 403 拒绝；
2. 普通用户 update 时把 agent_config_id 切换为他人拥有的应被 403 拒绝；
3. admin 创建 schedule 时绑定任何 agent_config 都应成功。
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_create_schedule_rejects_foreign_agent(test_client, admin_headers, standard_user):
    """普通用户绑定非自己的 agent_config_id 创建 schedule 应被 403 拒绝。"""
    user_headers = standard_user["headers"]

    # 用 admin 创建一个 agent_config
    create_cfg = await test_client.post(
        "/api/chat/agent/ChatbotAgent/configs",
        json={"name": f"admin_owned_{uuid.uuid4().hex[:6]}", "config_json": {}},
        headers=admin_headers,
    )
    assert create_cfg.status_code == 200
    admin_owned_config_id = create_cfg.json()["config"]["id"]

    # 普通用户尝试绑定 admin 拥有的 config
    res = await test_client.post(
        "/api/schedules",
        json={
            "name": "越权测试",
            "agent_config_id": admin_owned_config_id,
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "query": "hi",
        },
        headers=user_headers,
    )
    assert res.status_code == 403, res.text
    assert "无权" in res.json()["detail"]


async def test_update_schedule_rejects_foreign_agent(test_client, admin_headers, standard_user):
    """普通用户 update 时把 agent_config_id 切换为他人拥有的应被 403 拒绝。

    注：当前 chat_router 只允许 admin 创建 agent_config（普通用户 POST /api/chat/agent/.../configs
    返回 403），所以本测试改用「admin 创建 config + user 直接 PUT」验证 update 路径
    的 agent_config_id 归属校验。
    update_schedule_route 中 agent_config_id 校验在 schedule 校验之前触发，
    即 schedule_id 可以是任意值（包括不存在的），但 agent_config_id 必须是他人拥有的以触发 403。
    """
    user_headers = standard_user["headers"]

    # admin 创建一个 config（admin-owned）
    admin_cfg_res = await test_client.post(
        "/api/chat/agent/ChatbotAgent/configs",
        json={"name": f"admin_cfg_{uuid.uuid4().hex[:6]}", "config_json": {}},
        headers=admin_headers,
    )
    admin_cfg_id = admin_cfg_res.json()["config"]["id"]

    # user 尝试 update schedule 把 agent_config_id 切换为 admin 的
    # agent_config_id 校验在 schedule 校验之前 → 即使 schedule 不存在也返回 403
    upd_res = await test_client.put(
        "/api/schedules/any-schedule-id-placeholder",
        json={"agent_config_id": admin_cfg_id},
        headers=user_headers,
    )
    assert upd_res.status_code == 403, upd_res.text


async def test_admin_can_bind_any_agent_when_creating_schedule(test_client, admin_headers):
    """admin 创建 schedule 时绑定任何 agent_config 都应成功。"""
    cfg_res = await test_client.post(
        "/api/chat/agent/ChatbotAgent/configs",
        json={"name": f"any_cfg_{uuid.uuid4().hex[:6]}", "config_json": {}},
        headers=admin_headers,
    )
    cfg_id = cfg_res.json()["config"]["id"]

    res = await test_client.post(
        "/api/schedules",
        json={
            "name": "admin 任务",
            "agent_config_id": cfg_id,
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "query": "hi",
        },
        headers=admin_headers,
    )
    assert res.status_code == 200, res.text
