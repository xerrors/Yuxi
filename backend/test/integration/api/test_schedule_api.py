import pytest
import uuid

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _get_agent_config_id(test_client, admin_headers):
    # 直接通过 API 自动创建一个测试专用的智能体配置（基于 ChatbotAgent）
    payload = {
        "name": f"pytest_config_{uuid.uuid4().hex[:8]}",
        "description": "pytest config description",
        "icon": "",
        "pics": [],
        "examples": [],
        "config_json": {}
    }
    
    response = await test_client.post(
        "/api/chat/agent/ChatbotAgent/configs",
        json=payload,
        headers=admin_headers
    )
    assert response.status_code == 200, f"Failed to create test config: {response.text}"
    
    config_data = response.json().get("config") or {}
    config_id = config_data.get("id")
    assert config_id is not None, f"Config ID is missing in response: {config_data}"
    return config_id


async def test_schedule_crud_and_permissions(test_client, admin_headers, standard_user):
    """测试定时任务配置的 CRUD、局部状态修改、手动触发执行、日志查询以及多用户权限隔离"""
    user_headers = standard_user["headers"]
    
    # 1. 动态生成所需的 agent_config_id
    config_id = await _get_agent_config_id(test_client, admin_headers)

    # 2. 普通用户创建一个定时调度
    create_payload = {
        "name": "测试定时任务",
        "description": "这是描述信息",
        "agent_config_id": config_id,
        "cron_expr": "0 9 * * 1",  # 每周一早上 9 点
        "timezone": "Asia/Shanghai",
        "query": "你好，请播报今天的天气",
        "config": {"temperature": 0.7},
        "enabled": True
    }
    
    create_res = await test_client.post("/api/schedules", json=create_payload, headers=user_headers)
    assert create_res.status_code == 200, create_res.text
    create_data = create_res.json()
    assert create_data["success"] is True
    schedule_id = create_data["data"]["id"]
    assert schedule_id is not None
    assert create_data["data"]["next_run_at"] is not None

    # 3. 查询定时任务详情
    get_res = await test_client.get(f"/api/schedules/{schedule_id}", headers=user_headers)
    assert get_res.status_code == 200, get_res.text
    assert get_res.json()["data"]["name"] == "测试定时任务"

    # 4. 越权测试：未登录用户访问详情应被拒绝
    get_unauth = await test_client.get(f"/api/schedules/{schedule_id}")
    assert get_unauth.status_code == 401

    # 5. 列表查询：普通用户只能查到自己的
    list_user_res = await test_client.get("/api/schedules", headers=user_headers)
    assert list_user_res.status_code == 200, list_user_res.text
    user_items = list_user_res.json()["data"]
    assert len(user_items) >= 1
    assert any(item["id"] == schedule_id for item in user_items)

    # 6. 更新定时任务（enabled 设为 False，next_run_at 应当被清空）
    update_payload = {
        "name": "已修改的定时任务",
        "enabled": False
    }
    update_res = await test_client.put(f"/api/schedules/{schedule_id}", json=update_payload, headers=user_headers)
    assert update_res.status_code == 200, update_res.text
    update_data = update_res.json()["data"]
    assert update_data["name"] == "已修改的定时任务"
    assert update_data["enabled"] is False
    assert update_data["next_run_at"] is None

    # 7. 局部更新启用状态（enabled 设为 True，next_run_at 恢复）
    patch_res = await test_client.patch(f"/api/schedules/{schedule_id}", json={"enabled": True}, headers=user_headers)
    assert patch_res.status_code == 200, patch_res.text
    patch_data = patch_res.json()["data"]
    assert patch_data["enabled"] is True
    assert patch_data["next_run_at"] is not None

    # 8. 手动立即触发该调度
    trigger_res = await test_client.post(f"/api/schedules/{schedule_id}/trigger", headers=user_headers)
    assert trigger_res.status_code == 200, trigger_res.text
    trigger_data = trigger_res.json()
    assert trigger_data["success"] is True
    assert "thread_id" in trigger_data["data"]
    assert "run_id" in trigger_data["data"]

    # 9. 查询执行日志列表
    logs_res = await test_client.get(f"/api/schedules/{schedule_id}/logs", headers=user_headers)
    assert logs_res.status_code == 200, logs_res.text
    logs_data = logs_res.json()["data"]
    assert len(logs_data) >= 1
    assert logs_data[0]["status"] == "triggered"

    # 10. 管理员权限越权测试：管理员（admin_headers）有权访问普通用户的定时配置
    admin_get_res = await test_client.get(f"/api/schedules/{schedule_id}", headers=admin_headers)
    assert admin_get_res.status_code == 200, admin_get_res.text
    assert admin_get_res.json()["data"]["id"] == schedule_id

    # 11. 删除调度配置
    del_res = await test_client.delete(f"/api/schedules/{schedule_id}", headers=user_headers)
    assert del_res.status_code == 200, del_res.text
    assert del_res.json()["success"] is True

    # 12. 删除后查询应返回 404
    get_after_del = await test_client.get(f"/api/schedules/{schedule_id}", headers=user_headers)
    assert get_after_del.status_code == 404
